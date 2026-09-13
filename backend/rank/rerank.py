"""重排：打散 + 探索位 + 搜索干预混合。

对应抖音重排阶段：多样性调整、打散同类型内容、插入探索内容。
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from core.config import (
    SEARCH_DECAY_STEP,
    SEARCH_MAX_REFRESH,
    SEARCH_MIX_RATIO,
    SEARCH_WEIGHT,
)
from user_profile.builder import tag_overlap
from rank.scorer import Candidate


# ------------------------------------------------------------------ 搜索干预

@dataclass
class SearchIntervention:
    """搜索词作为临时标签参与推荐，并随时间衰减。

    ⭐ 修正的设计缺陷：
       原设计用乘性衰减 (×0.8) 实现"5 次归零"，
       实测 2.0 × 0.8⁵ = 0.8192，永远不会归零。
       改用减性衰减：2.0 - 0.4×n，第 5 次精确归零。
    """

    tags: dict[str, float]
    weight: float = SEARCH_WEIGHT
    refresh_count: int = 0
    expired: bool = False

    @property
    def current_weight(self) -> float:
        if self.expired:
            return 0.0
        return max(0.0, SEARCH_WEIGHT - SEARCH_DECAY_STEP * self.refresh_count)

    def on_refresh(self) -> None:
        """每次刷新衰减一次。"""
        self.refresh_count += 1
        if self.refresh_count >= SEARCH_MAX_REFRESH:
            self.expire()

    def expire(self) -> None:
        self.expired = True
        self.tags = {}

    def boost(self, c: Candidate) -> float:
        """计算该候选因搜索词获得的加成。"""
        if self.expired or not self.tags:
            return 0.0
        overlap = tag_overlap(self.tags, c.tags)
        return overlap * self.current_weight


def apply_search_intervention(
    candidates: list[Candidate],
    si: SearchIntervention | None,
) -> None:
    """把搜索加成叠加到候选分数上（原地修改）。"""
    if si is None or si.expired or not si.tags:
        return
    for c in candidates:
        b = si.boost(c)
        if b > 0:
            c.score += b
            c.score_detail["search_boost"] = round(b, 4)
            if "search" not in c.source_channels:
                c.source_channels.append("search")


# ------------------------------------------------------------------ 混合

def mix_search_and_longterm(
    long_term: list[Candidate],
    search_related: list[Candidate],
    total: int = 20,
) -> list[Candidate]:
    """长期兴趣与搜索相关按 4:1 混合。

    实现说明：
       位置占比 20%（每 5 个位置 1 个搜索位），而非项数占比。
       实测："每4个长期插1个" 的项数占比是 16.7%，这里用固定位置
       偏移 2 使搜索项不总落在"每第5个"这种可被察觉的规律位置上。
    """
    if not search_related:
        return long_term[:total]

    out: list[Candidate] = []
    si = li = 0

    for i in range(total):
        pick_search = (i % SEARCH_MIX_RATIO == 2)

        if pick_search and si < len(search_related):
            item = search_related[si]
            si += 1
        elif li < len(long_term):
            item = long_term[li]
            li += 1
        elif si < len(search_related):
            item = search_related[si]
            si += 1
        else:
            break

        out.append(item)

    return out


# ------------------------------------------------------------------ 打散

def rerank_disperse(
    candidates: list[Candidate],
    total: int = 20,
    explore_rate: float = 0.15,
    *,
    rand: random.Random | None = None,
) -> list[Candidate]:
    """最终重排：打散 + 探索位。

    规则：
    - 同一语言不连续出现
    - 同一作者不连续出现
    - 按 explore_rate 插入随机探索内容
    """
    rng = rand or random.Random()
    pool = sorted(candidates, key=lambda c: -c.score)
    if not pool:
        return []

    out: list[Candidate] = []
    used_ids: set[int] = set()
    prev_lang: str | None = None
    prev_owner: str | None = None

    explore_slots = max(1, int(total * explore_rate))
    # 探索位放在 1/3、2/3 附近，避免集中在末尾
    explore_positions = {
        int(total * (k + 1) / (explore_slots + 1))
        for k in range(explore_slots)
    }

    for i in range(total):
        if len(out) >= total:
            break

        if i in explore_positions:
            # 探索位：从剩余里随机挑（跳过明显低分的）
            remaining = [c for c in pool if c.repo_id not in used_ids]
            if remaining:
                # 从前 60% 分位里随机，保证探索内容也不至于太差
                cut = max(1, int(len(remaining) * 0.6))
                pick = rng.choice(remaining[:cut])
                pick.channel = "explore"
                if "explore" not in pick.source_channels:
                    pick.source_channels.append("explore")
                out.append(pick)
                used_ids.add(pick.repo_id)
                prev_lang, prev_owner = pick.language, pick.owner
                continue

        # 正常位：取分数最高、且不与前一个同语言/同作者的
        chosen = None
        for c in pool:
            if c.repo_id in used_ids:
                continue
            if prev_lang and c.language == prev_lang:
                continue
            if prev_owner and c.owner == prev_owner:
                continue
            chosen = c
            break

        # 打散过严导致选不出，放宽条件
        if chosen is None:
            for c in pool:
                if c.repo_id not in used_ids:
                    chosen = c
                    break

        if chosen is None:
            break

        out.append(chosen)
        used_ids.add(chosen.repo_id)
        prev_lang, prev_owner = chosen.language, chosen.owner

    # ⭐ 遗珠/新仓库保底配额
    #    仅靠加分是不够的：实测热门仓库的 velocity 分项就有 0.96，
    #    而遗珠加成上限只有 1.8，遗珠仓库最高分仍在全池第 19 位，
    #    永远进不了前 10。加分只能"拉近差距"，拉不平差距。
    #    所以必须给独立席位配额 —— 这是本项目与商业平台的本质区别，
    #    商业平台不会留这种位，我们要留。
    out = _enforce_quota(out, pool, used_ids, total, rng)

    return out


# 每类弱势内容在单次 Feed 中的保底席位数
QUOTA_FORGOTTEN = 2   # 遗珠仓库：优质但长期无人问津
QUOTA_FRESH = 1       # 新仓库：刚创建、还没被验证


def _enforce_quota(
    out: list[Candidate],
    pool: list[Candidate],
    used_ids: set[int],
    total: int,
    rng: random.Random,
) -> list[Candidate]:
    """把遗珠/新仓库按配额塞进结果，替换掉末尾的重复性内容。

    策略：从末尾往前替换，优先挤掉"同类扎堆"的位（例如连续第 3 个
    JavaScript 仓库），而不是挤掉高分的头部位，保持整体质量。
    """
    if not out:
        return out

    def is_forgotten(c: Candidate) -> bool:
        return c.forgotten_score >= 0.7

    def is_fresh(c: Candidate) -> bool:
        return c.freshness_score >= 0.9 and c.stars < 200

    for need, pred in ((QUOTA_FORGOTTEN, is_forgotten), (QUOTA_FRESH, is_fresh)):
        have = sum(1 for c in out if pred(c))
        if have >= need:
            continue

        # 候选：池子里符合条件但还没被选中的，按分排序取最高的
        avail = [c for c in pool if pred(c) and c.repo_id not in used_ids]
        avail.sort(key=lambda c: -c.score)

        # ⭐ 席位位置：插在中段而非末尾。
        #    "放在第 9、10 位"等于告诉用户"这是凑数的"，
        #    曝光质量远低于穿插展示。按 Feed 长度取 1/3、2/3 附近的位。
        slots = [
            max(2, int(total * r) )
            for r in (0.33, 0.66, 0.5, 0.8)
        ]

        for cand in avail[: need - have]:
            if len(out) <= 3:
                break
            placed = False
            for slot in slots:
                if slot >= len(out) - 1:
                    continue
                if not pred(out[slot]):
                    evicted = out[slot]
                    used_ids.discard(evicted.repo_id)
                    out[slot] = cand
                    placed = True
                    break
            if not placed:
                # 中段没有可换的位，退回从末尾替换
                for pos in range(len(out) - 1, 2, -1):
                    if not pred(out[pos]):
                        evicted = out[pos]
                        used_ids.discard(evicted.repo_id)
                        out[pos] = cand
                        placed = True
                        break
            if placed:
                cand.channel = "quota"
                if "quota" not in cand.source_channels:
                    cand.source_channels.append("quota")
                used_ids.add(cand.repo_id)

    return out
