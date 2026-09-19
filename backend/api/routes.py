"""FastAPI 路由。

API 一览：
    ── 基础 ──
    GET  /api/health                  健康检查

    ── 信息流（缓存池）──
    GET  /api/feed                    拉取 Feed（队列优先，见底补货）
    GET  /api/stats/queue             队列水位（调试/前端显示"还能刷几条"）
    POST /api/queue/refill            手动补货（按画像定向抓取）
    POST /api/queue/rebuild           重建队列（清空后按新画像重灌）

    ── 搜索 ──
    GET  /api/search                  搜索仓库（分页 / 筛选 / 排序）
    GET  /api/search/suggest          搜索联想（语言 / 标签 / topics）
    GET  /api/search/hot              热门搜索词

    ── 互动 ──
    POST /api/events                  批量上报埋点
    POST /api/repos/{id}/like         点赞
    DELETE /api/repos/{id}/like       取消点赞
    POST /api/repos/{id}/star         收藏（同步 GitHub star 意图）
    POST /api/repos/{id}/dislike      不感兴趣（梯度惩罚）
    GET  /api/repos/{id}/comments     评论列表
    POST /api/repos/{id}/comments     发表评论
    GET  /api/repos/{id}              仓库详情

    ── 用户 ──
    GET  /api/user/profile            我的兴趣画像
    POST /api/user/profile/rebuild    重建画像
    GET  /api/user/events             行为流水（调试）

    ── 翻译（OpenRouter 免费模型）──
    POST /api/repos/{id}/translate    翻译仓库简介+README 为中文（带缓存/限流）
    GET  /api/translate/status        翻译服务额度与冷却状态

    ── 调试 ──
    GET  /api/stats/pool              流量池状态
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from tags.extractor import is_noise_tag
from api.auth_service import (
    build_login_url,
    create_session,
    drop_session,
    exchange_code,
    fetch_profile,
    footprint_summary,
    resolve_session,
    sync_footprint,
    upsert_github_user,
    user_public,
)
from api.crawl_service import (
    add_keyword,
    crawl_keywords,
    list_keywords,
    remove_keyword,
)
from api.feed_service import build_feed, candidate_to_dict
from api.search_service import hot as search_hot
from api.search_service import search as search_repos
from api.search_service import suggest as search_suggest
from core.config import (
    QUEUE_TARGET_SIZE,
    COLD_START_MIN_TAGS,
    EVENT_WEIGHTS,
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MAX_LIMIT,
)
from db.connection import get_conn
from feed import queue as Q
from feed.service import get_feed as get_queue_feed
from feed.service import refill_queue
from pool.state_machine import ensure_pool, record_event

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ 模型

class EventIn(BaseModel):
    repo_id: int
    event_type: str
    dwell_ms: int = 0
    scroll_depth: float = 0.0
    source_channel: str | None = None
    batch_id: str | None = None
    rank_position: int | None = None


class IntentIn(BaseModel):
    """自然语言检索意图。"""
    user_id: int = 1
    text: str = ""
    apply_keywords: bool = True     # 是否把解析出的 keywords 写进自定义关键词（驱动爬虫）


class BriefIn(BaseModel):
    """AI 项目速读请求。"""
    user_id: int = 1
    repo_ids: list[int] = Field(default_factory=list)
    force: bool = False


class EventBatch(BaseModel):
    user_id: int = 1
    events: list[EventIn] = Field(default_factory=list)


class CommentIn(BaseModel):
    user_id: int = 1
    body: str


class DislikeIn(BaseModel):
    user_id: int = 1
    # "repo" = 只屏蔽这个仓库；"category" = 梯度惩罚标签
    reason: str = "category"


class KeywordIn(BaseModel):
    user_id: int = 1
    keyword: str


class CrawlIn(BaseModel):
    user_id: int = 1
    # 空 = 使用用户保存的自定义关键词
    keywords: list[str] = Field(default_factory=list)
    # 每个关键词抓几个候选（GitHub search per_page）
    per_keyword: int = Field(8, ge=1, le=15)
    # ⭐ 自定义搜索条件（爬虫大幅升级）
    min_stars: int = Field(20, ge=0, description="最低星数")
    max_stars: int | None = Field(None, ge=0, description="最高星数（空=不限）")
    language: str | None = Field(None, description="语言过滤（如 python / rust）")
    sort: str | None = Field(None, description="排序：stars / updated / 空=best-match")


class PatIn(BaseModel):
    """用 GitHub 个人访问令牌直接登录（不想建 OAuth App 时用）。"""
    token: str


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization") or ""
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    return None


# ------------------------------------------------------------------ 健康

@router.get("/health")
def health() -> dict[str, Any]:
    with get_conn() as conn:
        n_repos = conn.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"]
        n_users = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]
    return {"status": "ok", "repos": n_repos, "users": n_users}


# ------------------------------------------------------------------ Feed

@router.get("/feed")
def get_feed(
    background_tasks: BackgroundTasks,
    user_id: int = Query(1),
    limit: int = Query(10, ge=1, le=50),
    search: str | None = Query(None, description="搜索词（搜索干预）"),
    session_languages: str | None = Query(
        None, description="本次会话已出现的语言，逗号分隔"),
    session_owners: str | None = Query(
        None, description="本次会话已出现的作者，逗号分隔"),
    mode: str = Query("all", description="all | hot（≥1000 星热门）| gem（<1000 星遗珠）"),
) -> dict[str, Any]:
    """拉取 Feed。

    ⭐ 走缓存池：从待刷队列取，见底自动补货，绝不空屏。
       之前的实现是"每次请求实时跑完整推荐管线"，
       有队列膨胀 / 刷完就没 / 每次重算三个问题。

    ⚠️ 带 search 参数时会绕过队列，走实时搜索干预管线 ——
       用户主动搜索是一次性诉求，不该消耗队列。
       （独立的结果页搜索请用 /api/search）

    🇨🇳 中文预翻译：返回后用 BackgroundTasks 后台批量翻译本页描述，
       用户滑到卡片时译文已落库（前端再调 /api/translate/batch 秒回）。
    """
    langs = [s for s in (session_languages or "").split(",") if s]
    owners = [s for s in (session_owners or "").split(",") if s]

    with get_conn() as conn:
        if search or langs or owners:
            # 带会话上下文 / 搜索干预 → 走实时管线（需要看完整候选集）
            result = build_feed(
                conn, user_id,
                limit=limit,
                search_query=search,
                session_languages=langs,
                session_owners=owners,
            )
            items = [candidate_to_dict(c) for c in result["items"]]
        else:
            # 常规刷卡 → 缓存池
            # ⭐ 有个人模型时多取候选（limit×3）：个人分硬门槛会踢掉一部分，
            #    多取才能保证每页仍然是满的。
            try:
                from ml.user_model import model_path as _mp
                _has_model = _mp(user_id).exists()
            except Exception:
                _has_model = False
            fetch_n = min(50, limit * 3) if _has_model else limit
            payload = get_queue_feed(conn, user_id, limit=fetch_n)
            items = payload["items"]
            result = payload

        # ── 热门 / 遗珠 分档：**直接从本地池按星数档取货**，不走队列 ──
        #    ⚠️ 为什么不用队列补货：补货的 need = target - queue_size，
        #       而队列常被"全部"档填满 → need<=0 → 按档补货整段被跳过（实测空屏根因）。
        #       分档是"浏览视图"，直接从 3616 个仓库的池子里按档取，可靠且不污染主队列。
        if mode in ("hot", "gem"):
            try:
                from feed.refill import build_fetch_plan, pick_from_local_pool
                from feed.service import _score_picked
                from api.feed_service import candidate_to_dict
                plan = build_fetch_plan(conn, user_id, queue_size=0)
                picked = pick_from_local_pool(
                    conn, user_id, plan, need=max(limit * 6, 40),
                    star_min=1000 if mode == "hot" else None,
                    star_max=999 if mode == "gem" else None,
                )
                cands = _score_picked(conn, user_id, picked, plan)
                # 多取 2 倍：后面还有个人分门槛与"已收藏"排除，留出余量
                items = [candidate_to_dict(c) for c in cands[: limit * 2]]
                result = {
                    "items": items,
                    "meta": {"source": f"mode_{mode}",
                             "queue_size": Q.queue_size(conn, user_id)},
                }
            except Exception as e:  # noqa: BLE001
                logging.getLogger("recofeed").warning("分档取货失败：%s", e)

        # 后台预热本页描述的中文翻译（不阻塞响应；额度/缓存由服务内部控制）
        ids = [it["repo_id"] for it in items if it.get("repo_id") is not None]
        if ids:
            from api.translate_service import preheat_descriptions
            background_tasks.add_task(preheat_descriptions, ids)

        # ── 富化：给卡片带一段 README 摘要 ──
        # 满屏卡片如果只有两行字，版面会显得空；摘要让信息密度正常，
        # 用户也更容易判断要不要点进去（→ 点击/收藏信号更干净）。
        try:
            from api.feed_service import readme_excerpt
            if ids:
                marks = ",".join("?" * len(ids))
                ex_map: dict[int, str] = {}
                st_map: dict[int, dict] = {}
                for r in conn.execute(
                    f"""SELECT id, name, readme_md, description, forks, open_issues, size_kb,
                               created_at_gh, pushed_at_gh, has_ci, has_tests,
                               quality_score, velocity_score, freshness_score,
                               forgotten_score, homepage
                        FROM repos WHERE id IN ({marks})""",
                    tuple(ids),
                ):
                    ex = readme_excerpt(r["readme_md"])
                    # ⭐ 摘要过于贫瘠就不要摆出来（用户实测吐槽：某卡片 README 摘要
                    #    只有仓库名"biliboard-opensource"，既无信息量又像 bug）。
                    #    规则：太短（<50 字）或与仓库名/简介实质相同 → 视为无摘要。
                    if ex:
                        import re as _re
                        # ⚠️ 用 r.keys() 兜底：曾经因为 SELECT 漏了 name 导致
                        #    r["name"] 抛 sqlite3.Row 的 "No item with that key"，
                        #    整块富化被 except 吞掉 → 所有卡片 stats/摘要全空。
                        #    取不到的字段退化为空串即可，不能让一个可选字段炸掉整块逻辑。
                        _nm_raw = r["name"] if "name" in r.keys() else ""
                        _norm = lambda t: _re.sub(r"[\s\W_]+", "", (t or "").lower())
                        _ex, _nm, _ds = _norm(ex), _norm(_nm_raw), _norm(r["description"])
                        if len(ex) < 50 or _ex in (_nm, _ds) or _ex == (_nm + _ds):
                            ex = ""
                    # 概览与打分：桌面端右列用它填满版面（不依赖 README，
                    # 因为扩库进来的仓库大多只有"描述兜底"的伪 README）
                    stats = {
                        "forks": int(r["forks"] or 0),
                        "open_issues": int(r["open_issues"] or 0),
                        "size_kb": int(r["size_kb"] or 0),
                        "created_at": r["created_at_gh"],
                        "pushed_at": r["pushed_at_gh"],
                        "has_ci": bool(r["has_ci"]),
                        "has_tests": bool(r["has_tests"]),
                        "homepage": r["homepage"],
                        "quality": round(float(r["quality_score"] or 0), 2),
                        "velocity": round(float(r["velocity_score"] or 0), 2),
                        "freshness": round(float(r["freshness_score"] or 0), 2),
                        "forgotten": round(float(r["forgotten_score"] or 0), 2),
                    }
                    # ⚠️ 很多仓库的 readme_md 就是「描述兜底」（形如 "# 仓库名 + 描述"），
                    #    直接下发会让卡片上同一段字出现两遍 —— 用「包含」判断而不是前缀相等，
                    #    否则前缀会被仓库名顶掉而漏判。
                    desc = (r["description"] or "").strip()
                    if ex and desc and desc[:40].lower() in ex.lower():
                        ex = ""
                    ex_map[int(r["id"])] = ex
                    st_map[int(r["id"])] = stats
                # 已生成过 AI 速读的，顺带带上（**只读缓存**，不触发任何 LLM 调用）
                try:
                    from api.brief_service import brief_cached_only
                    for it in items:
                        b = brief_cached_only(conn, int(it.get("repo_id") or 0))
                        if b:
                            it["brief"] = b
                except Exception:  # noqa: BLE001
                    pass

                for it in items:
                    rid = int(it.get("repo_id") or 0)
                    it["readme_excerpt"] = ex_map.get(rid, "")
                    it["stats"] = st_map.get(rid)
        except Exception as e:  # noqa: BLE001
            # ⭐ 静默降级是危险的：这次就是富化整块失败却只留了一行日志，
            #    前端表现为"数据全空"，看起来像 UI 坏了。把原因透出到 meta，
            #    开发时一眼能看到（生产可忽略该字段）。
            logging.getLogger("recofeed").warning("README 摘要富化失败：%s", e)
            result.setdefault("meta", {})["enrich_error"] = f"{type(e).__name__}: {e}"

        # ⭐ 本地双塔重排：有个人模型（personal_model_u*.pth）时，
        #    用「你的模型」对这页候选重新打分排序；没有模型则原样返回。
        try:
            from ml import rerank as ml_rerank
            items, ml_info = ml_rerank.rerank(
                conn, user_id, items,
                get_repo_id=lambda it: int(it["repo_id"]),
                get_score=lambda it: float(it.get("score") or 0.0),
                min_keep=limit,
            )
            if ml_info.get("applied"):
                # 个人分透出给前端：卡片上的「匹配度」应该用它，
                # 而不是结构化质量分（否则会出现"AI 说不合口味 / 卡片却 0.57"的自相矛盾）
                sc = ml_info.get("scores") or {}
                for it in items:
                    ps = sc.get(int(it.get("repo_id") or 0))
                    if ps is not None:
                        it["personal_score"] = ps
                # 门槛踢掉一部分后补回 limit，保证每页满
                items = items[:limit]
                result.setdefault("meta", {})["ml_rerank"] = {
                    k: v for k, v in ml_info.items() if k != "scores"
                }
        except Exception as e:  # noqa: BLE001 —— 重排失败绝不能让 Feed 挂掉
            logging.getLogger("recofeed").warning("本地重排跳过：%s", e)

        if search or langs or owners:
            return {"items": items, "meta": result["meta"]}
        result["items"] = items          # 队列路径：把重排结果写回
        return result


# ------------------------------------------------------------------ 队列

@router.get("/stats/queue")
def queue_stats(user_id: int = Query(1)) -> dict[str, Any]:
    """队列水位（前端可用来显示"还剩 N 条待刷"）。"""
    with get_conn() as conn:
        stats = Q.queue_stats(conn, user_id)
        stats["recent_refills"] = Q.recent_refills(conn, user_id, limit=5)
    return stats


class RefillIn(BaseModel):
    user_id: int = 1
    target: int | None = Field(None, description="补到多少条，默认 60")


@router.post("/queue/refill")
def manual_refill(payload: RefillIn | None = None) -> dict[str, Any]:
    """手动触发补货（按用户画像定向抓取）。

    用户点"换一批"时调用。有冷却时间，短时间内重复调用会被拒绝。
    """
    uid = payload.user_id if payload else 1
    target = payload.target if payload else None
    with get_conn() as conn:
        info = refill_queue(conn, uid, trigger="manual", target=target)
        # ⚠️ refill_queue 返回的键是 queue_after，统一成 queue_size 给前端
        info["queue_size"] = Q.queue_size(conn, uid)
        info["plan_summary"] = _plan_summary(info.get("plan"))
        # 候选池不够时如实告知，避免前端以为"补满了"
        if not info.get("refilled"):
            info.setdefault("reason", "already_full")
        elif not info.get("success", True):
            info["partial"] = True
            info["note"] = (f"候选池枯竭：目标 {target or 60} 条，"
                            f"实际只补到 {info.get('enqueued', 0)} 条")
        info.pop("plan", None)
    return info


@router.post("/queue/rebuild")
def rebuild_queue(user_id: int = Query(1)) -> dict[str, Any]:
    """清空队列并按当前画像重灌。

    用于用户点了"重置推荐"、或画像大改之后。
    """
    with get_conn() as conn:
        cleared = Q.clear_queue(conn, user_id)
        info = refill_queue(conn, user_id, trigger="initial")
        info["cleared"] = cleared
        info["queue_size"] = Q.queue_size(conn, user_id)
        info["plan_summary"] = _plan_summary(info.get("plan"))
        if info.get("enqueued", 0) < 60:
            info["partial"] = True
            info["note"] = "候选池不足以灌满 60 条，已灌入全部可用仓库"
        info.pop("plan", None)
    return info


def _plan_summary(plan: dict | None) -> str:
    """把补货计划转成人话（前端提示用）。"""
    if not plan:
        return "无方向"
    q = "、".join((plan.get("queries") or [])[:4]) or "无"
    lang = "、".join(plan.get("languages") or []) or "无"
    return f"方向：{q}｜语言：{lang}"


# ------------------------------------------------------------------ 搜索

@router.get("/search")
def search(
    q: str = Query("", description="搜索词"),
    user_id: int = Query(1),
    limit: int = Query(SEARCH_DEFAULT_LIMIT, ge=1, le=SEARCH_MAX_LIMIT),
    offset: int = Query(0, ge=0, description="分页偏移"),
    language: str | None = Query(None, description="按语言过滤"),
    min_stars: int | None = Query(None, ge=0, description="最低 star 数"),
    sort: str = Query("relevance",
                      description="relevance | stars | recent | forgotten"),
) -> dict[str, Any]:
    """搜索仓库。

    ⚠️ 这是真检索，不是"给推荐结果打分"：
       全库扫描 + 按匹配度排序，与个性化无关。
       原来的 /api/feed?search= 是给召回结果加分，
       实测搜 "rust" 会返回 tailwindcss —— 因为候选池里就没有真匹配项。

    排序说明：
       relevance 相关度（默认）
       stars     按 star 数
       recent    最近更新
       forgotten 遗珠优先（⭐ 本项目特色：帮你发现被埋没的好仓库）
    """
    if sort not in ("relevance", "stars", "recent", "forgotten"):
        raise HTTPException(400, f"不支持的排序方式: {sort}")
    with get_conn() as conn:
        return search_repos(
            conn, user_id, q,
            limit=limit, offset=offset,
            language=language, min_stars=min_stars, sort=sort,
        )


@router.get("/search/suggest")
def search_suggest_api(
    prefix: str = Query("", description="已输入的前缀"),
    limit: int = Query(10, ge=1, le=30),
) -> dict[str, Any]:
    """搜索联想（搜索框实时下拉提示）。"""
    with get_conn() as conn:
        return search_suggest(conn, prefix, limit=limit)


@router.get("/search/hot")
def search_hot_api(limit: int = Query(10, ge=1, le=30)) -> dict[str, Any]:
    """热门搜索词（空搜索框时的默认展示）。"""
    with get_conn() as conn:
        return search_hot(conn, limit=limit)


# ------------------------------------------------------------------ 埋点

@router.post("/events")
def post_events(payload: EventBatch) -> dict[str, Any]:
    """批量上报行为事件。

    前端应至少上报：impression（曝光）、dwell_3s、read_readme、star_click。
    """
    accepted = 0
    # ⭐ 只有"有价值"的事件才触发画像重建：
    #    impression 只是曝光，不反映偏好；重建一次要遍历用户全部足迹，
    #    每个曝光都重建会让接口变成 O(n²)。
    profile_dirty = False

    with get_conn() as conn:
        for e in payload.events:
            weight = EVENT_WEIGHTS.get(e.event_type, 0.0)

            conn.execute(
                """INSERT INTO user_events
                   (user_id, repo_id, event_type, weight, dwell_ms,
                    scroll_depth, source_channel, rank_position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    payload.user_id, e.repo_id, e.event_type, weight,
                    e.dwell_ms, e.scroll_depth, e.source_channel,
                    e.rank_position,
                ),
            )

            # 同步到流量池统计
            if e.event_type not in ("impression",):
                record_event(conn, e.repo_id, e.event_type)

            if e.event_type in PROFILE_TRIGGER_EVENTS:
                profile_dirty = True
            accepted += 1

        if profile_dirty:
            _refresh_profile(conn, payload.user_id)

    return {"accepted": accepted, "profile_rebuilt": profile_dirty}


