"""模拟真实用户行为，验证此前"从没跑通过"的两条链路：

  1. 兴趣召回（recall_interest）—— 依赖用户画像，之前 user_profiles 表永远空着
  2. 流量池晋级（evaluate_promotion / apply_promotion）—— 之前 29 个仓库全卡在 cold 池

用法：
    python backend/jobs/simulate.py               # 跑完整模拟
    python backend/jobs/simulate.py --rounds 30   # 指定轮数
    python backend/jobs/simulate.py --reset       # 先清库再跑

模拟逻辑（尽量贴近真实用户）：
    - 一个对「LLM 推理 / 量化」感兴趣的 Python 用户
    - 对相关主题的仓库有更高概率深读、收藏
    - 对无关主题（游戏、内核）有更高概率快速划过
    - 每隔几轮做一次负反馈
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from api.feed_service import build_feed  # noqa: E402
from db.connection import get_conn, init_db  # noqa: E402
from pool.state_machine import (  # noqa: E402
    evaluate_promotion,
    apply_promotion,
    record_event,
    record_impression,
)
from profile.service import rebuild_profile  # noqa: E402
from tags.extractor import init_jieba  # noqa: E402

# 模拟用户的兴趣画像（用标签表达）
INTEREST_TAGS = {
    "llm", "推理", "量化", "gguf", "inference", "quantization",
    "python", "embedding", "模型", "llama", "mistral", "prompt",
    "onnx", "distillation", "vector-search", "embeddings",
}
# 明确不感兴趣的方向
DISINTEREST_TAGS = {"kernel", "内核", "游戏", "gui", "shader", "riscv", "os"}


def _topic_match(tags: dict[str, float], wanted: set[str]) -> float:
    """标签与目标兴趣的重合度（0~1）。

    ⚠️ 用"命中标签的权重之和 / 总权重"，而不是"命中个数"：
       命中的若是低权重装饰词，不该算作强兴趣。
    """
    if not tags:
        return 0.0
    hit = sum(w for t, w in tags.items() if t in wanted)
    total = sum(tags.values()) or 1.0
    return min(1.0, (hit / total) * 4.0)


def simulate(rounds: int = 25, user_id: int = 1,
             seed: int = 7) -> dict:
    rng = random.Random(seed)
    init_jieba()
    init_db()

    stats = {
        "rounds": 0, "impressions": 0, "deep_read": 0, "star": 0,
        "skip": 0, "dislike": 0,
    }
    promo_events: list[tuple[int, str]] = []

    for rnd in range(rounds):
        with get_conn() as conn:
            res = build_feed(conn, user_id, limit=10, rand=rng)
            items = res["items"]

            if not items:
                print(f"  [轮 {rnd+1}] 无候选，跳过")
                continue

            for pos, c in enumerate(items):
                stats["impressions"] += 1
                match = _topic_match(c.tags, INTEREST_TAGS)
                anti = _topic_match(c.tags, DISINTEREST_TAGS)

                # 兴趣高 → 更可能深读；无关 → 更可能划过
                p_deep = 0.15 + 0.65 * match - 0.3 * anti
                p_deep = max(0.02, min(0.9, p_deep))
                # 位置靠后天然更少被看
                p_deep *= (1.0 - 0.03 * pos)

                roll = rng.random()
                if roll < p_deep:
                    # 深读
                    conn.execute(
                        """INSERT INTO user_events
                           (user_id, repo_id, event_type, weight, dwell_ms,
                            scroll_depth, source_channel, rank_position)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (user_id, c.repo_id, "deep_read",
                         float(match), int(30000 * p_deep), 0.97,
                         c.channel, pos),
                    )
                    record_event(conn, c.repo_id, "deep_read")
                    stats["deep_read"] += 1

                    # 深读后有一定概率收藏（收藏权重最高）
                    if rng.random() < 0.25 + 0.5 * match:
                        conn.execute(
                            """INSERT OR IGNORE INTO stars
                               (user_id, repo_id, gh_starred, synced_at)
                               VALUES (?,?,1,datetime('now'))""",
                            (user_id, c.repo_id),
                        )
                        conn.execute(
                            """INSERT INTO user_events
                               (user_id, repo_id, event_type, weight,
                                rank_position)
                               VALUES (?,?,'star_click',8.0,?)""",
                            (user_id, c.repo_id, pos),
                        )
                        record_event(conn, c.repo_id, "star_click")
                        stats["star"] += 1

                    # 偶尔点赞
                    if rng.random() < 0.4:
                        conn.execute(
                            """INSERT OR IGNORE INTO likes (user_id, repo_id)
                               VALUES (?,?)""",
                            (user_id, c.repo_id),
                        )
                        conn.execute(
                            """INSERT INTO user_events
                               (user_id, repo_id, event_type, weight,
                                rank_position)
                               VALUES (?,?,'like',3.0,?)""",
                            (user_id, c.repo_id, pos),
                        )
                        record_event(conn, c.repo_id, "like")
                else:
                    stats["skip"] += 1
                    conn.execute(
                        """INSERT INTO user_events
                           (user_id, repo_id, event_type, weight,
                            dwell_ms, scroll_depth, rank_position)
                           VALUES (?,?,'skip',-1.0,1200,0.12,?)""",
                        (user_id, c.repo_id, pos),
                    )

                # 同步流量池计数
                record_impression(conn, c.repo_id)

            # 偶尔负反馈（对明显不感兴趣的）
            if rnd % 6 == 5:
                anti_items = [c for c in items
                              if _topic_match(c.tags, DISINTEREST_TAGS) > 0.3]
                if anti_items:
                    victim = anti_items[0]
                    conn.execute(
                        """INSERT INTO user_events
                           (user_id, repo_id, event_type, weight, dwell_ms)
                           VALUES (?,?,'dislike',-2.0,0)""",
                        (user_id, victim.repo_id),
                    )
                    stats["dislike"] += 1

            # 每轮重建画像（真实系统里是事件触发，这里手动触发便于观察）
            rebuild_profile(conn, user_id)

            stats["rounds"] += 1

            # ── 晋级检查 ──
            for c in items:
                d = evaluate_promotion(conn, c.repo_id)
                if d["action"] in ("promote", "demote", "eliminate", "revive"):
                    act = apply_promotion(conn, c.repo_id)
                    promo_events.append((c.repo_id, act))

    return {"stats": stats, "promotions": promo_events}


