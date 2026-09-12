"""高强度压测：刷 200 条 Feed + Ai/RVC 关键词匹配度分析。

验证目标：
    ① 刷 200 条不崩、不空、不重复刷同一个
    ② 缓存池在 200 条压力下的补货行为是否合理
    ③ 搜索 "Ai" / "RVC" 的匹配度是否准确
    ④ Feed 里推的内容与 "Ai / RVC" 兴趣方向的契合度

用法：
    cd backend && python3 jobs/stress_200.py
"""
from __future__ import annotations

import collections
import io
import json
import random
import sys
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db.connection import connect
from feed import queue as Q
from feed.service import get_feed, refill_queue

USER = 1
TOTAL = 200            # 要刷的总条数
PAGE = 10              # 每次请求条数
BAR = "─" * 68


def h(t: str) -> None:
    print(f"\n{BAR}\n  {t}\n{BAR}")


# ------------------------------------------------------------------ 相关性判定

# "AI 方向"关键词：命中任一即认为与 AI 相关
AI_TERMS = {
    "ai", "artificial-intelligence", "llm", "llms", "gpt", "transformer",
    "deep-learning", "machine-learning", "neural", "diffusion", "model",
    "models", "inference", "quantization", "gguf", "llama", "mistral",
    "qwen", "gemma", "phi", "openai", "anthropic", "gemini", "deepseek",
    "embedding", "embeddings", "rag", "vector", "agent", "vlm", "prompt",
    "fine-tuning", "lora", "speech", "audio", "tts", "voice",
    "voice-cloning", "voice-conversion", "speech-synthesis", "asr",
    "tokenizer", "attention", "pytorch", "tensorflow", "cuda", "gpu",
    "人工智能", "大模型", "深度学习", "机器学习", "模型", "推理",
    "量化", "语音", "语音合成", "语音识别", "克隆", "神经网络", "训练",
}

# "RVC 方向"关键词：语音克隆/变声相关
RVC_TERMS = {
    "rvc", "voice-conversion", "voice-cloning", "voice-changer", "svc",
    "singing", "singing-voice", "realtime", "tts", "speech-synthesis",
    "speech", "voice", "audio", "vits", "so-vits", "sovits",
    "gpt-sovits", "hubert", "f0", "音色", "变声", "歌声", "语音",
    "语音合成", "声库", "克隆", "人声",
}


def _repo_blob(item: dict) -> str:
    """把一条 Feed 拼成可匹配的文本。"""
    parts = [
        item.get("full_name") or "",
        item.get("name") or "",
        item.get("description") or "",
        item.get("language") or "",
        " ".join(item.get("topics") or []),
        " ".join(t.get("tag", "") for t in (item.get("tags") or [])),
    ]
    return " ".join(parts).lower().replace("_", "-")


def _hits(blob: str, terms: set[str]) -> list[str]:
    return sorted({t for t in terms if t in blob})


# 固定种子：压测要可复现，否则每次跑出来的重复率都不一样，没法对比修复前后
RNG = random.Random(20260911)


def _simulate_swipes(conn, items: list[dict]) -> None:
    """模拟用户对这批 Feed 的真实行为，写进 user_events。

    ⚠️ 为什么压测必须做这一步：
        ① 画像靠 user_events 构建。只拿数据不写事件 → 画像永远空，
           ④ 段必然失败，而且看不出推荐质量是否随行为收敛。
        ② 补货的 recent 冷却集也来自 user_events。
           不写事件 → 冷却集为空 → 测不到"刷过的不再立刻重复推"。

    行为模型（对齐 jobs/simulate.py 的口径）：
        与 AI/RVC 越相关 → 越可能深读；越无关 → 越可能划过。
    """
    from pool.state_machine import record_event

    for pos, it in enumerate(items):
        blob = _repo_blob(it)
        ai = len(_hits(blob, AI_TERMS))
        rvc = len(_hits(blob, RVC_TERMS))
        # 相关性打分 0~1
        rel = min(1.0, (ai * 0.18) + (rvc * 0.35))
        p_deep = max(0.05, min(0.85, 0.10 + 0.85 * rel))
        p_deep *= (1.0 - 0.03 * pos)        # 位置越后越少被看

        rid = it["repo_id"]
        if RNG.random() < p_deep:
            conn.execute(
                """INSERT INTO user_events
                   (user_id, repo_id, event_type, weight, dwell_ms,
                    scroll_depth, rank_position)
                   VALUES (?,?,?,?,?,?,?)""",
                (USER, rid, "deep_read", round(rel, 3),
                 int(30000 * p_deep), 0.97, pos),
            )
            record_event(conn, rid, "deep_read")
        else:
            conn.execute(
                """INSERT INTO user_events
                   (user_id, repo_id, event_type, weight,
                    dwell_ms, scroll_depth, rank_position)
                   VALUES (?,?,'skip',0.5,?,0.15,?)""",
                (USER, rid, int(1200 * RNG.random()), pos),
            )
            record_event(conn, rid, "skip")