# 会改变用户画像的事件类型
PROFILE_TRIGGER_EVENTS = frozenset({
    "deep_read",      # 完整阅读 → 注意力圈
    "star_click",     # 收藏 → 兴趣圈
    "like", "unlike",
    "dislike",        # 负反馈 → 惩罚项
    "read_readme",
})


def _refresh_profile(conn, user_id: int) -> None:
    """重建并落库用户画像（失败不阻断主流程）。"""
    try:
        from user_profile.service import rebuild_profile
        rebuild_profile(conn, user_id)
    except Exception:
        logging.getLogger("recofeed").exception("画像重建失败 user=%s", user_id)


# ------------------------------------------------------------------ 互动

@router.post("/repos/{repo_id}/like")
def like_repo(repo_id: int, user_id: int = Query(1)) -> dict[str, Any]:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO likes (user_id, repo_id) VALUES (?, ?)",
            (user_id, repo_id),
        )
        _log_event(conn, user_id, repo_id, "like")
    return {"ok": True, "liked": True}


@router.delete("/repos/{repo_id}/like")
def unlike_repo(repo_id: int, user_id: int = Query(1)) -> dict[str, Any]:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM likes WHERE user_id = ? AND repo_id = ?",
            (user_id, repo_id),
        )
        _log_event(conn, user_id, repo_id, "unlike")
    return {"ok": True, "liked": False}


