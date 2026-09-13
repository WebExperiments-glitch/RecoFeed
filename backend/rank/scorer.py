"""精排打分。

公式（迁移自抖音公开的 2026 权重体系）：

    推荐得分 = 多目标行为预估 + 增速加成 + 遗珠加成
               × 时效衰减
               × (1 + 个性化匹配)
               × 多样性惩罚
               − 负反馈惩罚

⚠️ 核心设计决策（实测支撑）：
   抖音 2026 已把完播率从 35% 降到不足 10%，把收藏率提到 25%。
   仓库场景下 "收藏" 就是 GitHub Star，且可真实校验。
   因此本项目以 **Star 转化率为第一核心指标**，而非停留时长。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.config import RANK_WEIGHTS
from user_profile.builder import TagVector, tag_overlap


def _hours_since(ts: str | None) -> float:
    """距现在多少小时。解析失败返回一个很大的数（= 很旧）。"""
    if not ts:
        return 24 * 365.0
    try:
        t = ts.replace("Z", "+00:00")
        dt = datetime.fromisoformat(t)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return 24 * 365.0
    delta = datetime.now(timezone.utc) - dt
    return max(0.0, delta.total_seconds() / 3600.0)


@dataclass
class Candidate:
    """候选仓库。"""

    repo_id: int
    full_name: str
    owner: str
    name: str
    description: str | None = None
    language: str | None = None
    stars: int = 0
    topics: list[str] = field(default_factory=list)
    tags: dict[str, float] = field(default_factory=dict)

    # 来自 repo_pools 的统计
    impressions: int = 0
    clicks: int = 0
    deep_reads: int = 0
    likes: int = 0
    stars_gained: int = 0
    comments: int = 0
    shares: int = 0
    follows: int = 0

    # 预计算的分
    quality_score: float = 0.0
    velocity_score: float = 0.0
    forgotten_score: float = 0.0
    freshness_score: float = 0.0
    pushed_at: str | None = None
    created_at: str | None = None

    # 归因
    channel: str = ""
    source_channels: list[str] = field(default_factory=list)

    # 打分输出
    score: float = 0.0
    score_detail: dict[str, float] = field(default_factory=dict)

    # ---- 率值（均可从统计推导）----
    @property
    def ctr(self) -> float:
        return self.clicks / self.impressions if self.impressions else 0.0

    @property
    def deep_rate(self) -> float:
        """深读率 ≈ 完播率。"""
        return self.deep_reads / self.impressions if self.impressions else 0.0

    @property
    def star_rate(self) -> float:
        """Star 转化率 ≈ 收藏率（第一核心指标）。"""
        return self.stars_gained / self.impressions if self.impressions else 0.0

    @property
    def like_rate(self) -> float:
        return self.likes / self.impressions if self.impressions else 0.0

    @property
    def comment_rate(self) -> float:
        return self.comments / self.impressions if self.impressions else 0.0

    @property
    def share_rate(self) -> float:
        return self.shares / self.impressions if self.impressions else 0.0

    @property
    def follow_rate(self) -> float:
        return self.follows / self.impressions if self.impressions else 0.0


# ------------------------------------------------------------------ 打分项

def engagement_score(c: Candidate,
                     weights: dict[str, float] | None = None) -> float:
    """多目标行为预估（用历史实际率代替模型预测）。

    对应抖音 2026 权重：收藏 > 复访 > 完播 > 评论 > 点赞 > 转发。
    """
    w = weights or RANK_WEIGHTS
    return (
        w["w_star"] * c.star_rate
        + w["w_deep_read"] * c.deep_rate
        + w["w_comment"] * c.comment_rate
        + w["w_share"] * c.share_rate
        + w["w_follow"] * c.follow_rate
        + w["w_like"] * c.like_rate
    )


def recency_decay(c: Candidate,
                  weights: dict[str, float] | None = None) -> float:
    """时效衰减：越新分越高。

    ⭐ 修正的设计缺陷：原实现 exp(-0.08 · 天数) 无下界。
       实测一个 700 天未 push 的仓库，衰减因子 = e⁻⁵⁶ ≈ 5e-25，
       最终得分 1e-29 —— 而"久未更新"恰恰是遗珠召回要救的对象。
       原实现把本项目唯一存在的理由给抹掉了：
       实测 11 个遗珠仓库全部 rec=0，得分被乘成 0，一个都进不了 Feed。

       修正：分段衰减 + 下界。
         - 90 天内：保持指数衰减（正常时效排序）
         - 超过 90 天：切换为极缓尾巴，且保底 0.35
       保底 0.35 的含义：老仓库最多损失 65% 时效分，
       不再归零，仍能靠 quality / forgotten 竞争。
    """
    w = weights or RANK_WEIGHTS
    lam = w["recency_decay"]
    days = _hours_since(c.pushed_at) / 24.0

    knee = w.get("recency_knee_days", 90.0)
    if days <= knee:
        return math.exp(-lam * days)

    knee_val = math.exp(-lam * knee)
    tail = math.exp(-w.get("recency_tail_decay", 0.0015) * (days - knee))
    floor = w.get("recency_floor", 0.35)
    return max(floor, knee_val * tail)


def velocity_boost(c: Candidate,
                   weights: dict[str, float] | None = None) -> float:
    """Star 增速加成。

    ⭐ 关键：用"增速"而非"总量"，解决老牌大项目霸榜问题。
    对数压缩防止超级项目分数爆炸。
    """
    w = weights or RANK_WEIGHTS
    return math.log1p(max(0.0, c.velocity_score)) * w["velocity_boost"]


def forgotten_boost(c: Candidate,
                    weights: dict[str, float] | None = None) -> float:
    """⭐ 遗珠加成。

    本项目与商业平台最大的区别：
    商业平台让热门更热，我们给"优质但被埋没"的仓库加分。
    """
    w = weights or RANK_WEIGHTS
    return c.forgotten_score * w["forgotten_boost"]


def user_affinity(profile: TagVector, c: Candidate) -> float:
    """个性化匹配：用户画像标签与仓库标签的重合度。"""
    if not profile.weights or not c.tags:
        return 0.0
    return tag_overlap(profile.weights, c.tags)


def diversity_penalty(session_languages: list[str],
                      session_owners: list[str],
                      c: Candidate,
                      lang_soft_limit: int = 3,
                      owner_soft_limit: int = 2) -> float:
    """多样性惩罚：避免同一语言/作者连续刷屏。

    返回 ≤1 的乘性系数。
    """
    penalty = 1.0

    if c.language and session_languages.count(c.language) >= lang_soft_limit:
        penalty *= 0.75
    elif c.language and session_languages.count(c.language) >= 2:
        penalty *= 0.92

    if c.owner and session_owners.count(c.owner) >= owner_soft_limit:
        penalty *= 0.7

    return penalty


def freshness_boost(c: Candidate) -> float:
    """新鲜度（新仓库专享加成）。"""
    return c.freshness_score * 0.5


# ------------------------------------------------------------------ 主入口

def score_candidate(
    c: Candidate,
    profile: TagVector,
    *,
    penalties: dict[str, float] | None = None,
    session_languages: list[str] | None = None,
    session_owners: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> float:
    """计算单个候选的最终得分，同时写入 score_detail。"""
    w = weights or RANK_WEIGHTS

    eng = engagement_score(c, w)
    vel = velocity_boost(c, w)
    forg = forgotten_boost(c, w)
    fresh = freshness_boost(c)
    rec = recency_decay(c, w)
    aff = user_affinity(profile, c)
    div = diversity_penalty(
        session_languages or [], session_owners or [], c
    )

    base = eng + vel + forg + fresh

    # ⭐ 兜底：base 可能为 0（例如所有分项都极小）。
    #    给一个由 quality_score 支撑的下限，保证任何候选都"可被展示"。
    #    否则 base=0 时无论 rec / aff 多大，乘积都是 0，直接等于永久淘汰。
    base = max(base, c.quality_score * w.get("base_floor_ratio", 0.05))

    score = base * rec * (1 + aff) * div

    # 负反馈惩罚（作用在标签上，而非仓库上）
    # ⚠️ 注意：惩罚是【减性】的，必须防止把分数减成负数后又被 max(0) 夹紧，
    #    那样多个标签的惩罚强度差异会消失（全变 0）。
    #    这里改为按比例削减，保留相对差异。
    pen_total = 0.0
    if penalties and c.tags:
        for tag in c.tags:
            pen_total += penalties.get(tag, 0.0)

    if pen_total > 0 and score > 0:
        # 惩罚以乘性折损形式作用，下限 0.05（最多削掉 95%）
        factor = max(0.05, 1.0 - pen_total)
        score *= factor
    score = max(0.0, score)

    c.score = score
    c.score_detail = {
        "engagement": round(eng, 4),
        "velocity": round(vel, 4),
        "forgotten": round(forg, 4),
        "freshness": round(fresh, 4),
        "recency": round(rec, 4),
        "affinity": round(aff, 4),
        "diversity": round(div, 4),
        "penalty": round(-pen_total, 4),
        "final": round(score, 4),
    }
    return score
