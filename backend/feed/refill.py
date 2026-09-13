"""定向补货：队列见底时，按用户画像决定"爬什么"。

⭐ 这是缓存池模式最关键的差异点。

    盲目补货（错的做法）：
        队列空了 → 抓 GitHub trending 前 50 → 灌进去
        后果：所有用户刷到的东西一样，推荐等于没做

    定向补货（本模块）：
        队列剩 6 条 → 读用户画像 + 行为指标 → 生成抓取方向 → 按方向抓
        后果：用户 A 刷到 LLM 推理，用户 B 刷到前端框架

补货方向从哪来（按可靠性排序）：
    1. 画像 Top 标签        —— 用户明确表达过的兴趣（权重最高）
    2. 高转化标签           —— "推了这个标签的仓库，用户真的深读了"
                               用 refill_log + user_events 反算，比画像更准
    3. 语言偏好             —— 用户互动的语言分布
    4. 遗珠优先             —— 本项目特色：优先补"优质但少人知"的
    5. 探索位               —— 一定比例的不相关方向，打破信息茧房
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from core.config import (
    REFILL_MAX_QUERIES,
    REFILL_PER_QUERY,
    SKIP_COOLDOWN_DAYS,
)


@dataclass
class FetchPlan:
    """一次补货的抓取计划。"""
    queries: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    domains: list[str] = field(default_factory=list)
    per_query: int = REFILL_PER_QUERY
    prefer_forgotten: bool = True
    explore_ratio: float = 0.15

    def to_dict(self) -> dict:
        return {
            "queries": self.queries,
            "languages": self.languages,
            "labels": self.labels,
            "domains": self.domains,
            "per_query": self.per_query,
            "prefer_forgotten": self.prefer_forgotten,
            "explore_ratio": self.explore_ratio,
        }


# ------------------------------------------------------------------ 画像 → 查询词

def _profile_tags(conn: sqlite3.Connection, user_id: int,
                  limit: int = 20) -> list[tuple[str, float]]:
    """读画像 Top 标签。"""
    row = conn.execute(
        "SELECT top_topics FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row or not row["top_topics"]:
        return []
    try:
        data = json.loads(row["top_topics"])
    except (json.JSONDecodeError, TypeError):
        return []
    out: list[tuple[str, float]] = []
    for item in data[:limit]:
        if isinstance(item, dict) and item.get("tag"):
            out.append((item["tag"], float(item.get("weight", 0.0))))
    return out


def _profile_languages(conn: sqlite3.Connection, user_id: int,
                       limit: int = 5) -> list[str]:
    row = conn.execute(
        "SELECT top_languages FROM user_profiles WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row or not row["top_languages"]:
        return []
    try:
        data = json.loads(row["top_languages"])
    except (json.JSONDecodeError, TypeError):
        return []
    langs = []
    for item in data[:limit]:
        if isinstance(item, dict) and item.get("lang"):
            langs.append(item["lang"])
    return langs


def _high_conversion_tags(conn: sqlite3.Connection, user_id: int,
                          limit: int = 10) -> list[tuple[str, float]]:
    """反算"哪些标签带来的仓库被真正深读了"。

    ⭐ 这比画像更准：画像记录"用户接触过什么"，
       而这里记录"用户对什么产生了深度行为"。

    做法：把用户对每个仓库的 deep_read 行为，与该仓库的标签关联，算深读率。

    ⚠️ 必须剔除仓库名派生标签。
       实测：用户互动过的 dspy-mini / cpython 让 "dspy"、"cpython"
       以 0.54 / 0.39 的转化率进入补货方向，最后补货方向变成了
       [gguf, models, dspy, minimal] —— 项目名当技术方向用，
       抓回来的货自然全是同一批仓库，定向补货等于没做。
    """
    rows = conn.execute(
        """SELECT r.tags_json, r.owner, r.name, r.full_name, e.event_type
           FROM user_events e
           JOIN repos r ON r.id = e.repo_id
           WHERE e.user_id = ?
             AND e.event_type IN ('deep_read', 'skip', 'dislike')
             AND r.tags_json IS NOT NULL
           ORDER BY e.created_at DESC
           LIMIT 500""",
        (user_id,),
    ).fetchall()

    stats: dict[str, list[int]] = {}   # tag -> [deep, total]
    for r in rows:
        try:
            tags = json.loads(r["tags_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(tags, dict):
            continue

        # ⭐ 剔除仓库名派生的标签（与 profile.service 同一套规则）
        self_tags = _self_tags_of(r)

        # ⚠️ 选 Top 标签时要先做方向词加权。
        #    否则语音仓库的 Top5 会被 训练/素材/模块 这类通用词占满，
        #    rvc/voice 根本进不来，补货方向就丢了语音方向。
        weighted = sorted(
            tags.items(),
            key=lambda kv: -_domain_weight(str(kv[0]), float(kv[1])),
        )[:5]
        good = 1 if r["event_type"] == "deep_read" else 0
        for tag, _ in weighted:
            key = str(tag).lower()
            if key in self_tags:
                continue          # 项目名不是技术方向
            if _is_generic(key):
                continue          # 通用词不是技术方向
            slot = stats.setdefault(key, [0, 0])
            slot[0] += good
            slot[1] += 1

    scored = [
        (tag, deep / total, total)
        for tag, (deep, total) in stats.items()
        if total >= 3          # 样本太少不算，避免偶然
    ]
    scored.sort(key=lambda kv: (-kv[1], -kv[2]))
    return [(t, rate) for t, rate, _ in scored[:limit]]


# ------------------------------------------------------------------ 领域方向词
#
# ⚠️ 为什么需要：TF-IDF 只看词频，分不清"方向词"和"出现在该方向的通用词"。
#    实测：语音仓库的 rvc 权重只有 0.18，排在 训练(0.31)/素材(0.21) 后面，
#    生成补货方向时被挤出 Top5 → 语音方向整个丢失。

_DOMAIN_BOOST = 1.8      # 方向词的权重倍数


def _load_domain_terms() -> tuple[set[str], dict[str, str]]:
    """读 dict/domain_terms.txt，返回 (方向词集合, 词→方向 映射)。"""
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "dict" / "domain_terms.txt"
    known: set[str] = set()
    group: dict[str, str] = {}
    if not path.exists():
        return known, group
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            name, _, words = line.partition(":")
            name = name.strip()
            if not name:
                continue
            for w in words.split():
                w = w.strip().lower()
                if w:
                    known.add(w)
                    group[w] = name
    except OSError:
        pass
    return known, group


_DOMAIN_TERMS, _DOMAIN_GROUP = _load_domain_terms()


def _self_tags_of(row) -> set[str]:
    """从一行仓库记录推出"仓库名派生词"集合。

    与 profile.service._repo_self_tags 保持同一口径 ——
    两边如果规则不一致，会出现"画像里过滤掉了、补货方向里又冒出来"。

    ⚠️ 领域方向词必须豁免。
       实测：`sovits` 因为仓库名就叫 `gpt-sovits`，
       按"项目名派生词"被过滤掉 —— 但它同时是整个语音合成方向的
       通用技术词（Sovits 是一类模型架构，不止这一个仓库用）。
       同理 `rvc` 在 rvc-project/... 下也会被整条规则误伤。
       判据：只要一个词出现在 dict/domain_terms.txt 里，
       就按"技术方向词"处理，不再当项目名。
    """
    names: set[str] = set()
    for field in ("name", "full_name", "owner"):
        try:
            raw = row[field]
        except (IndexError, KeyError):
            continue
        if not raw:
            continue
        low = str(raw).lower()
        names.add(low)
        for part in low.replace("/", "-").split("-"):
            part = part.strip()
            if len(part) >= 3:
                names.add(part)
    # 方向词豁免：技术术语优先于"项目名"判定
    return {n for n in names if n not in _DOMAIN_TERMS}


def _is_generic(tag: str) -> bool:
    """通用词判定：明显不表技术方向的词。

    停用词表已经拦了大部分，这里做补货环节的二次兜底 ——
    因为补货方向要直接拿去当搜索词，混进通用词会导致
    "抓回来一堆不相关的仓库"，代价比画像被稀释更高。
    """
    if len(tag) < 3 or tag.isdigit():
        return True
    return tag in _GENERIC_TAGS


def domain_of(tag: str) -> str | None:
    """查一个标签属于哪个领域方向（不属于则 None）。"""
    return _DOMAIN_GROUP.get(str(tag).lower())


def _domain_weight(tag: str, weight: float) -> float:
    """方向词加权：命中领域词表就放大。

    ⚠️ 只放大方向词，不动其他词 ——
       否则等于给所有词加同样的倍数，等于没加。
    """
    if str(tag).lower() in _DOMAIN_TERMS:
        return weight * _DOMAIN_BOOST
    return weight


_GENERIC_TAGS: set[str] = {
    "models", "model", "minimal", "mini", "simple", "fast", "quick",
    "string", "strings", "number", "numbers", "value", "values",
    "level", "levels", "form", "forms", "type", "types", "kind",
    "case", "cases", "part", "parts", "way", "ways", "thing", "things",
    "item", "items", "step", "steps", "result", "results", "reason",
    "method", "methods", "process", "example", "examples",
}


# ------------------------------------------------------------------ 生成计划

def build_fetch_plan(conn: sqlite3.Connection, user_id: int,
                     *, queue_size: int = 0) -> FetchPlan:
    """根据用户画像与行为指标，生成这次补货该抓什么。"""
    plan = FetchPlan()

    # ── ⓪ 用户自定义关键词（画像面板维护）—— 最高优先级信号 ──
    # 用户亲口指定的方向，直接占位且不受通用词过滤影响；
    # 爬虫抓回来的仓库 topics 已并入标签，本地池挑选时天然命中。
    try:
        from api.crawl_service import get_user_keywords
        custom = [k for k in get_user_keywords(conn, user_id) if k][:3]
    except Exception:
        custom = []

    # ── ⓪b GitHub 实证圈方向词（自建仓库话题 > 点星仓库话题）──
    # 这些是"用户亲手做过 / 明确收藏过"的技术方向，可信度仅次于自定义关键词，
    # 优先拿去当抓取查询词 → 队列里这类仓库会明显变多（用户要的"猛推"）。
    try:
        from api.auth_service import footprint_preferred_queries
        gh_queries = footprint_preferred_queries(conn, user_id, limit=4)
    except Exception:
        gh_queries = []

    # ── ① 高转化标签优先（最可靠信号）──
    conv = _high_conversion_tags(conn, user_id, limit=6)
    conv_tags = [t for t, rate in conv if rate >= 0.25]

    # ── ② 画像 Top 标签 ──
    prof = _profile_tags(conn, user_id, limit=20)
    prof_tags = [t for t, _ in prof]

    # ── ③ 语言偏好 ──
    plan.languages = _profile_languages(conn, user_id, limit=3)

    # 合并去重：高转化标签排前面（它更可靠）
    ordered: list[str] = []
    for t in conv_tags + prof_tags:
        if t and t not in ordered:
            ordered.append(t)

    # ── ④ 领域方向词插队 ──
    # ⚠️ 必须单独占席位。
    #    实测：画像 Top20 里完全没有语音词（被 server/gpu/cuda/gguf 占满），
    #    只靠"按权重排序取 Top5"必然丢方向。
    #    做法：识别画像里已出现的领域，把该领域的方向词提前，
    #          再按"领域覆盖"补足到 REFILL_MAX_QUERIES 个方向。
    seen_domains: list[str] = []
    domain_words: list[str] = []
    for t in ordered:
        d = domain_of(t)
        if d and d not in seen_domains:
            seen_domains.append(d)
            # 该方向的所有词，按词表顺序（越靠前越具代表性）
            domain_words.extend(
                w for w, g in _DOMAIN_GROUP.items() if g == d
            )
    # 领域方向词排在高转化标签之后、普通画像词之前
    merged: list[str] = []
    for t in conv_tags + domain_words + ordered:
        tl = str(t).lower()
        if not tl or tl in merged or _is_generic(tl):
            continue
        merged.append(tl)

    # 画像太空（新用户）且没有自定义关键词 / 实证圈 → 退化为通用的"优质仓库"方向
    if not merged and not custom and not gh_queries:
        plan.queries = ["awesome", "starter", "toolkit"]
        plan.labels = ["cold_start"]
        plan.explore_ratio = 0.4
        return plan

    # 优先级：用户自定义关键词 > GitHub 实证圈方向 > 画像/高转化标签
    plan.queries = (custom + gh_queries + merged)[:REFILL_MAX_QUERIES]
    plan.labels = ["high_conversion"] * min(len(conv_tags), REFILL_MAX_QUERIES)
    plan.domains = seen_domains[:5]
    # 画像里的标签越多，说明兴趣越明确，探索比例可以低一些
    plan.explore_ratio = max(0.10, 0.30 - 0.01 * len(merged))
    return plan


# ------------------------------------------------------------------ 本地候选挑选
#
# 说明：本项目当前阶段用"本地已有仓库池"充当爬虫的返回结果。
#      真实爬虫（jobs/crawler.py）接入后，从这里替换成 GitHub API 调用，
#      其余逻辑（入队、水位、日志）完全不用改。

def pick_from_local_pool(
    conn: sqlite3.Connection,
    user_id: int,
    plan: FetchPlan,
    *,
    need: int,
) -> list[int]:
    """按抓取计划，从本地仓库池里挑出该补的仓库 id。

    ⚠️ 这里刻意重复了召回/打分的一小部分逻辑，而不是直接调 build_feed：
       因为补货是"批量灌队列"，不需要满足单次 Feed 的打散约束，
       而且需要严格排除"已在队列里的"和"用户已互动的"。
    """
    if need <= 0:
        return []

    # 已在队列里的（不重复灌）
    queued = {
        r["repo_id"] for r in conn.execute(
            "SELECT repo_id FROM feed_queue WHERE user_id = ?", (user_id,)
        )
    }

    # ⚠️ 互动历史要分等级排除，不能一刀切。
    #
    #    实测教训：把 skip 也当永久排除后，
    #    84 个仓库里用户互动过 52 个（其中大量是 skip），
    #    排除完剩余 0 个候选 → 队列只能灌 32 条、远低于目标 60，
    #    再刷一轮就彻底无货可补。
    #
    #    语义上：
    #      skip     = "划过去了"，这是信息流的默认行为，不代表不想看
    #                 → 只做短期冷却（SKIP_COOLDOWN_DAYS 天内不重复推）
    #      dislike  = 明确点了"不感兴趣" → 永久排除
    #      深读/收藏 → 已经消费过，短期内不必重复推（同样用冷却处理）
    interacted = {
        r["repo_id"] for r in conn.execute(
            """SELECT DISTINCT repo_id FROM user_events
               WHERE user_id = ?
                 AND event_type IN ('dislike')""",
            (user_id,),
        )
    }
    # 短期冷却：近期划过/读过的先不推，过一段时间可以再出现
    recent = {
        r["repo_id"] for r in conn.execute(
            f"""SELECT DISTINCT repo_id FROM user_events
                WHERE user_id = ?
                  AND event_type IN ('deep_read','star_click','like','skip')
                  AND created_at >= datetime('now', '-{SKIP_COOLDOWN_DAYS} days')""",
            (user_id,),
        )
    }
    star_repos = {
        r["repo_id"] for r in conn.execute(
            "SELECT repo_id FROM stars WHERE user_id = ?", (user_id,)
        )
    }
    # 硬排除：这些仓库这次绝对不能再进队列
    exclude = queued | interacted | star_repos
    # 软排除：冷却期内的仓库，优先不用；候选不够时可以回填
    soft = recent - exclude

    # 按标签匹配度 + 遗珠优先 打分
    rows = conn.execute(
        """SELECT r.id, r.tags_json, r.topics, r.forgotten_score,
                  r.quality_score, r.velocity_score, r.freshness_score,
                  r.language, r.stars
           FROM repos r
           WHERE r.is_archived = 0 AND r.is_dead = 0"""
    ).fetchall()

    wanted = set(t.lower() for t in plan.queries)
    wanted_langs = set(l.lower() for l in plan.languages)

    scored: list[tuple[float, int]] = []
    cooled: list[tuple[float, int]] = []      # 冷却期内，仅作备用
    for r in rows:
        rid = r["id"]
        if rid in exclude:
            continue
        try:
            tags = json.loads(r["tags_json"]) if r["tags_json"] else {}
        except (json.JSONDecodeError, TypeError):
            tags = {}

        # 标签命中
        hit = sum(float(w) for t, w in tags.items() if t.lower() in wanted)
        # 语言命中
        lang_bonus = 0.3 if (r["language"] or "").lower() in wanted_langs else 0.0
        # 遗珠优先（本项目特色：优先给冷门优质仓库曝光机会）
        fg = (r["forgotten_score"] or 0.0) * 0.8 if plan.prefer_forgotten else 0.0
        # 质量兜底：完全没有标签命中的仓库也不该被完全排除
        base = (r["quality_score"] or 0.0) * 0.15

        score = hit + lang_bonus + fg + base
        if rid in soft:
            # ⚠️ 冷却期内的仓库降权，但仍然保留在候选里。
            #    数据规模小的时候（本地 94 个仓库），
            #    硬排除会让候选池直接枯竭，队列永远补不满。
            cooled.append((score * 0.4, rid))
        else:
            scored.append((score, rid))

    scored.sort(key=lambda kv: -kv[0])
    cooled.sort(key=lambda kv: -kv[0])

    # 探索比例：混入一些低分仓库（打破信息茧房）
    n_explore = int(need * plan.explore_ratio)
    n_main = need - n_explore

    main = [rid for _, rid in scored[:n_main]]
    rest = [rid for _, rid in scored[n_main:]]
    # 探索位从剩余的里挑（用末段，避免和主推荐重复）
    explore = rest[:n_explore] if rest else []

    out = main + explore
    # 候选不足 → 用冷却期内的仓库回填（宁可重复，也不让队列见底）
    if len(out) < need:
        out += [rid for _, rid in cooled[: need - len(out)]]

    return out[:need]


def plan_summary(plan: FetchPlan) -> str:
    """人类可读的计划描述（用于日志）。"""
    q = ", ".join(plan.queries[:5]) or "(无)"
    lang = ", ".join(plan.languages) or "(无)"
    dom = ", ".join(plan.domains) or "(无)"
    return (f"方向=[{q}] 领域=[{dom}] 语言=[{lang}] "
            f"探索率={plan.explore_ratio:.2f}")