@router.post("/repos/{repo_id}/star")
def star_repo(repo_id: int, user_id: int = Query(1)) -> dict[str, Any]:
    """收藏 = 去 GitHub 点 star。

    ⭐ 这是本系统权重最高的行为（对应抖音的"收藏率 25%"）。
    """
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO stars (user_id, repo_id, gh_starred, synced_at)
               VALUES (?, ?, 1, datetime('now'))
               ON CONFLICT(user_id, repo_id)
               DO UPDATE SET gh_starred = 1, synced_at = datetime('now')""",
            (user_id, repo_id),
        )
        row = conn.execute(
            "SELECT full_name FROM repos WHERE id = ?", (repo_id,)
        ).fetchone()
        _log_event(conn, user_id, repo_id, "star_click")
        # 收藏是最强兴趣信号，立即重建画像
        _refresh_profile(conn, user_id)

    url = f"https://github.com/{row['full_name']}" if row else None
    return {"ok": True, "starred": True, "github_url": url}


@router.post("/repos/{repo_id}/dislike")
def dislike_repo(repo_id: int, payload: DislikeIn) -> dict[str, Any]:
    """不感兴趣。

    reason="repo"     → 只屏蔽这个仓库，不动标签
    reason="category" → 梯度惩罚标签（第1个-0.25，第2个-0.21... 保底0.05）
    """
    with get_conn() as conn:
        # dwell_ms 复用：-1 表示"只屏蔽仓库"
        marker = -1 if payload.reason == "repo" else 0
        conn.execute(
            """INSERT INTO user_events
               (user_id, repo_id, event_type, weight, dwell_ms)
               VALUES (?, ?, 'dislike', ?, ?)""",
            (payload.user_id, repo_id,
             -EVENT_WEIGHTS.get("skip", 2.0), marker),
        )
        affected = _penalized_tags(conn, repo_id, payload.reason) \
            if payload.reason == "category" else []
        # 负反馈改变画像的惩罚项，需要重建
        _refresh_profile(conn, payload.user_id)

    return {"ok": True, "reason": payload.reason, "penalized_tags": affected}


def _penalized_tags(conn, repo_id: int, reason: str) -> list[dict]:
    """计算被惩罚的标签（用于前端反馈展示）。"""
    row = conn.execute(
        "SELECT tags_json FROM repos WHERE id = ?", (repo_id,)
    ).fetchone()
    if not row or not row["tags_json"]:
        return []
    try:
        tags = json.loads(row["tags_json"])
    except (json.JSONDecodeError, TypeError):
        return []

    ranked = sorted(tags.items(), key=lambda kv: -float(kv[1]))[:5]
    out = []
    for rank, (tag, _) in enumerate(ranked):
        amount = max(0.05, 0.25 - 0.04 * rank)
        out.append({"tag": tag, "penalty": round(amount, 3)})
    return out


# ------------------------------------------------------------------ 评论

@router.get("/repos/{repo_id}/comments")
def list_comments(repo_id: int, limit: int = Query(50, ge=1, le=200)
                  ) -> dict[str, Any]:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT c.id, c.user_id, c.body, c.body_len, c.created_at,
                      u.username
               FROM comments c
               LEFT JOIN users u ON u.id = c.user_id
               WHERE c.repo_id = ?
               ORDER BY c.created_at DESC LIMIT ?""",
            (repo_id, limit),
        ).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.post("/repos/{repo_id}/comments")
