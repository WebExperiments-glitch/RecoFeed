"""用户画像构建（三层来源）。

能力圈（自建仓库）×1.0  —— 自己写的，最能代表技能
兴趣圈（Star 仓库）×0.6  —— 认可的，代表品味
注意力圈（完读）×0.3     —— 愿意花时间的

⚠️ 关键修正：自建仓库必须降噪。
   用户的仓库列表里往往混杂练手项目（hello-world / tutorial / fork 的课程作业），
   不过滤会严重污染"能力圈"。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from core.config import PROFILE_SOURCE_WEIGHTS, TUTORIAL_PATTERNS

# ------------------------------------------------------------------ 降噪

_TUTORIAL_RE = re.compile(
    "|".join(re.escape(p) for p in TUTORIAL_PATTERNS),
    re.I,
)


def is_tutorial_like(name: str, description: str | None = None) -> bool:
    """判断是否像练手/教程类仓库。"""
    text = f"{name} {description or ''}"
    return bool(_TUTORIAL_RE.search(text))


def owned_repo_weight(
    *,
    is_fork: bool,
    stars: int,
    size_kb: int,
    has_ci: bool,
    name: str,
    description: str | None = None,
    recent_commits: int = 99,
) -> float:
    """自建仓库的权重（含降噪）。

    返回 0 表示不计入画像。
    """
    # fork 的不算能力圈（是别人的代码）
    if is_fork:
        return 0.0

    # 空仓库 / 练手：几乎没有信息量
    if stars < 1 and size_kb < 100:
        return 0.2

    # 教程 / 课程作业 / awesome 清单
    if is_tutorial_like(name, description):
        return 0.1

    # 刚建、提交极少：还没形成能力信号
    if recent_commits < 5 and size_kb < 200:
        return 0.3

    # 有 star、有代码量、持续维护 = 真正的能力圈
    weight = 1.0
    weight += min(stars / 100, 0.5)     # star 越多越可信（+0.5 上限）
    if has_ci:
        weight += 0.1                   # 有 CI = 严肃项目
    return round(weight, 3)


def starred_repo_weight(stars: int, has_release: bool = False) -> float:
    """Star 仓库权重（兴趣圈，×0.6 基准）。

    长尾/冷门仓库的 star 更有信息量（说明用户有独立品味），
    这里给一点点加权，避免画像只反映"热门大众口味"。
    """
    base = PROFILE_SOURCE_WEIGHTS["starred"]
    if stars < 50:
        base *= 1.15          # 冷门仓库 = 更有辨识度的信号
    if has_release:
        base *= 1.05
    return round(base, 3)


# ------------------------------------------------------------------ 画像结构

@dataclass
class TagVector:
    """标签向量。"""

    weights: dict[str, float] = field(default_factory=dict)
    # 每个标签的来源贡献，便于调试与解释
    sources: dict[str, dict[str, float]] = field(default_factory=dict)

    def add(self, tags: dict[str, float], weight: float,
            source: str) -> None:
        if not tags or weight <= 0:
            return
        for tag, w in tags.items():
            key = tag.strip().lower()
            if len(key) < 2:
                continue
            self.weights[key] = self.weights.get(key, 0.0) + w * weight
            self.sources.setdefault(key, {})
            self.sources[key][source] = (
                self.sources[key].get(source, 0.0) + w * weight
            )

    def apply_penalty(self, tag: str, amount: float) -> None:
        """应用负反馈惩罚（可为负，用于撤销）。"""
        key = tag.strip().lower()
        if key in self.weights:
            self.weights[key] = max(0.0, self.weights[key] - amount)

    def decay_penalties(self, rate: float) -> None:
        """让惩罚随时间衰减，避免一次误点成为永久伤害。

        注意：只衰减惩罚量，不衰减基础兴趣。
        """
        # 惩罚信息记录在 sources 的 "__penalty" 键下
        for tag, src in self.sources.items():
            pen = src.get("__penalty", 0.0)
            if pen:
                src["__penalty"] = pen * rate

    def normalize(self) -> "TagVector":
        """归一化到 0~1。"""
        if not self.weights:
            return self
        mx = max(self.weights.values())
        if mx > 0:
            self.weights = {k: round(v / mx, 4) for k, v in self.weights.items()}
        return self

    def top(self, n: int = 20) -> list[tuple[str, float]]:
        return sorted(self.weights.items(), key=lambda kv: -kv[1])[:n]

    def to_json(self) -> dict:
        return {
            "weights": {k: round(v, 4) for k, v in self.top(50)},
            "count": len(self.weights),
        }

    @property
    def size(self) -> int:
        """标签数量，用于判断冷启动是否结束。"""
        return len(self.weights)


# ------------------------------------------------------------------ 相似度

def tag_overlap(a: dict[str, float], b: dict[str, float]) -> float:
    """两个标签向量的加权 Jaccard 式重合度（0~1）。"""
    if not a or not b:
        return 0.0
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    num = sum(min(a[k], b[k]) for k in shared)
    den = math.sqrt(sum(v * v for v in a.values())) * \
        math.sqrt(sum(v * v for v in b.values()))
    if den <= 0:
        return 0.0
    return min(1.0, num / den)


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    """余弦相似度。"""
    if not a or not b:
        return 0.0
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    num = sum(a[k] * b[k] for k in shared)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    if da <= 0 or db <= 0:
        return 0.0
    return num / (da * db)
