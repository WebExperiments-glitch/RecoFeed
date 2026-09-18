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
# ⭐ 个人分硬门槛：个人模型判定"不感兴趣"（p < 阈值）的仓库直接踢出，
#    不看它有多少 star、结构化质量分多高。
#    修的是用户实测到的"精分"：AI 写着"不合你的口味"，卡片却挂着 0.57 匹配度。
MIN_PERSONAL_SCORE = 0.15
# 全部被门槛拦掉时的兜底条数（宁可选"最不坏"的，也不能空屏）
FALLBACK_KEEP = 10


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
           get_repo_id, get_score, min_keep: int = 8,
           ) -> tuple[list[Any], dict[str, Any]]:
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

    # ⭐ 排除"已经有关系的仓库"，绝不推荐给用户：
    #    ① 自建仓库 —— 兴趣证据，不是内容（用户当然知道自己写的项目）
    #    ② 已收藏/点过星的 —— 用户已经收下了，再推就是浪费一次曝光（用户原话：
    #       "给我推荐我收藏的仓库干嘛"）
    #    ③ 站内点赞过的同理
    try:
        exclude_names: set[str] = set()
        for r in conn.execute(
            "SELECT full_name FROM github_footprint WHERE user_id = ?", (user_id,)
        ):
            exclude_names.add(str(r["full_name"]))

        exclude_ids: set[int] = set()
        for tbl in ("stars", "likes"):
            try:
                for r in conn.execute(
                    f"SELECT repo_id FROM {tbl} WHERE user_id = ?", (user_id,)
                ):
                    exclude_ids.add(int(r["repo_id"]))
            except Exception:
                pass

        if items:
            marks = ",".join("?" * len(items))
            name_by_id = {
                int(r["id"]): str(r["full_name"])
                for r in conn.execute(
                    f"SELECT id, full_name FROM repos WHERE id IN ({marks})",
                    tuple(int(get_repo_id(it)) for it in items))
            }
            items = [
                it for it in items
                if int(get_repo_id(it)) not in exclude_ids
                and name_by_id.get(int(get_repo_id(it)), "") not in exclude_names
            ]
            info["excluded_engaged"] = True
    except Exception:
        pass

    # ⭐ 硬门槛：个人分低于阈值的一律踢掉（不再参与排序，也不会出现在 Feed 头部）
    #
    #    ⚠️ 但"**没有个人分**" ≠ "个人分是 0"：
    #       新抓进来的仓库在向量化完成前没有分数，旧逻辑用 scores.get(id, 0.0)
    #       把它们当成"明确不感兴趣"直接踢掉 —— 结果是**新仓库永远出不来**，
    #       补货越勤越无效（实测定向抓了 611 个遗珠，遗珠档还是只剩 4 张）。
    #       这里把"未打分"与"低分"区分开：未打分的**默认放过**（用中性分参与排序）。
    def _p(it):
        """返回个人分；未打分为 None。"""
        v = scores.get(get_repo_id(it))
        return None if v is None else float(v)

    UNSCORED_NEUTRAL = 0.5      # 未打分的中性分（既不顶到最前，也不沉到底）
    kept = [it for it in items
            if _p(it) is None or _p(it) >= MIN_PERSONAL_SCORE]
    dropped = len(items) - len(kept)
    if not kept:
        # 全军覆没说明这批候选与该用户完全不对路：宁可给"最接近的几条"，
        # 也不能返回空屏（空屏会让用户以为服务坏了）。
        kept = sorted(items,
                      key=lambda it: -(_p(it) if _p(it) is not None else UNSCORED_NEUTRAL)
                      )[:FALLBACK_KEEP]
        info["fallback"] = True

    # 未打分的仓库用中性分参与融合排序（否则 .get(id, 0) 会把它们一律沉底）
    scores_for_blend = dict(scores)
    for it in kept:
        rid = get_repo_id(it)
        if rid not in scores_for_blend:
            scores_for_blend[rid] = UNSCORED_NEUTRAL
    ranked = blend_and_sort(kept, scores_for_blend, get_repo_id, get_score)

    # ⭐ 尾部兜底：门槛踢完后如果不够一页，用"个人分最高的被踢项"补满（排在末尾）。
    #
    #    ⚠️ 2026-09-18 调整：原来"只关不填"会让页只剩 1~4 张（实测遗珠档只剩 1 张），
    #       体验更差。改为**软下限**：只补个人分 ≥ SOFT_FLOOR 的 ——
    #       那些 p≈0.01 的"明确不合口味"永远进不来（它们正是评测里被吐槽的
    #       "写着'可能不合你的口味'还硬推给我"的卡片），但分数只是略低一点的仍可补位。
    SOFT_FLOOR = 0.05
    filled = 0
    if len(ranked) < min_keep:
        passed = {get_repo_id(it) for it in ranked}
        filler = sorted(
            (it for it in items
             if get_repo_id(it) not in passed
             and (_p(it) is None or _p(it) >= SOFT_FLOOR)),
            key=lambda it: -(_p(it) if _p(it) is not None else UNSCORED_NEUTRAL),
        )[: max(0, min_keep - len(ranked))]
        ranked = ranked + filler
        filled = len(filler)

    info.update({
        "applied": True,
        "scored": len(scores),
        "kept": len(kept),
        "dropped": dropped,
        "filled_from_dropped": filled,
        "threshold": MIN_PERSONAL_SCORE,
        "blend": BLEND_PERSONAL,
        "scores": {get_repo_id(it): round(scores_for_blend[get_repo_id(it)], 3)
                   for it in ranked},
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
