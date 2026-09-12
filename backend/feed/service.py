"""缓存池 Feed 服务 —— 队列优先，见底补货。

这是 Feed 的主入口（替代原来的"每次实时跑全管线"）。

流程：
    1. 从队列取 limit 条（取走即从队列删除）
    2. 队列不够 → 触发补货（按画像定向抓取 → 灌队列）
    3. 补货后仍不够 → 退化为实时推荐（保底，绝不空屏）
    4. 记录曝光与队列余量

为什么保留"实时推荐保底"：
    队列见底 + 补货冷却期内，用户还在刷。
    这时候宁可跑一次实时管线，也不能返回空列表。
"""
from __future__ import annotations

import random
import sqlite3
import time

from core.config import (
    QUEUE_INITIAL_SIZE,
    QUEUE_LOW_WATERMARK,
    QUEUE_REFILL_COOLDOWN_SEC,
    QUEUE_REFILL_MIN_GAIN_RATIO,
    QUEUE_REFILL_RETRY_SEC,
    QUEUE_TARGET_SIZE,
)
from feed import queue as Q
from feed.refill import build_fetch_plan, pick_from_local_pool, plan_summary


# 上次补货时间（进程内，避免短时间内反复触发）
_last_refill_at: dict[int, float] = {}
# 上次"补货失败"后的快速重试时间戳。
# 与 _last_refill_at 分开：补货成功才锁长冷却，失败只锁短重试间隔。
_last_retry_at: dict[int, float] = {}


def _cooldown_ok(user_id: int) -> bool:
    """是否允许触发一次真正的补货。

    ⚠️ 关键修复：冷却只在"上一次真的补到货"时才生效。

        原实现的问题 —— 无论入队多少条都刷新 _last_refill_at，
        于是"候选池枯竭、入队 0 条"也会锁死 30 秒冷却。
        压测表现：刷 200 条，第 5 轮起队列恒为 0，
        16/20 轮全靠实时保底 —— 队列只出不进。

        现在的语义：
          补到货  → 锁 QUEUE_REFILL_COOLDOWN_SEC（30s），等用户慢慢刷
          没补到  → 只锁 QUEUE_REFILL_RETRY_SEC（3s），允许尽快重试
                    （候选池可能因为别的用户操作/冷却过期而变得有货）
    """
    now = time.time()
    if (now - _last_refill_at.get(user_id, 0.0)) < QUEUE_REFILL_COOLDOWN_SEC:
        return False
    if (now - _last_retry_at.get(user_id, 0.0)) < QUEUE_REFILL_RETRY_SEC:
        return False
    return True


def refill_queue(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    trigger: str = "low_watermark",
    target: int | None = None,
) -> dict:
    """补货：按用户画像决定抓什么，灌进队列。

    ⭐ 这就是"缓存池快见底时，爬虫看用户指标再爬"的实现。
    """
    target = target or QUEUE_TARGET_SIZE
    before = Q.queue_size(conn, user_id)
    need = max(0, target - before)

    if need <= 0:
        return {"refilled": False, "reason": "already_full",
                "queue_before": before, "enqueued": 0}

    # ── ① 读用户画像/指标，生成抓取计划 ──
    plan = build_fetch_plan(conn, user_id, queue_size=before)

    # ── ② 按计划抓取（当前从本地池挑选，接真实爬虫后换这里）──
    picked = pick_from_local_pool(conn, user_id, plan, need=need)

    # ── ③ 抓到的仓库要经过打分再入队（保证队列内顺序合理）──
    candidates = _score_picked(conn, user_id, picked, plan)

    batch_id = Q.new_batch_id()
    reasons = {c.repo_id: _reason_for(c, plan) for c in candidates}
    enqueued = Q.enqueue_candidates(
        conn, user_id, candidates, batch_id=batch_id, reasons=reasons
    )

    Q.log_refill(
        conn, user_id,
        trigger=trigger,
        queue_before=before,
        fetched=len(picked),
        enqueued=enqueued,
        strategy=plan.to_dict(),
    )

    # ⚠️ 冷却分级：补到货才锁长冷却，没补到只锁短重试间隔。
    #    否则候选池枯竭会连锁导致队列长期为空。
    min_gain = max(1, int(need * QUEUE_REFILL_MIN_GAIN_RATIO))
    ok = enqueued >= min_gain
    if ok:
        _last_refill_at[user_id] = time.time()
    else:
        _last_retry_at[user_id] = time.time()

    return {
        "refilled": True,
        "success": ok,
        "queue_before": before,
        "queue_after": Q.queue_size(conn, user_id),
        "fetched": len(picked),
        "enqueued": enqueued,
        "min_gain": min_gain,
        "batch_id": batch_id,
        "plan": plan.to_dict(),
    }


