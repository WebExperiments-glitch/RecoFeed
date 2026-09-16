"""Feed 编排服务 —— 完整推荐流程。

流程：
    冷启动判断 → 多路召回 → 精排打分 → 搜索干预 → 重排打散 → 记录曝光

⚠️ 最后一步"记录曝光"绝不能省：
   没有曝光作分母，所有率值（ctr / deep_rate / star_rate）都算不出来，
   整个流量池赛马机制就是空的。
"""
from __future__ import annotations

import json
import random
import re
import sqlite3
import uuid

from core.config import (
    COLD_START_MIN_TAGS,
    COLD_START_MIX_UNTIL,
    COLD_START_PURE_TRENDING,
    COLD_START_TRENDING_RATIO,
    RANK_WEIGHTS,
)
from tags.extractor import display_tags
from user_profile.builder import TagVector
from rank.rerank import (
    SearchIntervention,
    apply_search_intervention,
    mix_search_and_longterm,
    rerank_disperse,
)
from rank.scorer import Candidate, score_candidate
from recall.channels import ALL_CHANNELS, recall_all


# ------------------------------------------------------------------ 用户状态

def load_profile(conn: sqlite3.Connection, user_id: int,
                 *, auto_rebuild: bool = True) -> TagVector:
    """从数据库加载用户画像。

    存储格式：user_profiles.top_topics = [{"tag": "llm", "weight": 0.82}, ...]
    （由 profile.service.save_profile 写入）

    ⚠️ auto_rebuild：画像为空时自动从用户足迹重建。
       此前这个模块只读不写，user_profiles 永远空着，
       导致兴趣召回通道从未真正生效过。
       这里做一次惰性重建兜底，让新用户/冷启动用户也能获得画像。
    """
    if auto_rebuild and not _profile_row_exists(conn, user_id):
        try:
            from user_profile.service import rebuild_profile
            rebuild_profile(conn, user_id)
        except Exception:
            # 画像重建失败不应阻断 Feed，退化为空画像（冷启动路径）
            pass

    row = conn.execute(
        "SELECT top_topics, top_languages FROM user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()

    tv = TagVector()
    if not row:
        return tv

    for field in ("top_topics", "top_languages"):
        raw = row[field]
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue

        # 支持两种格式：
        #   1. {"llm": 0.82, ...}                      （旧格式）
        #   2. [{"tag": "llm", "weight": 0.82}, ...]   （当前格式）
        if isinstance(data, dict):
            for k, v in data.items():
                try:
                    tv.weights[str(k).lower()] = float(v)
                except (TypeError, ValueError):
                    continue
        elif isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                key = item.get("tag") or item.get("lang")
                if not key:
                    continue
                try:
                    val = float(item.get("weight", item.get("score", 1.0)))
                except (TypeError, ValueError):
                    continue
                tv.weights[str(key).lower()] = val
    return tv


def _profile_row_exists(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute(
        "SELECT interest_count FROM user_profiles WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return bool(row and (row["interest_count"] or 0) > 0)


def load_penalties(conn: sqlite3.Connection, user_id: int) -> dict[str, float]:
    """加载负反馈惩罚（从 user_events 的 dislike 事件聚合）。"""
    rows = conn.execute(
        """SELECT repo_id, dwell_ms FROM user_events
           WHERE user_id = ? AND event_type = 'dislike'
           ORDER BY created_at DESC LIMIT 200""",
        (user_id,),
    ).fetchall()

    penalties: dict[str, float] = {}
    for r in rows:
        repo = conn.execute(
            "SELECT tags_json FROM repos WHERE id = ?", (r["repo_id"],)
        ).fetchone()
        if not repo or not repo["tags_json"]:
            continue
        try:
            tags = json.loads(repo["tags_json"])
        except (json.JSONDecodeError, TypeError):
            continue

        # dwell_ms 复用为"惩罚等级"约定：0=gradient, -1=block repo only
        if r["dwell_ms"] == -1:
            continue      # 只屏蔽仓库，不惩罚标签

        ranked = sorted(tags.items(), key=lambda kv: -float(kv[1]))[:5]
        for rank, (tag, _) in enumerate(ranked):
            amount = max(0.05, 0.25 - 0.04 * rank)   # 线性 + 保底
            penalties[tag.lower()] = (
                penalties.get(tag.lower(), 0.0) + amount
            )
    return penalties


def is_cold_start(profile: TagVector) -> bool:
    return profile.size < COLD_START_MIN_TAGS


def get_user_feed_count(conn: sqlite3.Connection, user_id: int) -> int:
    """该用户已看过多少条（用于冷启动分阶段）。"""
    row = conn.execute(
        "SELECT COUNT(DISTINCT repo_id) AS n FROM feed_impressions "
        "WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row["n"] if row else 0


# ------------------------------------------------------------------ 冷启动

def cold_start_pick(conn: sqlite3.Connection, user_id: int,
                    seen_count: int, limit: int,
                    rand: random.Random | None = None) -> list[Candidate]:
    """冷启动三阶段。

    阶段1（第 1~5 个）：100% Trending —— 收集初始信号
    阶段2（第 6~20 个）：70% Trending + 30% 冷门池
    阶段3（第 21+ 个）：交由正常算法
    """
    rng = rand or random.Random()

    if seen_count < COLD_START_PURE_TRENDING:
        return ALL_CHANNELS["trending"](conn, user_id, limit=limit)

    if seen_count < COLD_START_MIX_UNTIL:
        n_trend = max(1, int(limit * COLD_START_TRENDING_RATIO))
        n_cold = limit - n_trend
        trend = ALL_CHANNELS["trending"](conn, user_id, limit=n_trend)
        forgotten = ALL_CHANNELS["forgotten"](conn, user_id, limit=n_cold)
        mixed = trend + forgotten
        rng.shuffle(mixed)
        return mixed[:limit]

    return []


# ------------------------------------------------------------------ 主流程

def build_feed(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    limit: int = 10,
    search_query: str | None = None,
    session_languages: list[str] | None = None,
    session_owners: list[str] | None = None,
    rand: random.Random | None = None,
) -> dict:
    """构建一次 Feed。返回 {items, meta}。"""
    rng = rand or random.Random()
    batch_id = uuid.uuid4().hex[:12]
    session_languages = list(session_languages or [])
    session_owners = list(session_owners or [])

    # ── 加载用户状态 ──
    profile = load_profile(conn, user_id)
    penalties = load_penalties(conn, user_id)
    seen_count = get_user_feed_count(conn, user_id)
    cold = is_cold_start(profile)

    # ── 冷启动分支 ──
    if cold and seen_count < COLD_START_MIX_UNTIL:
        candidates = cold_start_pick(conn, user_id, seen_count,
                                     limit=limit * 3, rand=rng)
        stage = ("pure_trending" if seen_count < COLD_START_PURE_TRENDING
                 else "mixed")
        if not candidates:
            candidates = recall_all(conn, profile, user_id, rand=rng)
    else:
        candidates = recall_all(conn, profile, user_id, rand=rng)
        stage = "algorithm"

    if not candidates:
        return {"items": [], "meta": {"reason": "no_candidates",
                                      "stage": stage, "batch_id": batch_id}}

    # ── 精排打分 ──
    for c in candidates:
        score_candidate(
            c, profile,
            penalties=penalties,
            session_languages=session_languages,
            session_owners=session_owners,
            weights=RANK_WEIGHTS,
        )

    # ── 搜索干预 ──
    si: SearchIntervention | None = None
    if search_query:
        from tags.extractor import display_tags, extract_query_tags
        qtags = extract_query_tags(search_query)
        if qtags:
            si = SearchIntervention(tags=qtags)
            apply_search_intervention(candidates, si)

    # ── 排序 + 混合 ──
    ranked = sorted(candidates, key=lambda c: -c.score)

    if si and si.tags:
        search_related = [c for c in ranked
                          if "search" in c.source_channels]
        long_term = [c for c in ranked
                     if "search" not in c.source_channels]
        ordered = mix_search_and_longterm(long_term, search_related,
                                          total=limit * 2)
    else:
        ordered = ranked[:max(limit * 3, 30)]

    # ── 重排打散 ──
    final = rerank_disperse(ordered, total=limit,
                            explore_rate=RANK_WEIGHTS["explore_rate"],
                            rand=rng)

    # ── 记录曝光（绝不能省）──
    log_impressions(conn, user_id, final, batch_id)

    # ── 搜索衰减 ──
    if si:
        si.on_refresh()

    return {
        "items": final,
        "meta": {
            "stage": stage,
            "cold_start": cold,
            "seen_count": seen_count,
            "candidates": len(candidates),
            "batch_id": batch_id,
            "search_weight": round(si.current_weight, 4) if si else 0.0,
            "search_refresh": si.refresh_count if si else 0,
        },
    }


def log_impressions(conn: sqlite3.Connection, user_id: int,
                    items: list[Candidate], batch_id: str) -> None:
    """写入曝光记录。

    ⚠️ 这是整个算法闭环的基础：
       没有曝光作分母，所有率值都算不出来。
    """
    for rank_pos, c in enumerate(items):
        conn.execute(
            """INSERT INTO feed_impressions
               (user_id, repo_id, rank_position, final_score, raw_score,
                channel, batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id, c.repo_id, rank_pos,
                c.score, c.score_detail.get("final", c.score),
                ",".join(c.source_channels) or c.channel,
                batch_id,
            ),
        )
        # 同步累加到流量池
        from pool.state_machine import record_impression
        record_impression(conn, c.repo_id)


def candidate_to_dict(c: Candidate) -> dict:
    """序列化为 API 输出。"""
    return {
        "repo_id": c.repo_id,
        "full_name": c.full_name,
        "owner": c.owner,
        "name": c.name,
        "description": c.description,
        "language": c.language,
        "stars": c.stars,
        "topics": c.topics[:8],
        # 同上：展示层也要过滤（画像层过滤 ≠ 卡片干净）
        "tags": [{"tag": k, "weight": round(v, 3)}
                 for k, v in display_tags(
                     c.tags, name=c.name, owner=c.owner,
                     topics=c.topics or [], limit=8)],
        "score": round(c.score, 4),
        "score_detail": c.score_detail,
        "channels": c.source_channels,
        "url": f"https://github.com/{c.full_name}",
        # 用于前端展示"为什么推荐给你"
        "reason": explain(c),
    }


def explain(c: Candidate) -> str:
    """生成推荐理由（对应抖音/快手生成式推荐的"推荐解释"能力）。"""
    parts: list[str] = []

    if "forgotten" in c.source_channels:
        parts.append("优质但少有人发现")
    if "trending" in c.source_channels:
        parts.append("近期 star 增长很快")
    if "fresh" in c.source_channels:
        parts.append("刚发布不久的新项目")
    if "similar" in c.source_channels:
        parts.append("与你读过的项目相似")
    if "following" in c.source_channels:
        parts.append("来自你关注的作者")
    if "search" in c.source_channels:
        parts.append("匹配你的搜索")
    if "interest" in c.source_channels:
        parts.append("符合你的技术兴趣")
    if "explore" in c.source_channels:
        parts.append("随机探索，帮你发现新领域")

    if c.score_detail.get("affinity", 0) > 0.3 and "interest" not in parts:
        parts.append("与你的兴趣高度重合")

    return "；".join(parts[:2]) if parts else "为你推荐"


# ---------------------------------------------------------------- README 摘要

_FENCE = re.compile(r"```[\s\S]*?```", re.MULTILINE)      # 代码块（整体删掉）
_IMG = re.compile(r"!\[[^\]]*\]\([^)]*\)")              # 图片
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")            # 链接 → 保留文字
_HTML = re.compile(r"<[^>]{1,120}>")                       # 行内 HTML
_URL = re.compile(r"https?://\S+")                         # 裸 URL
_LINE_MARK = re.compile(r"^\s*(?:[#>\-\*+=|~]{1,}|\d+\.)\s*", re.MULTILINE)
_SECTION_TITLE = re.compile(
    r"^(?:install(?:ation)?|usage|getting started|quick ?start|license|contribut\w*|"
    r"features?|requirements?|dependencies|screenshot\w*|demo|faq|changelog)\s*[:：]?\s*",
    re.IGNORECASE)
_BADGE_ONLY = re.compile(r"^[\s\W_]*$")


def readme_excerpt(md: str | None, limit: int = 420) -> str:
    """把 README 压成一段纯文本摘要（给 Feed 卡片用）。

    为什么要它：满屏卡片如果只有两行字，版面会显得空；
    带上 README 摘要后信息密度正常，用户也更容易判断要不要点进去。
    """
    if not md:
        return ""
    t = _FENCE.sub(" ", md)        # ⚠️ 必须先删代码块：
    t = _IMG.sub(" ", t)           #    否则行首标记规则会把 ``` 吃掉、留下代码内容
    t = _LINK.sub(r"\1", t)   # 保留链接文字，丢掉 URL
    t = _HTML.sub(" ", t)
    t = _URL.sub(" ", t)
    t = _LINE_MARK.sub("", t)
    t = re.sub(r"[*_`~]{1,}", "", t)          # 残留的行内强调符号
    # 链接/图片被删掉后会留下 ", , , and ." 这类空标点残渣，收拾干净
    t = re.sub(r"\s*,\s*(?=[,.;:)])", "", t)
    t = re.sub(r"(?:\s*,\s*){2,}", ", ", t)
    t = re.sub(r"\s+([,.;:])", r"", t)
    t = re.sub(r"\s+", " ", t).strip()
    # 去掉开头的空段落 / 章节名（Install / Usage 这类对预览无意义）
    while True:
        before = t
        t = _SECTION_TITLE.sub("", t).strip()
        if t == before:
            break
    t = re.sub(r"(?:see|see also|read more|more info)\s*[:：]?\s*$", "", t,
               flags=re.IGNORECASE).strip()
    if _BADGE_ONLY.match(t):
        return ""
    return t[:limit]
