"""用户待刷队列（缓存池）。

设计动机 —— 为什么要有这一层：

    原来的做法是"每次请求实时跑完整推荐管线"，有三个硬伤：
      1. `feed_impressions` 只增不减，池子无限膨胀
      2. 用户刷完一轮就没新内容了，只能靠去重降级重复推老仓库
      3. 每次请求都跑 7 路召回 + 打分 + 重排，纯浪费算力

    缓存池模式：
      ┌─────────┐   定向补货    ┌──────────┐   取走即删    ┌────────┐
      │  爬虫   │ ───────────→ │ 待刷队列 │ ───────────→ │  用户  │
      └─────────┘   (按画像)   └──────────┘              └────────┘
                                    ↑
                            剩 ≤6 条时触发补货

    ⭐ 关键设计：删除的是【队列项】，不是仓库本身。
       仓库、标签、流量池统计全部保留 —— 别的用户还能刷到，
       算法学习也不受影响。

与"去重窗口"的关系：
    有了队列，7 天去重窗口就退居二线了 —— 队列本身保证不重复，
    去重窗口只在补货时用，避免把刚刷过的又灌回来。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from core.config import (
    QUEUE_LOW_WATERMARK,
    QUEUE_TARGET_SIZE,
)
from rank.scorer import Candidate


# ------------------------------------------------------------------ 队列状态

def queue_size(conn: sqlite3.Connection, user_id: int) -> int:
    """当前队列里还剩多少条。"""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM feed_queue WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return row["n"] if row else 0


def is_low(conn: sqlite3.Connection, user_id: int) -> bool:
    """是否到了补货水位（剩 ≤ QUEUE_LOW_WATERMARK 条）。"""
    return queue_size(conn, user_id) <= QUEUE_LOW_WATERMARK


def peek_queue(conn: sqlite3.Connection, user_id: int,
               limit: int) -> list[sqlite3.Row]:
    """看一眼队列前 limit 条（不删除）。"""
    return conn.execute(
        """SELECT q.id AS queue_id, q.repo_id, q.position, q.score,
                  q.channel, q.reason, q.batch_id,
                  r.full_name, r.owner, r.name, r.description, r.language,
                  r.stars, r.topics, r.tags_json, r.license_spdx,
                  r.license_risk, r.forgotten_score, r.created_at_gh,
                  r.pushed_at_gh
           FROM feed_queue q
           JOIN repos r ON r.id = q.repo_id
           WHERE q.user_id = ?
           ORDER BY q.position ASC, q.id ASC
           LIMIT ?""",
        (user_id, limit),
    ).fetchall()


# ------------------------------------------------------------------ 出队（取走即删）

def pop_queue(conn: sqlite3.Connection, user_id: int,
              limit: int) -> list[sqlite3.Row]:
    """从队首取出 limit 条，并【立即从队列删除】。

    ⚠️ 删除队列项 ≠ 删除仓库。
       只是标记"这个用户已经刷过了"，仓库仍在 repos 表里，
       其他用户照常能刷到。
    """
    rows = peek_queue(conn, user_id, limit)
    if not rows:
        return []

    ids = [r["queue_id"] for r in rows]
    placeholders = ",".join("?" for _ in ids)
    conn.execute(
        f"DELETE FROM feed_queue WHERE id IN ({placeholders})", ids
    )
    return rows


def clear_queue(conn: sqlite3.Connection, user_id: int) -> int:
    """清空某用户的队列（调试/重置用）。"""
    cur = conn.execute(
        "DELETE FROM feed_queue WHERE user_id = ?", (user_id,)
    )
    return cur.rowcount


# ------------------------------------------------------------------ 入队

def enqueue_candidates(
    conn: sqlite3.Connection,
    user_id: int,
    candidates: list[Candidate],
    *,
    batch_id: str,
    reasons: dict[int, str] | None = None,
) -> int:
    """把候选写入队列。

    已有的条目会被跳过（UNIQUE(user_id, repo_id)），
    所以重复补货不会造成队列里出现同一个仓库。
    """
    if not candidates:
        return 0

    # position 从当前最大位置继续往后排
    row = conn.execute(
        "SELECT COALESCE(MAX(position), -1) AS m FROM feed_queue WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    start = (row["m"] if row else -1) + 1

    inserted = 0
    for i, c in enumerate(candidates):
        cur = conn.execute(
            """INSERT OR IGNORE INTO feed_queue
                   (user_id, repo_id, position, score, channel, reason, batch_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                c.repo_id,
                start + i,
                c.score,
                ",".join(c.source_channels) or c.channel,
                (reasons or {}).get(c.repo_id),
                batch_id,
            ),
        )
        inserted += cur.rowcount
    return inserted