def post_comment(repo_id: int, payload: CommentIn) -> dict[str, Any]:
    body = (payload.body or "").strip()
    if not body:
        raise HTTPException(status_code=400, detail="评论不能为空")

    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO comments (repo_id, user_id, body, body_len)
               VALUES (?, ?, ?, ?)""",
            (repo_id, payload.user_id, body, len(body)),
        )
        _log_event(conn, payload.user_id, repo_id, "comment")
        cid = cur.lastrowid
    # 评论深度（2026 算法看重）——15 字以上算优质评论
    return {"ok": True, "id": cid, "depth": "quality" if len(body) >= 15
            else "normal"}


# ------------------------------------------------------------------ 详情

@router.get("/repos/{repo_id}")
def repo_detail(
    repo_id: int,
    user_id: int = Query(1),
    with_readme: bool = Query(False, description="是否下发完整 README 正文"),
) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT r.*, p.current_pool, p.impressions, p.deep_rate,
                      p.star_rate, p.ctr
               FROM repos r
               LEFT JOIN repo_pools p ON p.repo_id = r.id
               WHERE r.id = ?""",
            (repo_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="仓库不存在")

        liked = conn.execute(
            "SELECT 1 FROM likes WHERE user_id = ? AND repo_id = ?",
            (user_id, repo_id),
        ).fetchone() is not None

        starred = conn.execute(
            "SELECT 1 FROM stars WHERE user_id = ? AND repo_id = ?",
            (user_id, repo_id),
        ).fetchone() is not None

    d = dict(row)
    # ⚠️ 默认不下发 README（正文可达数十 KB，列表页用不到）。
    #    详情页需要渲染 README 时显式传 with_readme=true。
    if not with_readme:
        d.pop("readme_md", None)
    d["liked"] = liked
    d["starred"] = starred
    d["url"] = f"https://github.com/{row['full_name']}"
    return d


