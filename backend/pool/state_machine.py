"""流量池状态机。

把抖音的"逐级赛马"机制迁移到仓库推荐上。

层级：cold(0) → basic(1) → mid(2) → high(3) → viral(4)
淘汰：-1

晋级条件（实测阈值）：
    深读率 ≥ 0.30  或  Star 率 ≥ 0.03
淘汰条件：
    深读率 < 0.08 且 曝光已用尽

⭐ 特色：保留"复活通路"。抖音 2026 允许老内容因长尾互动重新被推
（俗称"挖坟"），我们同样允许被淘汰的仓库因新的 star 增长复活。
"""
from __future__ import annotations

import sqlite3

from core.config import (
    POOL_DEMOTE_DEEP_RATE,
    POOL_LEVELS,
    POOL_PROMOTE_DEEP_RATE,
    POOL_PROMOTE_STAR_RATE,
    POOL_QUOTA_MIN,
    POOL_QUOTA_POOL_FACTOR,
)


def effective_quota(conn: sqlite3.Connection, level: int) -> int:
    """按池子规模自适应的曝光配额。

    ⚠️ 配置里的配额是"平台级"数值（抖音冷启动池就是几百），
       但自建部署的仓库池可能只有几十个，永远填不满 200，
       流量池因此永久静止 —— 晋级/淘汰形同虚设。
       实测：29 个仓库跑 25 轮，每个仓库曝光 ~48 次，
       quota=200 时没有任何一次晋级发生。

    有效配额 = clamp(池子规模 × 系数, 下限, 配置配额)
    """
    total = conn.execute("SELECT COUNT(*) AS c FROM repos").fetchone()["c"] or 1
    configured = POOL_LEVELS[max(0, min(level, len(POOL_LEVELS) - 1))]["quota"]
    adaptive = max(POOL_QUOTA_MIN, total * POOL_QUOTA_POOL_FACTOR)
    return int(min(configured, adaptive))


def ensure_pool(conn: sqlite3.Connection, repo_id: int) -> sqlite3.Row:
    """确保仓库有池子记录，没有则创建（进入冷启动池）。"""
    row = conn.execute(
        "SELECT * FROM repo_pools WHERE repo_id = ?", (repo_id,)
    ).fetchone()
    if row:
        return row

    conn.execute(
        """INSERT INTO repo_pools
           (repo_id, current_pool, exposure_quota)
           VALUES (?, 0, ?)""",
        (repo_id, effective_quota(conn, 0)),
    )
    return conn.execute(
        "SELECT * FROM repo_pools WHERE repo_id = ?", (repo_id,)
    ).fetchone()


def record_impression(conn: sqlite3.Connection, repo_id: int) -> None:
    """记录一次曝光。"""
    ensure_pool(conn, repo_id)
    conn.execute(
        """UPDATE repo_pools
           SET impressions = impressions + 1,
               exposures_used = exposures_used + 1,
               updated_at = datetime('now')
           WHERE repo_id = ?""",
        (repo_id,),
    )


def record_event(conn: sqlite3.Connection, repo_id: int,
                 event_type: str) -> None:
    """把行为事件累加到池子的统计上。"""
    # ⚠️ 注意：这里只映射 repo_pools 表【真实存在】的计数器列。
    #    "关注作者"是 follows 表的行，不是仓库级计数，没有对应列，
    #    之前映射到 "follows" 会触发 no such column 报错。
    col_map = {
        "click": "clicks",
        "view": "clicks",
        "read_readme": "deep_reads",
        "deep_read": "deep_reads",
        "like": "likes",
        "star_click": "stars_gained",
        "comment": "comments",
        "share": "shares",
        # follow_owner → 无仓库级列，靠 follows 表统计，不走这里
    }
    col = col_map.get(event_type)
    if not col:
        return

    ensure_pool(conn, repo_id)
    conn.execute(
        f"""UPDATE repo_pools
            SET {col} = {col} + 1, updated_at = datetime('now')
            WHERE repo_id = ?""",
        (repo_id,),
    )
    recalc_rates(conn, repo_id)


def recalc_rates(conn: sqlite3.Connection, repo_id: int) -> None:
    """重算率值。供晋级判定使用。"""
    row = conn.execute(
        "SELECT impressions, clicks, deep_reads, stars_gained "
        "FROM repo_pools WHERE repo_id = ?",
        (repo_id,),
    ).fetchone()
    if not row:
        return

    imp = max(1, row["impressions"])
    conn.execute(
        """UPDATE repo_pools
           SET ctr = ?, deep_rate = ?, star_rate = ?
           WHERE repo_id = ?""",
        (
            row["clicks"] / imp,
            row["deep_reads"] / imp,
            row["stars_gained"] / imp,
            repo_id,
        ),
    )


