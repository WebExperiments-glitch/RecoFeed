"""本地重排（Local Re-ranker）：让个人模型决定最终看到的 10 条。

两段式（业界标准范式，搬到本地单机）：

    召回（宽）  —— 多路召回给出 50 个候选
                     · 缓存池队列（补货时的定向抓取结果）
                     · TF-IDF 标签匹配
                     · embedding 余弦相似度（用户向量 vs 仓库向量）
    精排（准）  —— personal_model_u{id}.pth 对这 50 个打分，取 Top10

为什么重排有用：结构化分数只知道"这个仓库质量高不高"，
个人模型知道"**你**会不会喜欢它" —— 例如 ComfyUI 星数高，
但模型看到你最近在写 indexTTS，会把那个 300 star 的音频项目排到前面。

降级：没有个人模型 → 完全退回原有排序（不改变行为）；
      没有 embedding → 只有队列+TF-IDF 召回，重排跳过。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ml import embedder, user_model

# 混合比例：个人模型分数 vs 原有结构化分数（0.7 = 更信个人模型）
BLEND_PERSONAL = 0.7
# 候选池大小（召回后进入精排的条数）
RERANK_POOL = 50


def blend_and_sort(items: list[Any], personal_scores: dict[int, float],
                   get_repo_id, get_score, *, blend: float = BLEND_PERSONAL) -> list[Any]:
    """把个人模型分数与结构化分数融合排序。

    ⚠️ 两边量纲不同，必须先各自 min-max 归一到 0~1 再融合，
    否则"结构化分 1.19 vs 概率 0.83"这种直接相加毫无意义。
    """
    if not items:
        return items
    if not personal_scores:
        return items

    struct = [(get_repo_id(it), float(get_score(it) or 0.0)) for it in items]
    vals = [s for _, s in struct]
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
    span = (hi - lo) or 1.0

    def final(it: Any) -> float:
        rid = get_repo_id(it)
        s_norm = (float(get_score(it) or 0.0) - lo) / span
        p = personal_scores.get(rid)
        if p is None:
            return s_norm          # 没向量的仓库只按结构化分排（不惩罚，但不给优惠）
        return blend * p + (1 - blend) * s_norm

    return sorted(items, key=lambda it: -final(it))


def rerank(conn: sqlite3.Connection, user_id: int, items: list[Any],
           get_repo_id, get_score) -> tuple[list[Any], dict[str, Any]]:
    """对候选做本地精排；没有模型就原样返回。"""
    info: dict[str, Any] = {"applied": False}
    if not items:
        return items, info
    if user_model.model_path(user_id) is None or not user_model.model_path(user_id).exists():
        info["reason"] = "no_personal_model"
        return items, info

    ids = [get_repo_id(it) for it in items]
    scores = user_model.score_repos(conn, user_id, ids)
    if not scores:
        info["reason"] = "no_embeddings_or_model_failed"
        return items, info

    ranked = blend_and_sort(items, scores, get_repo_id, get_score)
    info.update({
        "applied": True,
        "scored": len(scores),
        "blend": BLEND_PERSONAL,
        "top": [{"repo_id": get_repo_id(it),
                 "personal": round(scores.get(get_repo_id(it), -1), 3)}
                for it in ranked[:5]],
    })
    return ranked, info


def recall_by_embedding(conn: sqlite3.Connection, user_id: int,
                        k: int = RERANK_POOL) -> list[tuple[int, float]]:
    """向量召回：用用户向量在全部仓库里找最相近的 k 个。"""
    u, _ = user_model.build_user_vector(conn, user_id)
    if u is None:
        return []
    return embedder.cosine_topk(conn, u, k=k)