def report() -> None:
    """打印验证结果。"""
    with get_conn() as conn:
        print("\n" + "=" * 74)
        print("  ①  用户画像")
        print("=" * 74)
        row = conn.execute(
            "SELECT top_topics, interest_count, updated_at "
            "FROM user_profiles WHERE user_id = 1"
        ).fetchone()
        if row:
            import json
            topics = json.loads(row["top_topics"] or "[]")
            print(f"  标签数 = {row['interest_count']}   更新时间 = {row['updated_at']}")
            print("  Top 12 兴趣标签：")
            for i, t in enumerate(topics[:12], 1):
                print(f"    {i:>2}. {t['tag']:<22}{t['weight']:.4f}")
        else:
            print("  ❌ 画像仍为空")

        print("\n" + "=" * 74)
        print("  ②  流量池分布")
        print("=" * 74)
        names = {-1: "已淘汰", 0: "冷启动", 1: "初级池", 2: "中级池",
                 3: "高级池", 4: "爆款池"}
        for r in conn.execute(
            """SELECT current_pool, COUNT(*) AS n,
                      SUM(impressions) AS imp,
                      AVG(deep_rate) AS dr
               FROM repo_pools GROUP BY current_pool
               ORDER BY current_pool"""
        ):
            lv = r["current_pool"]
            print(f"  {names.get(lv, lv):<8} {r['n']:>3} 个   累计曝光 {r['imp'] or 0:>5}   "
                  f"平均深读率 {r['dr'] or 0:.4f}")

        print("\n" + "=" * 74)
        print("  ③  兴趣召回是否激活")
        print("=" * 74)
        from profile.builder import TagVector
        from recall.channels import recall_interest
        import json as _json
        tv = TagVector()
        pr = conn.execute(
            "SELECT top_topics FROM user_profiles WHERE user_id = 1"
        ).fetchone()
        if pr and pr["top_topics"]:
            for item in _json.loads(pr["top_topics"]):
                tv.weights[item["tag"]] = float(item["weight"])
        ir = recall_interest(conn, tv, 1, limit=8)
        print(f"  画像标签 {tv.size} 个 → 兴趣召回命中 {len(ir)} 个仓库")
        for c in ir[:6]:
            print(f"    {c.full_name:<34}{c.stars:>8}★")

        print("\n" + "=" * 74)
        print("  ④  行为统计")
        print("=" * 74)
        for r in conn.execute(
            """SELECT event_type, COUNT(*) AS n FROM user_events
               WHERE user_id = 1 GROUP BY event_type ORDER BY n DESC"""
        ):
            print(f"  {r['event_type']:<14}{r['n']:>5}")
        n_star = conn.execute(
            "SELECT COUNT(*) AS n FROM stars WHERE user_id=1"
        ).fetchone()["n"]
        print(f"  {'star（收藏）':<14}{n_star:>5}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="模拟用户行为，验证画像与流量池")
    ap.add_argument("--rounds", type=int, default=25)
    ap.add_argument("--reset", action="store_true", help="先清库再跑")
    args = ap.parse_args()

    if args.reset:
        from core.config import DB_PATH
        for suffix in ("", "-wal", "-shm"):
            p = str(DB_PATH) + suffix
            if Path(p).exists():
                Path(p).unlink()
        print("🗑️  已清空数据库")

    from jobs.seed_data import seed
    seed(reset=False)

    print(f"\n开始模拟 {args.rounds} 轮浏览...\n")
    out = simulate(rounds=args.rounds)

    s = out["stats"]
    print(f"\n模拟完成：{s['rounds']} 轮 / 曝光 {s['impressions']} 次 / "
          f"深读 {s['deep_read']} 次 / 收藏 {s['star']} 次 / "
          f"划过 {s['skip']} 次 / 负反馈 {s['dislike']} 次")

    if out["promotions"]:
        print(f"\n流量池变动 {len(out['promotions'])} 次：")
        tail: dict[str, int] = {}
        for _, act in out["promotions"]:
            tail[act] = tail.get(act, 0) + 1
        for act, n in sorted(tail.items(), key=lambda kv: -kv[1]):
            print(f"    {act:<12}{n:>4} 次")
    else:
        print("\n⚠️  没有发生任何晋级 —— 检查曝光配额或阈值设置")

    report()
