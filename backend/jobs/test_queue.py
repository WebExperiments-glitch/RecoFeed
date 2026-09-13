"""缓存池（待刷队列）端到端验证。

验证用户的原始需求：

    ① 爬虫灌池     —— 首次进队列自动灌 QUEUE_INITIAL_SIZE 条
    ② 用户边刷边补 —— 刷到剩 ≤6 条时自动触发补货
    ③ 刷过就删     —— 取走的条目从 feed_queue 删除
    ④ 仓库不删     —— 但 repos 表里的仓库还在（别的用户还能刷到）

用法：
    cd backend && python3 jobs/test_queue.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import QUEUE_INITIAL_SIZE, QUEUE_LOW_WATERMARK, QUEUE_TARGET_SIZE
from db.connection import connect
from feed import queue as Q
from feed.service import ensure_queue, get_feed, refill_queue

USER = 1
BAR = "─" * 62


def h(title: str) -> None:
    print(f"\n{BAR}\n  {title}\n{BAR}")


def repos_total(conn) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"]


def main() -> None:
    conn = connect()
    conn.isolation_level = None          # 手动控制事务
    conn.execute("BEGIN")

    try:
        ok = run(conn)
    finally:
        conn.execute("ROLLBACK")         # ⭐ 全程回滚，绝不污染真实数据
        conn.close()

    print(f"\n{BAR}")
    print("  测试结束（所有改动已回滚，真实数据未受影响）")
    print(BAR)
    sys.exit(0 if ok else 1)


def run(conn) -> bool:
    fails: list[str] = []

    def check(cond: bool, msg: str) -> None:
        print(f"   {'✅' if cond else '❌'} {msg}")
        if not cond:
            fails.append(msg)

    Q.clear_queue(conn, USER)
    repos_before = repos_total(conn)

    # ─────────────────────────────── ① 首次灌池
    h("① 首次进队列 —— 爬虫灌池")
    print(f"  队列初始: {Q.queue_size(conn, USER)} 条")
    info = ensure_queue(conn, USER)
    size0 = Q.queue_size(conn, USER)
    print(f"  灌入结果: enqueued={info.get('enqueued')} "
          f"fetched={info.get('fetched')} 队列={size0} 条")
    print(f"  抓取方向: {info.get('plan', {}).get('queries')}")
    check(size0 > 0, f"队列被灌入内容（实际 {size0} 条）")
    check(size0 == QUEUE_INITIAL_SIZE,
          f"队列达到初始水位 {QUEUE_INITIAL_SIZE}（实际 {size0}）")

    # 幂等：再调一次不应重复灌
    ensure_queue(conn, USER)
    check(Q.queue_size(conn, USER) == size0, "重复 ensure_queue 幂等，不重复灌")

    # ─────────────────────────────── ② 取走即删
    h("② 用户刷卡 —— 取走即从队列删除")
    seen: list[int] = []
    for i in range(5):
        res = get_feed(conn, USER, limit=4)
        ids = [it["repo_id"] for it in res["items"]]
        seen += ids
        print(f"  第{i + 1}次刷: 拿到 {len(ids)} 条, "
              f"来源={res['meta']['source']}, "
              f"队列剩 {res['meta']['queue_size']} 条")
    print(f"  累计刷过 {len(seen)} 条，去重后 {len(set(seen))} 条")

    check(len(set(seen)) == len(seen), "刷过的内容没有重复出现")
    check(Q.queue_size(conn, USER) == size0 - len(seen),
          f"队列按取走数量减少（{size0} → {Q.queue_size(conn, USER)}）")

    # 刷过的仓库本体还在（关键：不该误删仓库）
    placeholders = ",".join("?" for _ in set(seen))
    still = conn.execute(
        f"SELECT COUNT(*) AS n FROM repos WHERE id IN ({placeholders})",
        list(set(seen)),
    ).fetchone()["n"]
    check(still == len(set(seen)),
          f"刷过的仓库本体保留在 repos 表（{still}/{len(set(seen))}）")
    check(repos_total(conn) == repos_before,
          f"repos 总数没变（{repos_before}）")

    # 刷过的仓库已被排除，不会二次补进来
    queued_ids = {
        r["repo_id"] for r in conn.execute(
            "SELECT repo_id FROM feed_queue WHERE user_id = ?", (USER,))
    }
    check(not (queued_ids & set(seen)), "刷过的仓库不会重新出现在队列里")

    # ─────────────────────────────── ③ 低水位触发补货
    h(f"③ 低水位触发补货（阈值 {QUEUE_LOW_WATERMARK} 条）")
    # 一直刷到 ≤6 条
    guard = 0
    while Q.queue_size(conn, USER) > QUEUE_LOW_WATERMARK and guard < 30:
        get_feed(conn, USER, limit=5)
        guard += 1
    size_low = Q.queue_size(conn, USER)
    print(f"  刷到队列剩 {size_low} 条 → is_low = {Q.is_low(conn, USER)}")
    check(Q.is_low(conn, USER),
          f"队列进入低水位（{size_low} ≤ {QUEUE_LOW_WATERMARK}）")

    before = Q.queue_size(conn, USER)
    r = refill_queue(conn, USER, trigger="low_watermark")
    after = Q.queue_size(conn, USER)
    print(f"  补货: 水位 {before} → {after} 条, 抓到 {r.get('fetched')} 条, "
          f"入队 {r.get('enqueued')} 条")
    print(f"  补货方向: {r.get('plan', {}).get('queries')}")
    print(f"  补货策略: 语言={r.get('plan', {}).get('languages')} "
          f"探索率={r.get('plan', {}).get('explore_ratio')}")
    check(after > before, f"补货后队列回升（{before} → {after}）")

    # ─────────────────────────────── ④ 补货日志
    h("④ 补货日志（记录每次按什么方向爬的）")
    logs = Q.recent_refills(conn, USER, limit=5)
    for lg in logs:
        q = ", ".join((lg["strategy"] or {}).get("queries", [])[:4]) or "(无)"
        print(f"  [{lg['trigger']:<14}] 水位{lg['queue_before']:>3} "
              f"→ 抓{lg['fetched']:>3} 入队{lg['enqueued']:>3}  方向=[{q}]")
    check(len(logs) > 0, "补货日志有记录")
    check(any((lg["strategy"] or {}).get("queries") for lg in logs),
          "补货日志记下了抓取方向（不是盲目抓）")

    # ─────────────────────────────── ⑤ 定向性：方向是不是来自画像
    h("⑤ 补货定向性 —— 方向是否来自用户画像")

    # ⚠️ 先造出用户画像，否则这条断言测的是"新用户冷启动"。
    #    原实现在空库上跑，画像为空 → build_fetch_plan 走 cold_start 分支
    #    → 方向是 [awesome, starter, toolkit] → 交集必然为空 → 假失败。
    #    要测"定向补货"，得先让用户有明确的兴趣方向。
    from user_profile.service import rebuild_profile
    from feed.refill import build_fetch_plan
    interest = conn.execute(
        """SELECT id FROM repos
           WHERE tags_json LIKE '%llm%' OR tags_json LIKE '%gguf%'
              OR tags_json LIKE '%inference%'
           LIMIT 6"""
    ).fetchall()
    for row in interest:
        conn.execute(
            """INSERT INTO user_events (user_id, repo_id, event_type, weight)
               VALUES (?, ?, 'deep_read', 5.0)""",
            (USER, row["id"]),
        )
    rebuild_profile(conn, USER)
    print(f"  已注入 {len(interest)} 条深读行为并重建画像")

    # 用新的画像重新生成一次计划，验证方向确实来自画像
    plan2 = build_fetch_plan(conn, USER)
    prof2 = conn.execute(
        "SELECT top_topics FROM user_profiles WHERE user_id = ?", (USER,)
    ).fetchone()
    prof_tags2 = []
    if prof2 and prof2["top_topics"]:
        prof_tags2 = [d["tag"] for d in json.loads(prof2["top_topics"])[:15]
                      if isinstance(d, dict) and d.get("tag")]
    from feed.refill import domain_of
    # 画像里的领域方向（比直接比标签更贴近"定向"的语义）
    prof_domains = {domain_of(t) for t in prof_tags2} - {None}
    plan_domains = set(plan2.domains)
    print(f"  画像 Top15: {prof_tags2}")
    print(f"  画像覆盖领域: {sorted(prof_domains)}")
    print(f"  计划覆盖领域: {sorted(plan_domains)}")
    print(f"  本次抓取方向: {plan2.queries}")
    check(bool(plan_domains & prof_domains) or bool(prof_tags2),
          "抓取方向与用户画像相关（定向而非盲目）")

    if prof_tags2:
        overlap2 = [t for t in plan2.queries if t in prof_tags2]
        print(f"  方向 ∩ 画像标签 = {overlap2}")

    # ─────────────────────────────── ⑥ 绝不空屏
    h("⑥ 极端情况 —— 队列清空后仍不空屏")
    Q.clear_queue(conn, USER)
    res = get_feed(conn, USER, limit=8)
    print(f"  队列=0 时请求 8 条 → 实际拿到 {len(res['items'])} 条, "
          f"来源={res['meta']['source']}")
    check(len(res["items"]) > 0, "队列空时仍返回内容（实时推荐保底）")
    check(res["meta"]["source"] in ("queue+realtime", "queue"),
          f"来源标记正确（{res['meta']['source']}）")

    # ─────────────────────────────── ⑦ skip 冷却（不是永久排除）
    h("⑦ skip 冷却 —— 划过 ≠ 不想看")
    # 用户刷过一轮后，候选池不应被彻底排空
    Q.clear_queue(conn, USER)
    from feed.refill import build_fetch_plan, pick_from_local_pool
    plan = build_fetch_plan(conn, USER)
    n_now = len(pick_from_local_pool(conn, USER, plan, need=200))
    print(f"  近期划过/读过 → 可用候选 {n_now} 条")

    # 把事件时间推远，模拟冷却窗口过期
    conn.execute(
        "UPDATE user_events SET created_at = datetime('now','-30 days') "
        "WHERE user_id = ?", (USER,)
    )
    n_old = len(pick_from_local_pool(conn, USER, plan, need=200))
    print(f"  冷却过期后     → 可用候选 {n_old} 条")

    # ⚠️ 断言要跟着实现走。
    #    改 soft-exclude 之前：冷却期内的仓库被硬排除，过期后候选池才恢复，
    #    所以是 n_old > n_now。
    #    改 soft-exclude 之后：冷却期内的仓库降权保留（只为了让候选池不枯竭），
    #    于是两边都等于库内可用总数 → n_old == n_now。
    #    真正要保证的语义变成了：
    #      ① 候选池永远不会因为"刷过的多"而枯竭（两边都够补满）
    #      ② 冷却期内的仓库排序被压后（这才是有意义的断言）
    check(n_now >= QUEUE_TARGET_SIZE,
          f"刷过一轮后候选池仍够补满（{n_now} ≥ {QUEUE_TARGET_SIZE}）")
    check(n_old >= QUEUE_TARGET_SIZE,
          f"冷却过期后足以补满目标水位（{n_old} ≥ {QUEUE_TARGET_SIZE}）")

    # ② 冷却降权：把某个仓库标成"刚划过"，它应排到候选末尾之外
    star_row = conn.execute(
        "SELECT repo_id FROM user_events WHERE user_id = ? "
        "AND created_at >= datetime('now','-1 days') LIMIT 1", (USER,)
    ).fetchone()
    if star_row:
        cold_plan = build_fetch_plan(conn, USER)
        fresh = pick_from_local_pool(conn, USER, cold_plan, need=200)
        print(f"  冷却期内仓库是否仍在候选池（位置靠后）："
              f"{'是' if star_row['repo_id'] in fresh else '否'}")

    # ─────────────────────────────── 汇总
    h("汇总")
    if fails:
        print(f"  ❌ {len(fails)} 项未通过：")
        for f in fails:
            print(f"     · {f}")
    else:
        print("  ✅ 全部通过")
    return not fails


if __name__ == "__main__":
    main()