# ------------------------------------------------------------------ 画像

@router.get("/user/profile")
def user_profile(user_id: int = Query(1)) -> dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        n_events = conn.execute(
            "SELECT COUNT(*) AS n FROM user_events WHERE user_id = ?",
            (user_id,),
        ).fetchone()["n"]
        n_seen = conn.execute(
            "SELECT COUNT(DISTINCT repo_id) AS n FROM feed_impressions "
            "WHERE user_id = ?",
            (user_id,),
        ).fetchone()["n"]

    if not row:
        return {"user_id": user_id, "interests": [], "events": n_events,
                "seen": n_seen, "cold_start": True}

    def _parse(v):
        try:
            return json.loads(v) if v else []
        except (json.JSONDecodeError, TypeError):
            return []

    interests = _parse(row["top_topics"])

    # ── 画像展示的收口规则 ──
    # 一次展示 50 个标签 = 垃圾堆：长尾全是 0.1x 的描述性词与项目名
    # （公式 / 热门 / 监控 / cordis / design-tokens…），真正有指向的只有前 10 个。
    # 规则：剔除噪声词 + 只保留「相对权重 ≥ 25% 榜首」的标签 + 最多 24 个。
    # 用相对权重而不是绝对阈值：不同用户的画像量纲不同，相对值能自适应。
    try:
        clean = [it for it in interests
                 if float(it.get("weight", 0)) > 0
                 and not is_noise_tag(str(it.get("tag", "")))]
        top_w = max((float(it["weight"]) for it in clean), default=0.0)
        if top_w > 0:
            clean = [it for it in clean if float(it["weight"]) >= 0.25 * top_w]
        interests = clean[:24]

        # ⭐ 兴趣方向（细粒度推荐池）：把散标签聚成方向 ——
        #    用户的第一印象应该是"我在语音合成/Agent 方向"，而不是"我喜欢 ai"。
        #    这也是评测说的"建立更细维度的推荐池"的第一步（方向 → 成员标签 → 补货查询）。
        try:
            from quality.domains import domain_scores
            _dw = {it["tag"]: float(it["weight"]) for it in interests}
            domains = domain_scores(_dw)
        except Exception:  # noqa: BLE001
            domains = []
    except Exception:
        pass
    languages = _parse(row["top_languages"])

    return {
        "domains": domains if True else [],
        "interests": interests,
        "languages": languages,
        "events": n_events,
        "seen": n_seen,
        "cold_start": len(interests) + len(languages) < 10,
        "updated_at": row["updated_at"],
    }


@router.post("/user/profile/rebuild")
def rebuild_user_profile(user_id: int = Query(1)) -> dict[str, Any]:
    """强制重建用户画像（从三层足迹重新聚合）。

    正常使用中画像会在 star / 深读 / 负反馈时自动重建，
    这个接口用于调试，或在批量导入用户足迹后手动触发。
    """
    from user_profile.service import rebuild_profile

    with get_conn() as conn:
        tv = rebuild_profile(conn, user_id)
        top = [{"tag": k, "weight": round(v, 4)} for k, v in tv.top(20)]
        sources: dict[str, int] = {}
        for tag, src in tv.sources.items():
            for name in src:
                sources[name] = sources.get(name, 0) + 1

    return {
        "ok": True,
        "user_id": user_id,
        "tags": len(tv.weights),
        "top": top,
        "by_source": sources,
        "cold_start": tv.size < COLD_START_MIN_TAGS,
    }