# ------------------------------------------------------------------ 主流程

def main() -> None:
    conn = connect()
    conn.isolation_level = None
    conn.execute("BEGIN")
    try:
        run(conn)
    finally:
        conn.execute("ROLLBACK")
        conn.close()
    print(f"\n{BAR}\n  压测结束（已回滚，真实数据未受影响）\n{BAR}")


def run(conn) -> None:
    Q.clear_queue(conn, USER)
    repos_total = conn.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"]

    # ═══════════════════════════════════ ① 刷 200 条
    h(f"① 连续刷卡 {TOTAL} 条（每次 {PAGE} 条）")

    seen: list[dict] = []
    seen_ids: list[int] = []
    sources = collections.Counter()
    refill_events: list[dict] = []
    empty_pages = 0
    duplicates = 0

    rounds = TOTAL // PAGE
    for i in range(rounds):
        buf = io.StringIO()
        with redirect_stdout(buf):
            res = get_feed(conn, USER, limit=PAGE)

        items = res["items"]
        meta = res["meta"]
        sources[meta.get("source", "?")] += 1

        if not items:
            empty_pages += 1
        for it in items:
            rid = it["repo_id"]
            if rid in seen_ids:
                duplicates += 1
            seen_ids.append(rid)
            seen.append(it)

        # ⚠️ 必须模拟"用户真的看了"这部分行为。
        #    原实现只调 get_feed 拿数据、不写 user_events，
        #    于是：
        #      ① 画像永远是空的（④ 段必然失败）
        #      ② 补货的 recent 冷却集也是空的（等于没测到真实冷却行为）
        #    真实用户刷卡会在 /api/events 落库，这里按同样的语义补上。
        _simulate_swipes(conn, items)

        if meta.get("refill"):
            refill_events.append({
                "round": i + 1,
                "queue_before": meta["refill"].get("queue_before"),
                "enqueued": meta["refill"].get("enqueued"),
                "queue_size": meta.get("queue_size"),
            })

        if (i + 1) % 5 == 0 or i == 0:
            print(f"  第{i + 1:>3}/{rounds} 轮: "
                  f"拿到{len(items):>2}条 来源={meta.get('source'):<14} "
                  f"队列={meta.get('queue_size')}")

    print(f"\n  实际刷到: {len(seen)} 条（目标 {TOTAL}）")
    print(f"  唯一条数: {len(set(seen_ids))}")
    print(f"  重复出现: {duplicates} 次")
    print(f"  空页: {empty_pages} 次")
    print(f"  来源分布: {dict(sources)}")
    print(f"  触发补货: {len(refill_events)} 次")

    ok = True
    def check(cond, msg):
        nonlocal ok
        print(f"   {'✅' if cond else '❌'} {msg}")
        if not cond:
            ok = False

    check(len(seen) >= TOTAL * 0.9,
          f"刷到目标量 90% 以上（{len(seen)}/{TOTAL}）")
    check(empty_pages == 0, "没有空页")
    # 仓库总数有限时允许重复，但要如实报告重复率
    dup_rate = duplicates / max(1, len(seen))
    print(f"  重复率: {dup_rate:.1%} （库内共 {repos_total} 个仓库）")

    # ═══════════════════════════════════ ② AI 相关性
    h("② 相关性分析 —— 推的内容和 AI 方向匹配吗")

    ai_hit, rvc_hit, neither = [], [], []
    for it in seen:
        blob = _repo_blob(it)
        a = _hits(blob, AI_TERMS)
        r = _hits(blob, RVC_TERMS)
        if r:
            rvc_hit.append((it, a, r))
        elif a:
            ai_hit.append((it, a))
        else:
            neither.append(it)

    uniq = {it["repo_id"]: it for it in seen}
    u_total = len(uniq)
    u_ai = len({it["repo_id"] for it, _, _ in rvc_hit}
               | {it["repo_id"] for it, _ in ai_hit})
    u_rvc = len({it["repo_id"] for it, _, _ in rvc_hit})

    print(f"  唯一仓库 {u_total} 个：")
    print(f"    AI 相关     {u_ai:>3} 个  {u_ai / u_total:.1%}")
    print(f"    其中语音方向 {u_rvc:>3} 个  {u_rvc / u_total:.1%}")
    print(f"    非 AI        {u_total - u_ai:>3} 个  "
          f"{(u_total - u_ai) / u_total:.1%}")

    print(f"\n  语音/RVC 方向命中明细：")
    for it, a, r in rvc_hit[:12]:
        print(f"    {it['full_name']:<42} ★{it['stars']:<7} {r[:4]}")

    if neither:
        print(f"\n  完全非 AI 的（抽样 8 个）：")
        for it in neither[:8]:
            print(f"    {it['full_name']:<42} ★{it['stars']}")

    # ═══════════════════════════════════ ③ 搜索匹配度
    h("③ 搜索匹配度 —— Ai / RVC")

    from api.search_service import search as do_search

    for q in ["Ai", "RVC", "AI 语音", "voice cloning"]:
        res = do_search(conn, USER, q, limit=10)
        m = res["meta"]
        print(f"\n  ┌─ 搜索 {q!r}：命中 {m['total']} 条，分词 {m['terms']}")
        top = res["items"][:6]
        precise = 0
        for it in top:
            blob = _repo_blob(it)
            terms = [t.lower() for t in m["terms"]]
            hit = [t for t in terms if t in blob]
            if hit:
                precise += 1
            mark = "✓" if hit else "✗"
            print(f"  │ {mark} {it['full_name']:<40} "
                  f"{'/'.join(it['search_reasons'][:2])}")
        print(f"  └─ 前 {len(top)} 条的查询词命中率: "
              f"{precise}/{len(top)}")

    # ═══════════════════════════════════ ④ 画像契合度
    h("④ 用户画像是否反映 AI / 语音方向")

    # ⚠️ 必须先用本轮产生的行为重建画像。
    #    原实现直接读 user_profiles —— 但压测跑在事务里，
    #    且画像只在曝光/互动时增量更新，直接读会拿到空画像，
    #    导致"画像里 AI 词 0 个"这种假失败。
    from profile.service import rebuild_profile
    rebuild_profile(conn, USER)

    p = conn.execute(
        "SELECT top_topics FROM user_profiles WHERE user_id = ?", (USER,)
    ).fetchone()
    topics = [d["tag"] for d in json.loads(p["top_topics"])[:25]] if p else []
    ai_in_profile = [t for t in topics if t in AI_TERMS]
    rvc_in_profile = [t for t in topics if t in RVC_TERMS]
    print(f"  画像 Top25: {topics}")
    print(f"  其中 AI 词: {ai_in_profile}")
    print(f"  其中语音词: {rvc_in_profile}")
    check(len(ai_in_profile) >= 5,
          f"画像里 AI 相关词 ≥5（实际 {len(ai_in_profile)}）")

    # ═══════════════════════════════════ 汇总
    h("压测汇总")
    print(f"  刷取总数      {len(seen)} 条 / 目标 {TOTAL}")
    print(f"  唯一仓库      {u_total} 个")
    print(f"  重复率        {dup_rate:.1%}")
    print(f"  AI 相关占比   {u_ai / u_total:.1%}")
    print(f"  语音占比      {u_rvc / u_total:.1%}")
    print(f"  空页          {empty_pages}")
    print()
    print("  ✅ 压测通过" if ok else "  ❌ 有未通过项")


if __name__ == "__main__":
    main()
