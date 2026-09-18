"""AI 周报：把这周的行为数据变成一份"发现总结 + 学习路线"。

为什么做（用户提的 P0）
    竖滑卡片刷久了会累，也容易"滑过就忘" —— 用户需要一个**回顾层**：
      本周你刷了 N 个仓库 / 深读了 M 个 / 收藏了 K 个，
      其中这几个是你领域里的隐藏宝藏（附一句为什么值得看），
      并给一条简短的下一步路线。这既是留存钩子，也是"被发现的价值"的兑现。

数据来源（全部本地，不外传）
    user_events：impression / skip / deep_read / click / like / star_click + dwell_ms
    repos：名称、简介、标签、星数、领域

产出
    {headline, stats_comment, highlights:[{repo, why}], learning_path:[...], next_step}
    LLM 只负责**把这些事实写成话**，不凭空编项目 —— 项目清单由 SQL 算好再交给它。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from api.insight_service import _llm_json

REPORT_SYSTEM = (
    "你是开源发现周报的编辑。用户使用一个「刷 GitHub 仓库」的信息流产品，"
    "我会给你他**本周真实的行为数据与项目清单**（都是事实，不要编造项目或数字）。\n"
    "请写一份简短、有洞察、口语化的中文周报，输出 JSON：\n"
    "1. headline：一句总结这周的发现（≤30 字，有画面感，不要用「本周报告」这种废话开头）；\n"
    "2. stats_comment：用一句话点评他的浏览习惯（≤60 字，可指出「深读率高/划得很快」这类特征）；\n"
    "3. highlights：2~3 条「本周宝藏」，每条 {\"repo\": \"仓库名\", \"why\": \"为什么值得他看（≤40 字，"
    "结合他的兴趣方向与项目本身特点）\"}；只能从给定清单里选，不要新造项目；\n"
    "4. learning_path：2~3 条具体可执行的建议（≤35 字/条，例如「先跑通 xx 的 demo，再对比 yy 的实现」）；\n"
    "5. next_step：一句话告诉他下周可以关注什么方向（≤30 字）。\n"
    "只输出 JSON：{\"headline\":\"\",\"stats_comment\":\"\",\"highlights\":[],\"learning_path\":[],\"next_step\":\"\"}"
)

_WEEK_DAYS_DEFAULT = 7


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS weekly_reports (
          user_id     INTEGER NOT NULL,
          period_key  TEXT    NOT NULL,
          stats_json  TEXT    NOT NULL,
          report_json TEXT    NOT NULL,
          model       TEXT,
          created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY (user_id, period_key)
        );
        """
    )