@router.post("/user/intent")
def post_user_intent(payload: IntentIn) -> dict[str, Any]:
    """⭐ AI 意图解析：一句大白话 → 结构化检索意图（keywords / tags / exclude）。

    把门槛从"懂技术标签"降到"会打字"：
      用户写「我想找能在本地跑、支持声音克隆、最好带界面的 TTS 项目」，
      解析出 {"keywords":["tts","voice-cloning","local-inference"],
              "tags":["webui"], "exclude":["api-wrapper"]}，
      并把 keywords 写进自定义关键词（爬虫随即按它去 GitHub 抓）。
    """
    from api.intent_service import parse_intent, save_intent

    with get_conn() as conn:
        out = parse_intent(conn, payload.user_id, payload.text,
                           apply_keywords=payload.apply_keywords)
        if out.get("ok"):
            save_intent(conn, payload.user_id, payload.text,
                        out["intent"], out.get("model"))
    return out


@router.get("/user/report")
def get_user_report(
    user_id: int = Query(1),
    days: int = Query(7, ge=1, le=90),
    force: bool = Query(False, description="忽略缓存重新生成（仍消耗 1 次 LLM 调用）"),
) -> dict[str, Any]:
    """⭐ AI 周报：本周刷了什么 / 挖到几个宝藏 / 下一步学什么。

    事实（曝光、深读、收藏、项目清单）由 SQL 算好再交给 LLM ——
    LLM 只负责**把事实写成话**，不允许编造项目。
    """
    from api.report_service import generate_report

    with get_conn() as conn:
        return generate_report(conn, user_id, days=days, force=force)


@router.get("/user/keywords")
def get_custom_keywords(user_id: int = Query(1)) -> dict[str, Any]:
    """我的自定义兴趣关键词（画像面板管理，可驱动爬虫抓取）。"""
    with get_conn() as conn:
        kws = list_keywords(conn, user_id)
    return {"keywords": kws}


@router.post("/user/keywords")
def post_custom_keyword(payload: KeywordIn) -> dict[str, Any]:
    """添加一个自定义关键词（自动小写去重，上限 12 个）。"""
    try:
        with get_conn() as conn:
            item = add_keyword(conn, payload.user_id, payload.keyword)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return item


@router.delete("/user/keywords/{kid}")
def delete_custom_keyword(kid: int, user_id: int = Query(1)) -> dict[str, Any]:
    with get_conn() as conn:
        ok = remove_keyword(conn, user_id, kid)
    if not ok:
        raise HTTPException(status_code=404, detail="关键词不存在")
    return {"ok": True}


@router.post("/queue/crawl")
def crawl_by_keywords(
    background_tasks: BackgroundTasks,
    payload: CrawlIn | None = None,
) -> dict[str, Any]:
    """⭐ 用户自定义关键词爬虫：Scrapling 抓 GitHub 真实仓库 → 入库 → 直接入队。

    keywords 为空时使用画像面板里保存的自定义关键词。
    README/标签由后台任务异步补齐，本接口秒回。
    """
    p = payload or CrawlIn()
    try:
        with get_conn() as conn:
            info = crawl_keywords(
                conn,
                p.user_id,
                keywords=p.keywords or None,
                per_keyword=p.per_keyword,
                background_enrich=background_tasks,
                min_stars=p.min_stars,
                max_stars=p.max_stars,
                language=p.language,
                sort=p.sort,
            )
    except ValueError as e:
        raise HTTPException(status_code=429 if "频繁" in str(e) else 400,
                            detail=str(e))
    return info


@router.get("/auth/status")
def auth_status(request: Request) -> dict[str, Any]:
    """认证能力状态：OAuth 是否已配置 + 回调地址（配置 GitHub App 时要用）。"""
    from core.config import AUTH_CONFIGURED, GITHUB_OAUTH_REDIRECT_URI
    return {
        "configured": AUTH_CONFIGURED,
        "callback_url": GITHUB_OAUTH_REDIRECT_URI,
        "login_url": "/api/auth/github/login",
    }


@router.get("/auth/github/login")
def auth_github_login(
    request: Request,
    origin: str | None = Query(
        None, description="发起登录的前端地址（如 http://localhost:5273），"
                           "用于端口无关的回跳；缺省时用配置默认值"),
) -> Any:
    """跳转到 GitHub 授权页（注册/登录二合一：GitHub 侧新用户自动带过来）。"""
    import secrets as _secrets

    from core.config import AUTH_CONFIGURED, GITHUB_OAUTH_REDIRECT_URI
    from api.auth_service import build_login_url, remember_origin
    if not AUTH_CONFIGURED:
        raise HTTPException(
            status_code=400,
            detail="还没配置 GitHub OAuth。两种方式：① 在 GitHub 建 OAuth App 并把 "
                   "client_id/secret 填进 backend/core/auth_local.json；"
                   "② 改用「令牌登录」POST /api/auth/github/pat（无需建 App）。",
        )
    # ⭐ 把前端来源绑定到 state：dev server 端口可变（5173 被系统保留时可换 5273），
    #    回跳地址必须跟着变，否则登录回来 ERR_CONNECTION_REFUSED。
    state = _secrets.token_urlsafe(16)
    remember_origin(state, origin)
    return RedirectResponse(build_login_url(GITHUB_OAUTH_REDIRECT_URI, state=state))