def new_batch_id() -> str:
    return uuid.uuid4().hex[:12]


# ------------------------------------------------------------------ 补货日志

def log_refill(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    trigger: str,
    queue_before: int,
    fetched: int,
    enqueued: int,
    strategy: dict | None = None,
) -> None:
    conn.execute(
        """INSERT INTO refill_log
               (user_id, trigger, queue_before, fetched, enqueued, strategy)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            user_id, trigger, queue_before, fetched, enqueued,
            json.dumps(strategy or {}, ensure_ascii=False),
        ),
    )


def recent_refills(conn: sqlite3.Connection, user_id: int,
                   limit: int = 10) -> list[dict]:
    rows = conn.execute(
        """SELECT trigger, queue_before, fetched, enqueued, strategy, created_at
           FROM refill_log WHERE user_id = ?
           ORDER BY created_at DESC, id DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["strategy"] = json.loads(d["strategy"] or "{}")
        except (json.JSONDecodeError, TypeError):
            d["strategy"] = {}
        out.append(d)
    return out


# ------------------------------------------------------------------ 队列 → API 输出

def row_to_dict(row: sqlite3.Row) -> dict:
    """把队列行转成前端要的结构。

    注意：这里不再跑打分，直接用入队时缓存的 score / reason ——
    这正是缓存池的价值：读取时零算法开销。
    """
    try:
        topics = json.loads(row["topics"]) if row["topics"] else []
    except (json.JSONDecodeError, TypeError):
        topics = []
    try:
        tags = json.loads(row["tags_json"]) if row["tags_json"] else {}
    except (json.JSONDecodeError, TypeError):
        tags = {}

    return {
        "repo_id": row["repo_id"],
        "full_name": row["full_name"],
        "owner": row["owner"],
        "name": row["name"],
        "description": row["description"],
        "language": row["language"],
        "stars": row["stars"],
        "topics": topics[:8] if isinstance(topics, list) else [],
        "tags": [
            {"tag": k, "weight": round(float(v), 3)}
            for k, v in sorted(
                tags.items(), key=lambda kv: -float(kv[1])
            )[:12]
        ],
        "license_spdx": row["license_spdx"],
        "license_risk": row["license_risk"],
        "score": round(row["score"] or 0.0, 4),
        "channels": (row["channel"] or "").split(",") if row["channel"] else [],
        "reason": row["reason"] or "",
        # ⚠️ 这两个字段前端卡片要用：
        #    forgotten_score → 决定是否打「💎 遗珠」标记（本项目的品牌符号）
        #    pushed_at_gh    → "更新于 N 天前"，判断仓库是否还活跃
        #    它们本来就在 SELECT 里查出来了，之前漏放进返回体。
        "forgotten_score": round(row["forgotten_score"] or 0.0, 4),
        "pushed_at_gh": row["pushed_at_gh"],
        "created_at_gh": row["created_at_gh"],
        "url": f"https://github.com/{row['full_name']}",
    }


# ------------------------------------------------------------------ 队列统计

def queue_stats(conn: sqlite3.Connection, user_id: int) -> dict:
    """队列健康状况（供 /api/stats/queue 用）。"""
    size = queue_size(conn, user_id)
    by_channel = conn.execute(
        """SELECT channel, COUNT(*) AS n FROM feed_queue
           WHERE user_id = ? GROUP BY channel ORDER BY n DESC""",
        (user_id,),
    ).fetchall()
    return {
        "size": size,
        "low_watermark": QUEUE_LOW_WATERMARK,
        "target_size": QUEUE_TARGET_SIZE,
        "needs_refill": size <= QUEUE_LOW_WATERMARK,
        "by_channel": [dict(r) for r in by_channel],
    }
