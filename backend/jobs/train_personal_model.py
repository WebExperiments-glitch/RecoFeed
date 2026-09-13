"""一键训练你的本地个人推荐模型（双塔 + 本地微调 + 重排体检）。

流程：
    ① 物品塔：给全部仓库生成语义向量（本地 embedding，增量、可重跑）
    ② 用户塔：把自建仓库(0.5)/点星仓库(0.4)/深读点赞(0.3) 加权成用户向量
    ③ 微调：训练 512→128→1 的个人 MLP（CPU 100 epoch，通常 <3 秒）
    ④ 体检：留出集 AUC/准确率 + 重排前后对比（让你看到"模型到底学到了什么"）
    ⑤ 产出：backend/models/personal_model_u{user_id}.pth（几百 KB）

用法：
    python backend/jobs/train_personal_model.py --user 3
    python backend/jobs/train_personal_model.py --user 3 --rebuild-embeddings   # 重建向量
    python backend/jobs/train_personal_model.py --user 3 --embed-only           # 只向量化
    python backend/jobs/train_personal_model.py --user 1 --epochs 200
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from db.connection import get_conn  # noqa: E402
from ml import embedder, rerank, user_model  # noqa: E402


def step_embeddings(conn, rebuild: bool, limit: int | None) -> None:
    print("① 物品塔：本地向量化")
    if rebuild:
        print("   （--rebuild-embeddings：先清空已有向量）")
    if not embedder.model_available():
        print("   ⚠️ 未检测到 sentence-transformers，将使用 LSA(TF-IDF+SVD) 兜底向量")

    def prog(done: int, total: int) -> None:
        print(f"   进度 {done}/{total}", end="\r", flush=True)

    stats = embedder.build_all(conn, force=rebuild, limit=limit, progress=prog)
    print(" " * 40, end="\r")
    print(f"   向量库：{embedder.count_embedded(conn)} 条 / 共 {stats['total']} 个仓库"
          f"（dim={stats['dim']}，后端={stats.get('backend', 'st')}，本次新增 {stats['embedded']}）")


def step_demo_rerank(conn, user_id: int, top: int = 5) -> None:
    """重排体检：拿向量召回的候选，看结构化排序 vs 个人模型排序的差异。"""
    print("\n④ 重排体检（向量召回 → 个人模型精排 vs 结构化排序）")
    cands = rerank.recall_by_embedding(conn, user_id, k=50)
    if not cands:
        print("   （没有用户向量，跳过）")
        return

    rows = []
    for rid, cos in cands:
        r = conn.execute(
            """SELECT full_name, stars, forgotten_score, quality_score
               FROM repos WHERE id = ?""", (rid,)
        ).fetchone()
        if r:
            rows.append({"repo_id": rid, "full_name": r["full_name"],
                         "stars": r["stars"], "cos": round(cos, 3),
                         "struct": r["quality_score"]})
    personal = user_model.score_repos(conn, user_id, [r["repo_id"] for r in rows])

    def fake_get(it):
        return it["repo_id"]

    def fake_score(it):
        return it["struct"]

    blended = rerank.blend_and_sort(rows, personal, fake_get, fake_score)

    print("   —— 结构化排序 Top5（原逻辑）——")
    for it in sorted(rows, key=lambda x: -x["struct"])[:top]:
        print(f"     {it['struct']:.2f}  ★{it['stars']:<7} cos={it['cos']:.2f}  {it['full_name']}")
    print("   —— 个人模型重排 Top5（你的专属排序）——")
    for it in blended[:top]:
        p = personal.get(it["repo_id"], -1)
        print(f"     p={p:.2f}  ★{it['stars']:<7} cos={it['cos']:.2f}  {it['full_name']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="训练本地个人推荐模型")
    ap.add_argument("--user", type=int, default=1, help="用户 id")
    ap.add_argument("--epochs", type=int, default=user_model.EPOCHS_DEFAULT)
    ap.add_argument("--lr", type=float, default=user_model.LR_DEFAULT)
    ap.add_argument("--rebuild-embeddings", action="store_true")
    ap.add_argument("--embed-only", action="store_true", help="只做向量化，不训练")
    ap.add_argument("--limit", type=int, default=None, help="向量化条数上限（调试用）")
    ap.add_argument("--no-demo", action="store_true", help="跳过重排体检")
    args = ap.parse_args()

    print(f"=== 本地个人推荐模型训练（user_id={args.user}）===\n")
    with get_conn() as conn:
        step_embeddings(conn, args.rebuild_embeddings, args.limit)
        if args.embed_only:
            return

        u, vinfo = user_model.build_user_vector(conn, args.user)
        print("\n② 用户塔：聚合用户证据")
        if u is None:
            print(f"   ✗ {vinfo.get('note')}")
            return
        print(f"   证据仓库 {vinfo['evidence']} 个（{vinfo['by_source']}），"
              f"可用 {vinfo['usable']} 个，权重和 {vinfo['weight_sum']}，向量 dim={vinfo['dim']}")

        print("\n③ 微调个人模型（512→128→1 MLP，CPU）")
        res = user_model.train(conn, args.user, epochs=args.epochs, lr=args.lr)
        if not res.get("ok"):
            print(f"   ✗ {res.get('reason')}")
            print(f"   数据情况：{res.get('data')}")
            return
        m = res["metrics"]
        print(f"   样本 {m['n']} 条（正 {m['pos']} / 负 {m['neg']}），"
              f"训练 {m['seconds']}s / {m['epochs']} epoch")
        if "auc_holdout" in m:
            print(f"   留出集：AUC {m['auc_holdout']}  准确率 {m['acc_holdout']}"
                  f"（{m['n_holdout']} 条）")

        if not args.no_demo:
            step_demo_rerank(conn, args.user)

    print("\n✓ 完成。个人模型文件里装着你这个用户的「兴趣灵魂」。")


if __name__ == "__main__":
    main()
