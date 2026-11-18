"""精排打分的数值回归。

这些断言对应 docs/后端修复记录.md 里几个"一改就归零"的历史缺陷：
    · 时效衰减必须有下界，否则老仓库永远得 0 分
    · 遗珠加成必须真实生效
    · 负反馈只能乘性折损，不能把分数减成负数
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rank.scorer import (
    Candidate,
    forgotten_boost,
    recency_decay,
    score_candidate,
)
from user_profile.builder import TagVector


def _candidate(days_old: float = 0, **kwargs) -> Candidate:
    pushed = (datetime.now(timezone.utc) - timedelta(days=days_old)).isoformat()
    base = {
        "repo_id": 1,
        "full_name": "acme/demo",
        "owner": "acme",
        "name": "demo",
        "pushed_at": pushed,
        "quality_score": 0.5,
    }
    base.update(kwargs)
    return Candidate(**base)


def test_fresh_repo_has_high_recency() -> None:
    assert recency_decay(_candidate(0)) > 0.99


def test_old_repo_hits_recency_floor() -> None:
    """2000 天没更新的仓库仍应保有 0.35 的时效分（遗珠召回的前提）。"""
    assert recency_decay(_candidate(2000)) >= 0.35


def test_forgotten_boost() -> None:
    c = _candidate(forgotten_score=1.0)
    assert abs(forgotten_boost(c) - 1.8) < 1e-9


def test_score_detail_matches_return_value() -> None:
    c = _candidate(days_old=10, tags={"python": 1.0})
    profile = TagVector(weights={"python": 1.0})
    score = score_candidate(c, profile)
    assert score > 0
    assert c.score_detail["final"] == round(score, 4)
    assert c.score == score


def test_negative_feedback_is_multiplicative_and_bounded() -> None:
    c = _candidate(days_old=10, tags={"python": 1.0})
    profile = TagVector(weights={"python": 1.0})
    baseline = score_candidate(c, profile)
    penalized = score_candidate(c, profile, penalties={"python": 2.0})
    assert penalized >= 0
    assert penalized < baseline
    assert c.score_detail["penalty"] == -2.0
    # 最多削掉 95%，不会变成 0/负数
    assert penalized >= baseline * 0.05 - 1e-9