@router.get("/auth/github/callback")
def auth_github_callback(
    code: str = Query(...),
    state: str | None = Query(None),
) -> Any:
    """GitHub 回调：换 token → 建/取用户 → 发会话 → 跳回前端。"""
    from core.config import AUTH_FRONTEND_REDIRECT
    from api.auth_service import pop_origin

    # 优先跳回"发起登录的那个前端地址"（端口无关），否则用配置默认值
    _front = pop_origin(state) or AUTH_FRONTEND_REDIRECT

    def _back(qs: str) -> RedirectResponse:
        return RedirectResponse(f"{_front}/?{qs}")

    token = exchange_code(code)
    if not token:
        return _back("auth_error=exchange_failed")
    gh = fetch_profile(token)
    if not gh:
        return _back("auth_error=profile_failed")
    try:
        with get_conn() as conn:
            uid = upsert_github_user(conn, gh, token)
            sess = create_session(conn, uid)
    except Exception as e:  # noqa: BLE001
        return _back(f"auth_error={type(e).__name__}")
    return _back(f"auth_token={sess}&auth_sync=1")


@router.post("/auth/github/pat")
def auth_github_pat(payload: PatIn) -> dict[str, Any]:
    """用个人访问令牌（PAT）登录 —— 不用建 OAuth App，本地开发最省事。

    PAT 生成：GitHub → Settings → Developer settings → Personal access tokens，
    勾选 read:user（读公开资料）+ public_repo（读你点星的仓库）。
    """
    gh = fetch_profile(payload.token.strip())
    if not gh:
        raise HTTPException(status_code=401, detail="令牌无效或没有 read:user 权限")
    with get_conn() as conn:
        uid = upsert_github_user(conn, gh, payload.token.strip())
        sess = create_session(conn, uid)
        row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        user = user_public(row)
    return {"token": sess, "user": user}


@router.get("/auth/me")
def auth_me(request: Request) -> dict[str, Any]:
    """当前登录用户 + GitHub 实证圈概况。"""
    tok = _bearer(request)
    with get_conn() as conn:
        row = resolve_session(conn, tok)
        if not row:
            raise HTTPException(status_code=401, detail="未登录或会话已过期")
        out = user_public(row)
        out["footprint"] = footprint_summary(conn, int(row["id"]))
    return out


@router.post("/auth/sync")
def auth_sync(request: Request) -> dict[str, Any]:
    """⭐ 拉取用户自建仓库 + 点星仓库（权重按规则计算）→ 重建画像。

    这一步是"用户实操证据 → 强推荐信号"的入口：
      自建仓库（本周更新 0.5 ~ 陈旧 0.03）> 点星仓库（第 1 名 0.4 ~ 长尾 0.05）
    """
    tok = _bearer(request)
    with get_conn() as conn:
        row = resolve_session(conn, tok)
        if not row:
            raise HTTPException(status_code=401, detail="未登录或会话已过期")
        gh_token = row["github_token"]
        if not gh_token:
            raise HTTPException(status_code=400, detail="该账号没有 GitHub 令牌，请重新登录")
        info = sync_footprint(conn, int(row["id"]), gh_token)
        info["footprint"] = footprint_summary(conn, int(row["id"]))
    return info


@router.post("/auth/logout")
def auth_logout(request: Request) -> dict[str, Any]:
    tok = _bearer(request)
    if tok:
        with get_conn() as conn:
            drop_session(conn, tok)
    return {"ok": True}


@router.get("/ml/status")
def ml_status(user_id: int = Query(1)) -> dict[str, Any]:
    """本地双塔模型状态：向量库 / 个人模型 / 证据量 / 后台任务进度。"""
    from ml import state as ml_state
    from ml import user_model
    with get_conn() as conn:
        out = user_model.status(conn, user_id)
    out["tasks"] = ml_state.snapshot()
    return out