def _score_picked(conn: sqlite3.Connection, user_id: int,
                  repo_ids: list[int], plan) -> list:
    """给抓回来的仓库打分排序（复用现有 scorer）。"""
    if not repo_ids:
        return []

    from api.feed_service import load_penalties, load_profile
    from rank.scorer import Candidate, score_candidate
    from recall.channels import _row_to_candidate

    profile = load_profile(conn, user_id, auto_rebuild=False)
    penalties = load_penalties(conn, user_id)

    placeholders = ",".join("?" for _ in repo_ids)
    rows = conn.execute(
        f"""SELECT r.*,
                p.impressions, p.clicks, p.deep_reads, p.likes,
                p.stars_gained, p.comments, p.shares,
                0 AS follows
            FROM repos r
            LEFT JOIN repo_pools p ON p.repo_id = r.id
            WHERE r.id IN ({placeholders})""",
        repo_ids,
    ).fetchall()

    cands = []
    for row in rows:
        c = _row_to_candidate(row, "refill")
        score_candidate(c, profile, penalties=penalties)
        cands.append(c)

    cands.sort(key=lambda c: -c.score)
    return cands


def _reason_for(c, plan) -> str:
    """生成入队时缓存的推荐理由。"""
    parts = []
    if getattr(c, "forgotten_score", 0) >= 0.7:
        parts.append("优质但少有人发现")
    if "interest" in (c.source_channels or []):
        parts.append("符合你的技术兴趣")
    if plan.languages and (c.language or "") in plan.languages:
        parts.append(f"{c.language} 是你常用的语言")
    if not parts:
        parts.append("根据你的浏览兴趣挑选")
    return "；".join(parts)


# ------------------------------------------------------------------ 主入口

def get_feed(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    limit: int = 10,
    search_query: str | None = None,
    rand: random.Random | None = None,
) -> dict:
    """从缓存池取 Feed。"""
    rng = rand or random.Random()

    # ── 搜索干预优先：用户主动搜了，就不走队列 ──
    if search_query:
        from api.feed_service import build_feed as _build
        res = _build(conn, user_id, limit=limit, search_query=search_query,
                     rand=rng)
        return {
            "items": [_to_api(c) for c in res["items"]],
            "meta": {**res["meta"], "source": "realtime_search",
                     "queue_size": Q.queue_size(conn, user_id)},
        }

    meta: dict = {"source": "queue"}

    # ── ① 取队首 ──
    rows = Q.pop_queue(conn, user_id, limit)
    meta["popped"] = len(rows)

    # ── ② 不够 → 补货 ──
    if len(rows) < limit:
        if _cooldown_ok(user_id):
            info = refill_queue(
                conn, user_id,
                trigger="initial" if not rows else "low_watermark",
                target=max(QUEUE_TARGET_SIZE, limit * 3),
            )
            meta["refill"] = {
                k: v for k, v in info.items() if k != "plan"
            }
            if info.get("plan"):
                meta["refill_plan"] = info["plan"]
        else:
            meta["refill"] = {"refilled": False, "reason": "cooldown"}

        # 补完再取一次（只补缺口部分）
        extra = Q.pop_queue(conn, user_id, limit - len(rows))
        rows = list(rows) + list(extra)

    # ── ③ 还不够 → 实时推荐保底（绝不空屏）──
    if len(rows) < limit:
        from api.feed_service import build_feed as _build
        res = _build(conn, user_id, limit=limit - len(rows), rand=rng)
        realtime = [_to_api(c) for c in res["items"]]
        meta["source"] = "queue+realtime"
        meta["realtime"] = len(realtime)
        items = [Q.row_to_dict(r) for r in rows] + realtime
    else:
        items = [Q.row_to_dict(r) for r in rows]

    # ── ④ 记录曝光 + 队列水位 ──
    size_after = Q.queue_size(conn, user_id)
    meta["queue_size"] = size_after
    meta["needs_refill"] = size_after <= QUEUE_LOW_WATERMARK

    return {"items": items, "meta": meta}


def _to_api(c) -> dict:
    from api.feed_service import candidate_to_dict
    return candidate_to_dict(c)


# ------------------------------------------------------------------ 初始化

def ensure_queue(conn: sqlite3.Connection, user_id: int,
                 *, size: int | None = None) -> dict:
    """确保队列有货（首次访问 / 手动预热）。"""
    size = size or QUEUE_INITIAL_SIZE
    if Q.queue_size(conn, user_id) >= size:
        return {"ok": True, "skipped": True,
                "queue_size": Q.queue_size(conn, user_id)}
    return refill_queue(conn, user_id, trigger="initial", target=size)
