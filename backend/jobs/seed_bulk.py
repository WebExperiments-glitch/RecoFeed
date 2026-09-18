"""批量扩库：从 GitHub 抓取上千个仓库，喂饱推荐池与个人模型。

为什么需要
    语料库只有 300 个仓库时，个人模型再准也没得挑 —— 数字人类实验早已证明
    "候选池枯竭"是当前最大的瓶颈（命中率峰值后回落）。
    这里把语料库一次拉到 2000+。

用法
    python backend/jobs/seed_bulk.py --target 2000
    python backend/jobs/seed_bulk.py --target 500 --pages 1        # 快速试跑
    python backend/jobs/seed_bulk.py --target 2000 --embed         # 抓完顺便重建向量

要点
- **认证令牌**：搜索限流从 10 次/分钟提升到 30 次/分钟（令牌取 `gh auth token`）
- 每个关键词按 stars 降序翻页，每页 100 条；跨关键词去重
- 入库口径复用 crawl_service（_normalize / _insert_repo / ensure_pool），
  与网页端爬虫完全一致，不产生第二套数据标准
- 关键词来源：用户画像标签 + 领域方向词 + 一批 topic 查询（保证覆盖面）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import requests

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from api.crawl_service import _insert_repo, _normalize  # noqa: E402
from db.connection import get_conn  # noqa: E402
from pool.state_machine import ensure_pool  # noqa: E402

SEARCH_URL = "https://api.github.com/search/repositories"

# 覆盖面：语言维度 + 热门 topic + 通用高质量
# （用 topic:xxx 语法，GitHub 会返回该话题下星数最高的仓库）
BASE_QUERIES: list[str] = [
    # 语言维度（按星数取样，保证长尾之外的广度）
    "language:python stars:>2000",
    "language:typescript stars:>2000",
    "language:rust stars:>1000",
    "language:go stars:>1000",
    "language:cpp stars:>1000",
    "language:java stars:>2000",
    "language:javascript stars:>2000",
    "language:csharp stars:>1000",
    "language:swift stars:>500",
    "language:kotlin stars:>500",
    # 方向维度
    "topic:llm",
    "topic:agent",
    "topic:rag",
    "topic:mcp",
    "topic:voice-cloning",
    "topic:text-to-speech",
    "topic:diffusion-models",
    "topic:quantization",
    "topic:inference-engine",
    "topic:embedding",
    "topic:vector-database",
    "topic:web-scraping",
    "topic:automation",
    "topic:developer-tools",
    "topic:cli",
    "topic:dataset",
    "topic:computer-vision",
    "topic:speech-recognition",
    "topic:music-generation",
    "topic:audio-processing",
    "topic:prompt-engineering",
    "topic:fine-tuning",
    "topic:model-compression",
    "topic:edge-computing",
    "topic:desktop-app",
    "topic:chrome-extension",
    "topic:vscode-extension",
    "topic:observability",
    "topic:data-visualization",
    "topic:self-hosted",
]


# ⭐ 遗珠档查询：**低星**仓库（50~999 星）。
#    产品定位就是"让被埋没的好项目浮上来"，所以语料库必须有一批低星仓库；
#    之前只跑了 stars:>2000 的查询，结果 2361 个里只有 60 个是 <1000 星，
#    "遗珠"筛选形同虚设。
GEM_QUERIES: list[str] = [
    f"stars:50..999 {q}" for q in [
        "topic:tts", "topic:voice-cloning", "topic:llm", "topic:agent",
        "topic:rag", "topic:mcp", "topic:embedding", "topic:inference-engine",
        "topic:quantization", "topic:fine-tuning", "topic:speech-synthesis",
        "topic:audio-processing", "topic:music-generation", "topic:web-scraping",
        "topic:cli-tool", "topic:developer-tools", "topic:automation",
        "topic:desktop-app", "topic:self-hosted", "topic:dataset",
        "language:python", "language:typescript", "language:rust", "language:go",
    ]
]


def _token(explicit: str | None) -> str:
    if explicit:
        return explicit
    try:
        return subprocess.run(["gh", "auth", "token"], capture_output=True,
                              text=True, timeout=15).stdout.strip()
    except Exception:
        return ""


def _search(query: str, token: str, page: int, per_page: int) -> list[dict]:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "recofeed-seed"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = requests.get(
            SEARCH_URL,
            params={"q": query, "per_page": per_page, "page": page,
                    "sort": "stars", "order": "desc"},
            headers=headers, timeout=30, verify=False,
        )
        if r.status_code != 200:
            print(f"    ⚠️ {query} p{page} → HTTP {r.status_code}")
            return []
        return r.json().get("items", []) or []
    except Exception as e:  # noqa: BLE001
        print(f"    ⚠️ {query} p{page} → {type(e).__name__}")
        return []


def main() -> None:
    ap = argparse.ArgumentParser(description="批量扩库（目标 2000+ 仓库）")
    ap.add_argument("--target", type=int, default=2000, help="目标新增仓库数")
    ap.add_argument("--pages", type=int, default=3, help="每个关键词翻几页(每页100)")
    ap.add_argument("--per-page", type=int, default=100)
    ap.add_argument("--sleep", type=float, default=2.2,
                    help="请求间隔秒（认证后上限 30 次/分钟，2.2s 稳妥）")
    ap.add_argument("--token", default=None, help="GitHub 令牌（默认取 gh auth token）")
    ap.add_argument("--embed", action="store_true", help="抓完重建向量与画像")
    ap.add_argument("--set", choices=["main", "gem"], default="main",
                    help="main=高星广度（默认）；gem=遗珠档（50~999 星）")
    ap.add_argument("--from-profile", action="store_true",
                    help="按用户画像标签定向抓遗珠（低星 × 你的兴趣方向）。"
                         "⚠️ 通用 topic 抓来的低星仓库往往与你的兴趣无关，"
                         "个人分全被门槛滤掉 → 遗珠档只剩一两张。")
    args = ap.parse_args()

    token = _token(args.token)
    print(f"令牌：{'已获取' if token else '❌ 未获取（限流 10 次/分钟，速度会慢）'}")

    # 关键词 = 基础查询 + 用户画像标签（让扩库也带一点个人倾向）
    queries = list(GEM_QUERIES if args.set == "gem" else BASE_QUERIES)
    if args.set == "gem":
        print("模式：遗珠档（50~999 星）—— 填充「遗珠」筛选的候选池")

    if args.from_profile:
        # ⭐ 按画像定向：把"用户的兴趣方向"和"低星"这两个条件叠起来
        prof: list[str] = []
        try:
            import json as _json
            with get_conn() as _c:
                row = _c.execute(
                    "SELECT top_topics FROM user_profiles WHERE user_id = 3"
                ).fetchone()
                if row and row["top_topics"]:
                    prof = [t["tag"] for t in _json.loads(row["top_topics"])][:12]
        except Exception as e:  # noqa: BLE001
            print(f"（读画像失败：{type(e).__name__}）")
        if prof:
            top = max(50, args.target // max(1, len(prof)) // 3)
            queries = [f"stars:50..999 {t}" if " " not in t and t.isascii() else t
                       for t in prof] + [f"stars:50..999 topic:{t}" for t in prof if "-" in t]
            print(f"按画像定向（{len(queries)} 个查询，每查询最多取 {top} 个左右）：{prof}")
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT top_topics FROM user_profiles WHERE user_id = 3"
            ).fetchone()
            if rows and rows["top_topics"]:
                import json
                tags = [t["tag"] for t in json.loads(rows["top_topics"])][:8]
                queries += [f"topic:{t}" for t in tags if "-" in t or t.isascii()]
                print(f"追加画像方向词：{tags[:8]}")
    except Exception as e:  # noqa: BLE001
        print(f"（画像关键词跳过：{type(e).__name__}）")

    print(f"计划：{len(queries)} 个查询 × {args.pages} 页 × {args.per_page} 条，"
          f"目标新增 {args.target} 个仓库\n")

    seen: set[str] = set()
    added = 0
    updated = 0
    skipped = 0
    t0 = time.time()

    with get_conn() as conn:
        existing = {r["full_name"] for r in conn.execute("SELECT full_name FROM repos")}
        print(f"库内现有 {len(existing)} 个仓库\n")

        for qi, q in enumerate(queries, 1):
            if added >= args.target:
                break
            for page in range(1, args.pages + 1):
                if added >= args.target:
                    break
                items = _search(q, token, page, args.per_page)
                if not items:
                    break
                for it in items:
                    fn = it.get("full_name")
                    if not fn or fn in seen:
                        continue
                    seen.add(fn)
                    norm = _normalize(it, strict=True)
                    if not norm:
                        skipped += 1
                        continue
                    try:
                        rid = _insert_repo(conn, norm)
                    except Exception as e:  # noqa: BLE001
                        # 单个仓库入库失败不该打断整轮（实测 github_id 唯一冲突曾中断爬虫）
                        print(f"      ⚠️ 入库失败 {fn}：{type(e).__name__}")
                        skipped += 1
                        continue
                    if not rid:
                        skipped += 1
                        continue
                    ensure_pool(conn, rid)
                    if fn in existing:
                        updated += 1
                    else:
                        added += 1
                        existing.add(fn)
                print(f"  [{qi}/{len(queries)}] {q[:38]:<40} 第{page}页 → "
                      f"新增 {added} / 目标 {args.target}（合格跳过 {skipped}）")
                time.sleep(args.sleep)

    dur = time.time() - t0
    print(f"\n✓ 抓取结束：新增 {added} 个，更新 {updated} 个，"
          f"不合格跳过 {skipped} 个，用时 {dur/60:.1f} 分钟")
    with get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM repos").fetchone()["n"]
    print(f"✓ 语料库现在 {n} 个仓库")

    if args.embed:
        print("\n→ 重建向量与画像（个人模型才能用上新仓库）…")
        from ml import embedder
        from user_profile.service import rebuild_profile
        with get_conn() as conn:
            stats = embedder.build_all(conn)
            print(f"  向量库 +{stats['embedded']} 条（共 {stats['total']} 个仓库）")
            for u in (1, 2, 3):
                rebuild_profile(conn, u)
        print("  画像已重建。个人模型可执行：")
        print("  python backend/jobs/train_personal_model.py --user 3")


if __name__ == "__main__":
    main()