@router.post("/ml/embed")
def ml_embed(
    background_tasks: BackgroundTasks,
    force: bool = Query(False, description="是否重建全部向量"),
    limit: int | None = Query(None, description="条数上限（调试）"),
) -> dict[str, Any]:
    """物品塔：本地 embedding 向量化（后台跑，进度看 /api/ml/status）。

    ⚠️ 首次运行会自动从魔搭下载模型（~100MB），可能耗时几分钟。
    """
    from ml import state as ml_state

    def _job() -> None:
        from ml import embedder
        ml_state.set_embed(running=True, done=0, total=0, error=None)
        try:
            with get_conn() as conn:
                def prog(done: int, total: int) -> None:
                    ml_state.set_embed(done=done, total=total)
                stats = embedder.build_all(conn, force=force, limit=limit, progress=prog)
            ml_state.set_embed(running=False, done=stats["embedded"], total=stats["total"],
                              finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
        except Exception as e:  # noqa: BLE001
            ml_state.set_embed(running=False, error=str(e))

    background_tasks.add_task(_job)
    return {"started": True, "hint": "进度查询 GET /api/ml/status"}


@router.post("/ml/train")
def ml_train(user_id: int = Query(1),
             epochs: int = Query(100, ge=10, le=1000)) -> dict[str, Any]:
    """用户塔 + 本地微调：训练个人模型（CPU 通常几秒），返回体检指标。"""
    from ml import user_model
    with get_conn() as conn:
        res = user_model.train(conn, user_id, epochs=epochs)
    if not res.get("ok"):
        raise HTTPException(status_code=400, detail=res.get("reason", "训练失败"))
    return res


@router.get("/ml/recall")
def ml_recall(user_id: int = Query(1),
              k: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    """向量召回演示：用用户向量在全部仓库里找最相近的 k 个（含个人模型打分）。"""
    from ml import rerank, user_model
    with get_conn() as conn:
        cands = rerank.recall_by_embedding(conn, user_id, k=k)
        if not cands:
            return {"items": [], "note": "没有用户向量（先登录同步 / 刷几条，或先向量化）"}
        ids = [rid for rid, _ in cands]
        personal = user_model.score_repos(conn, user_id, ids)
        items = []
        for rid, cos in cands:
            r = conn.execute(
                "SELECT full_name, stars FROM repos WHERE id = ?", (rid,)
            ).fetchone()
            if r:
                items.append({"repo_id": rid, "full_name": r["full_name"],
                              "stars": r["stars"], "cosine": round(cos, 3),
                              "personal_score": round(personal.get(rid, -1), 3)})
    return {"items": items}


class ExlainIn(BaseModel):
    """批量请求推荐理由（一页卡片合并 1 次 LLM 调用）。"""
    user_id: int = 1
    repo_ids: list[int] = Field(default_factory=list)


@router.get("/user/profile/summary")
def user_profile_summary(user_id: int = Query(1)) -> dict[str, Any]:
    """读取 LLM 画像归纳（只读缓存；没有或画像已变则返回 null）。"""
    from api.insight_service import get_cached_insight
    with get_conn() as conn:
        return {"insight": get_cached_insight(conn, user_id)}


@router.post("/user/profile/summarize")
def user_profile_summarize(user_id: int = Query(1),
                           force: bool = Query(False)) -> dict[str, Any]:
    """⭐ 让 LLM 归纳你的画像（人话版 + 精确技术方向 + 噪声剔除）。

    成本：1 次 LLM 调用；按画像指纹缓存，画像没变就直接返回旧结果。
    """
    from api.insight_service import summarize_profile
    with get_conn() as conn:
        res = summarize_profile(conn, user_id, force=force)
    if not res.get("ok"):
        raise HTTPException(status_code=503, detail=res.get("reason", "LLM 不可用"))
    return res


@router.post("/ml/brief")
def ml_brief(payload: BriefIn) -> dict[str, Any]:
    """⭐ AI 项目速读：把 README 变成「一句话定位 / 技术栈 / 核心亮点 / 适合谁 / 成熟度」。

    按需调用（前端点按钮才跑）+ 落库缓存（README 变了才重新生成）——
    不随 Feed 自动调用，避免把额度烧在用户根本不看的仓库上。
    """
    from api.brief_service import make_brief

    ids = [int(i) for i in payload.repo_ids][:3]      # 单次最多 3 个：README 很长，别混上下文
    if not ids:
        return {"ok": False, "reason": "缺少 repo_ids", "briefs": {}}

    out: dict[str, Any] = {}
    with get_conn() as conn:
        for rid in ids:
            out[str(rid)] = make_brief(conn, rid, force=payload.force)
    return {"ok": True, "briefs": out}


@router.post("/ml/explain")
def ml_explain(payload: ExlainIn) -> dict[str, Any]:
    """⭐ LLM 推荐理由：给一批卡片各生成一句话「为什么推荐给你」。

    一页 10 张合并成 1 次调用，结果按 (用户, 卡片, 画像指纹) 缓存。
    """
    from api.insight_service import explain_cards
    with get_conn() as conn:
        return explain_cards(conn, payload.user_id, payload.repo_ids)


@router.get("/user/events")
def user_events(
    user_id: int = Query(1),
    limit: int = Query(50, ge=1, le=500),
) -> dict[str, Any]:
    """用户行为流水（调试用，便于前端核对埋点是否正确）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT e.event_type, e.weight, e.dwell_ms, e.scroll_depth,
                      e.source_channel, e.rank_position, e.created_at,
                      r.full_name
               FROM user_events e
               LEFT JOIN repos r ON r.id = e.repo_id
               WHERE e.user_id = ?
               ORDER BY e.created_at DESC, e.id DESC
               LIMIT ?""",
            (user_id, limit),
        ).fetchall()
        summary = conn.execute(
            """SELECT event_type, COUNT(*) AS n
               FROM user_events WHERE user_id = ?
               GROUP BY event_type ORDER BY n DESC""",
            (user_id,),
        ).fetchall()

    return {
        "items": [dict(r) for r in rows],
        "by_type": {r["event_type"]: r["n"] for r in summary},
    }


# ------------------------------------------------------------------ 翻译

@router.post("/repos/{repo_id}/translate")
def translate_repo_api(repo_id: int) -> dict[str, Any]:
    """把仓库的 description / README 翻译成中文。

    防护：本地限流（12 次/分钟、45 次/天）+ 429 冷却 + SQLite 缓存 +
    并发合并 + 三模型级联降级。详见 api/translate_service.py。
    """
    from api.translate_service import translate_repo
    with get_conn() as conn:
        return translate_repo(conn, repo_id)


@router.get("/translate/status")
def translate_status() -> dict[str, Any]:
    """翻译服务额度与冷却状态（调试 / 前端提示）。"""
    from api.translate_service import rate_status
    return rate_status()


class BatchTranslateIn(BaseModel):
    repo_ids: list[int] = Field(default_factory=list, description="本页 feed 的仓库 id 列表")


@router.post("/translate/batch")
def translate_batch(payload: BatchTranslateIn) -> dict[str, Any]:
    """批量拉取 feed 卡片描述的中文译文。

    缓存命中秒回；未命中的整批合并成 1 次 LLM 调用（只计 1 次额度）。
    限流时不抛错，缺失条目为 null，前端回退显示英文原文。
    """
    from api.translate_service import translate_descriptions
    with get_conn() as conn:
        m = translate_descriptions(conn, payload.repo_ids)
    return {
        "translations": {str(k): v for k, v in m.items()},
    }


# ------------------------------------------------------------------ 调试

@router.get("/stats/pool")
def pool_stats() -> dict[str, Any]:
    """流量池分布（调试用）。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT current_pool, COUNT(*) AS n
               FROM repo_pools GROUP BY current_pool
               ORDER BY current_pool"""
        ).fetchall()
    names = {-1: "eliminated", 0: "cold", 1: "basic", 2: "mid",
             3: "high", 4: "viral"}
    return {
        "distribution": [
            {"level": r["current_pool"],
             "name": names.get(r["current_pool"], "unknown"),
             "count": r["n"]}
            for r in rows
        ]
    }


# ------------------------------------------------------------------ 内部

def _log_event(conn, user_id: int, repo_id: int, event_type: str) -> None:
    weight = EVENT_WEIGHTS.get(event_type, 0.0)
    conn.execute(
        """INSERT INTO user_events (user_id, repo_id, event_type, weight)
           VALUES (?, ?, ?, ?)""",
        (user_id, repo_id, event_type, weight),
    )
    record_event(conn, repo_id, event_type)
    ensure_pool(conn, repo_id)