def evaluate_promotion(conn: sqlite3.Connection,
                       repo_id: int) -> dict:
    """评估单个仓库是否应晋级/淘汰。返回决策结果。"""
    row = ensure_pool(conn, repo_id)
    level = row["current_pool"]
    quota = row["exposure_quota"]
    used = row["exposures_used"]

    if level < 0:
        # 已淘汰 —— 检查是否满足复活条件
        revived = _check_revive(conn, repo_id, row)
        return {"action": "revive" if revived else "stay",
                "repo_id": repo_id, "level": level}

    # 曝光还没用够，先攒数据
    if used < quota:
        return {"action": "wait", "repo_id": repo_id, "level": level,
                "progress": f"{used}/{quota}"}

    deep_rate = row["deep_rate"] or 0.0
    star_rate = row["star_rate"] or 0.0

    # 晋级判定
    if deep_rate >= POOL_PROMOTE_DEEP_RATE or star_rate >= POOL_PROMOTE_STAR_RATE:
        return {"action": "promote", "repo_id": repo_id, "level": level,
                "deep_rate": round(deep_rate, 4),
                "star_rate": round(star_rate, 4)}

    # 淘汰判定
    if deep_rate < POOL_DEMOTE_DEEP_RATE:
        return {"action": "demote", "repo_id": repo_id, "level": level,
                "deep_rate": round(deep_rate, 4)}

    return {"action": "hold", "repo_id": repo_id, "level": level}


def apply_promotion(conn: sqlite3.Connection, repo_id: int) -> str:
    """执行晋级/淘汰决策，返回实际动作。"""
    decision = evaluate_promotion(conn, repo_id)
    action = decision["action"]
    row = conn.execute(
        "SELECT current_pool FROM repo_pools WHERE repo_id = ?", (repo_id,)
    ).fetchone()
    if not row:
        return "none"

    level = row["current_pool"]

    if action == "promote":
        next_level = min(level + 1, len(POOL_LEVELS) - 1)
        _reset_pool_for_level(conn, repo_id, next_level, promoted=True)
        return "promote"

    if action == "demote":
        # 不是直接淘汰，先退一级；已在最低级才淘汰
        if level <= 0:
            conn.execute(
                """UPDATE repo_pools
                   SET current_pool = -1,
                       demoted_at = datetime('now'),
                       updated_at = datetime('now')
                   WHERE repo_id = ?""",
                (repo_id,),
            )
            return "eliminate"
        _reset_pool_for_level(conn, repo_id, level - 1, promoted=False)
        return "demote"

    if action == "revive":
        _reset_pool_for_level(conn, repo_id, 1, promoted=True)
        return "revive"

    return "none"


def _reset_pool_for_level(conn: sqlite3.Connection, repo_id: int,
                          level: int, promoted: bool) -> None:
    """进入新层级：重置本层配额与统计（保留历史累计用于分析）。"""
    quota = effective_quota(conn, level)
    conn.execute(
        """UPDATE repo_pools
           SET current_pool = ?,
               exposure_quota = ?,
               exposures_used = 0,
               impressions = 0,
               clicks = 0,
               deep_reads = 0,
               likes = 0,
               stars_gained = 0,
               comments = 0,
               shares = 0,
               dwell_sum_ms = 0,
               ctr = 0,
               deep_rate = 0,
               star_rate = 0,
               entered_pool_at = datetime('now'),
               promoted_at = CASE WHEN ? THEN datetime('now')
                                  ELSE promoted_at END,
               updated_at = datetime('now')
           WHERE repo_id = ?""",
        (level, quota, 1 if promoted else 0, repo_id),
    )


def _check_revive(conn: sqlite3.Connection, repo_id: int,
                  row: sqlite3.Row) -> bool:
    """复活判定。

    被淘汰的仓库，若近期又获得了显著 star 增长（说明外部重新关注），
    则允许回到初级池重新赛马。
    """
    recent = conn.execute(
        """SELECT COUNT(*) AS n FROM repo_star_snapshots
           WHERE repo_id = ? AND captured_at > datetime('now', '-7 day')""",
        (repo_id,),
    ).fetchone()

    if not recent or recent["n"] < 2:
        return False

    first_last = conn.execute(
        """SELECT
             (SELECT stars FROM repo_star_snapshots
              WHERE repo_id = ? AND captured_at > datetime('now','-7 day')
              ORDER BY captured_at ASC LIMIT 1) AS s0,
             (SELECT stars FROM repo_star_snapshots
              WHERE repo_id = ? AND captured_at > datetime('now','-7 day')
              ORDER BY captured_at DESC LIMIT 1) AS s1""",
        (repo_id, repo_id),
    ).fetchone()

    if not first_last or first_last["s0"] is None or first_last["s1"] is None:
        return False

    growth = first_last["s1"] - first_last["s0"]
    base = max(1, first_last["s0"])
    return (growth / base) >= 0.05    # 7 天内涨了 5% 以上 = 值得复活


def promote_all(conn: sqlite3.Connection, limit: int = 500) -> dict:
    """批量执行晋级评估（供定时任务调用）。"""
    rows = conn.execute(
        """SELECT repo_id FROM repo_pools
           WHERE current_pool >= 0 AND exposures_used >= exposure_quota
           LIMIT ?""",
        (limit,),
    ).fetchall()

    stats: dict[str, int] = {}
    for r in rows:
        action = apply_promotion(conn, r["repo_id"])
        stats[action] = stats.get(action, 0) + 1

    return stats