def collect_stats(conn: sqlite3.Connection, user_id: int,
                  days: int = _WEEK_DAYS_DEFAULT) -> dict[str, Any]:
    """把「事实」先算清楚 —— LLM 只写字，不负责统计。"""
    win = f"-{int(days)} days"
    counts = {r["event_type"]: r["n"] for r in conn.execute(
        """SELECT event_type, COUNT(*) AS n FROM user_events
           WHERE user_id = ? AND created_at >= datetime('now', ?)
           GROUP BY event_type""",
        (user_id, win),
    )}

    # 本周"值得记一笔"的仓库：有过深读/点赞/收藏/点击的
    engaged = conn.execute(
        """SELECT e.repo_id, MAX(e.event_type) AS last_type,
                  COUNT(*) AS n,
                  SUM(CASE WHEN e.event_type = 'deep_read' THEN 1 ELSE 0 END) AS deep,
                  SUM(CASE WHEN e.event_type IN ('like','star_click') THEN 1 ELSE 0 END) AS liked,
                  SUM(CASE WHEN e.event_type = 'click' THEN 1 ELSE 0 END) AS clicked,
                  r.full_name, r.description, r.stars, r.language, r.topics, r.tags_json
           FROM user_events e JOIN repos r ON r.id = e.repo_id
           WHERE e.user_id = ? AND e.created_at >= datetime('now', ?)
           GROUP BY e.repo_id
           HAVING (deep + liked + clicked) > 0
           ORDER BY (liked * 3 + deep * 2 + clicked) DESC, r.stars DESC
           LIMIT 8""",
        (user_id, win),
    ).fetchall()

    items = []
    for r in engaged:
        try:
            tags = [t for t in json.loads(r["tags_json"] or "{}")][:6]
        except json.JSONDecodeError:
            tags = []
        try:
            topics = [str(t) for t in json.loads(r["topics"] or "[]")][:5]
        except json.JSONDecodeError:
            topics = []
        items.append({
            "repo": r["full_name"],
            "desc": (r["description"] or "")[:120],
            "stars": int(r["stars"] or 0),
            "language": r["language"],
            "tags": tags,
            "topics": topics,
            "deep": int(r["deep"] or 0),
            "liked": int(r["liked"] or 0),
            "clicked": int(r["clicked"] or 0),
        })

    # 兴趣方向（画像 Top 标签）
    try:
        row = conn.execute(
            "SELECT top_topics FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
        interests = [t["tag"] for t in json.loads(row["top_topics"] or "[]")][:10] if row else []
    except Exception:  # noqa: BLE001
        interests = []

    # 语料库本周新增（体现「冷启动搜索在扩张」）
    new_repos = conn.execute(
        """SELECT COUNT(*) AS n FROM repos WHERE first_seen_at >= datetime('now', ?)""",
        (win,),
    ).fetchone()["n"]

    return {
        "days": days,
        "impressions": counts.get("impression", 0),
        "skips": counts.get("skip", 0),
        "deep_reads": counts.get("deep_read", 0),
        "clicks": counts.get("click", 0),
        "likes": counts.get("like", 0) + counts.get("star_click", 0),
        "engaged": items,
        "interests": interests,
        "corpus_new": int(new_repos or 0),
    }


def _period_key(days: int) -> str:
    """周期 key：按"最近 N 天"归一，同一天内复用缓存。"""
    import datetime as _dt
    today = _dt.date.today()
    return f"{days}d-{today.isoformat()}"


def generate_report(conn: sqlite3.Connection, user_id: int, *,
                    days: int = _WEEK_DAYS_DEFAULT, force: bool = False) -> dict[str, Any]:
    ensure_tables(conn)
    stats = collect_stats(conn, user_id, days)
    if stats["impressions"] == 0 and not stats["engaged"]:
        return {"ok": False, "reason": "本周还没有足够的行为数据（先刷几张卡再来）",
                "stats": stats}

    key = _period_key(days)
    if not force:
        row = conn.execute(
            """SELECT report_json, model, created_at FROM weekly_reports
               WHERE user_id = ? AND period_key = ?""",
            (user_id, key),
        ).fetchone()
        if row:
            return {"ok": True, "report": json.loads(row["report_json"]),
                    "stats": stats, "cached": True, "period": key}

    payload = {
        "浏览与互动": {
            "曝光": stats["impressions"], "滑过": stats["skips"],
            "深读": stats["deep_reads"], "点击": stats["clicks"], "收藏/点赞": stats["likes"],
        },
        "兴趣方向": stats["interests"],
        "本周有互动的项目（只能从这里选 highlights）": stats["engaged"],
        "语料库本周新增仓库": stats["corpus_new"],
    }
    data, model = _llm_json(
        conn, REPORT_SYSTEM,
        "本周数据（JSON）：\n" + json.dumps(payload, ensure_ascii=False)[:6000],
        temperature=0.5,
    )
    if not data:
        return {"ok": False, "reason": "AI 暂时不可用（限流或额度用尽）", "stats": stats}

    report = {
        "headline": str(data.get("headline") or "").strip()[:60],
        "stats_comment": str(data.get("stats_comment") or "").strip()[:120],
        "highlights": [
            {"repo": str(h.get("repo") or "")[:80],
             "why": str(h.get("why") or "")[:120]}
            for h in (data.get("highlights") or [])[:3]
            if isinstance(h, dict) and h.get("repo")
        ],
        "learning_path": [str(x)[:80] for x in (data.get("learning_path") or [])[:3]],
        "next_step": str(data.get("next_step") or "").strip()[:60],
    }
    conn.execute(
        """INSERT INTO weekly_reports (user_id, period_key, stats_json, report_json, model)
           VALUES (?,?,?,?,?)
           ON CONFLICT(user_id, period_key) DO UPDATE SET
               stats_json = excluded.stats_json, report_json = excluded.report_json,
               model = excluded.model, created_at = datetime('now')""",
        (user_id, key, json.dumps(stats, ensure_ascii=False),
         json.dumps(report, ensure_ascii=False), model),
    )
    return {"ok": True, "report": report, "stats": stats, "cached": False,
            "period": key, "model": model}
