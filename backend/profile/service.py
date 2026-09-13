"""用户画像构建与落库。

⚠️ 这个模块此前是**缺失**的：`TagVector` 类写得很完整，
   但没有任何代码调用它去构建画像，`user_profiles` 表只被读、从没被写。
   后果是兴趣召回通道（recall_interest）**从未被真实激活过** ——
   画像永远是空的，`interest_count` 恒为 0，永远停留在冷启动。

本模块补上这条链路：
    用户行为/仓库 → TagVector → 落库 user_profiles → 供 Feed 召回使用

三层来源（对应"三层标签体系"的另一面）：
    能力圈 owned    ×1.0   自己写的仓库 —— 最能代表技能
    兴趣圈 starred  ×0.6   点过 star 的 —— 代表品味
    注意力圈 read   ×0.3   完整读过的 —— 愿意花时间的
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone

from core.config import (
    COLD_START_MIN_TAGS,
    PROFILE_SOURCE_WEIGHTS,
    READ_WPM,
)
from profile.builder import (
    TagVector,
    owned_repo_weight,
    starred_repo_weight,
)


# ------------------------------------------------------------------ 数据加载

def _parse_tags(raw: str | None) -> dict[str, float]:
    """解析 repos.tags_json。"""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in data.items():
        try:
            out[str(k).strip().lower()] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def load_owned_repos(conn: sqlite3.Connection,
                     github_login: str) -> list[sqlite3.Row]:
    """加载用户自己拥有的仓库。

    数据来源说明：本项目不抓取用户私有数据，`repos` 表里
    只有"已被系统收录"的仓库。owner 匹配 github_login 即视为自建仓库。
    """
    if not github_login:
        return []
    return conn.execute(
        """SELECT id, name, full_name, description, stars, size_kb,
                  has_ci, is_archived, tags_json, pushed_at_gh
           FROM repos
           WHERE owner = ? COLLATE NOCASE
           ORDER BY stars DESC, pushed_at_gh DESC
           LIMIT 100""",
        (github_login,),
    ).fetchall()


def load_starred_repos(conn: sqlite3.Connection,
                       user_id: int) -> list[sqlite3.Row]:
    """加载用户点过 star 的仓库（兴趣圈）。"""
    return conn.execute(
        """SELECT r.id, r.name, r.full_name, r.description, r.stars,
                  r.tags_json, r.pushed_at_gh, s.created_at
           FROM stars s
           JOIN repos r ON r.id = s.repo_id
           WHERE s.user_id = ?
           ORDER BY s.created_at DESC
           LIMIT 300""",
        (user_id,),
    ).fetchall()


def load_read_repos(conn: sqlite3.Connection,
                    user_id: int) -> list[sqlite3.Row]:
    """加载用户完整读过的仓库（注意力圈）。

    只取 deep_read，且按最近阅读排序；同一个仓库只算一次
    （重复阅读不叠加权重，否则反复刷同一篇会污染画像）。
    """
    return conn.execute(
        """SELECT r.id, r.full_name, r.tags_json, MAX(e.created_at) AS last_read
           FROM user_events e
           JOIN repos r ON r.id = e.repo_id
           WHERE e.user_id = ? AND e.event_type = 'deep_read'
           GROUP BY r.id
           ORDER BY last_read DESC
           LIMIT 300""",
        (user_id,),
    ).fetchall()


def load_dislike_penalties(conn: sqlite3.Connection,
                           user_id: int) -> dict[str, float]:
    """加载负反馈惩罚（梯度惩罚，线性 + 保底）。

    与 api/feed_service.load_penalties 保持同一套公式，
    避免"画像里的惩罚"和"排序时的惩罚"数值不一致。
    """
    rows = conn.execute(
        """SELECT repo_id FROM user_events
           WHERE user_id = ? AND event_type = 'dislike'
           ORDER BY created_at DESC LIMIT 200""",
        (user_id,),
    ).fetchall()

    penalties: dict[str, float] = {}
    for r in rows:
        row = conn.execute(
            "SELECT tags_json FROM repos WHERE id = ?", (r["repo_id"],)
        ).fetchone()
        tags = _parse_tags(row["tags_json"] if row else None)
        if not tags:
            continue
        ranked = sorted(tags.items(), key=lambda kv: -kv[1])[:5]
        for rank, (tag, _) in enumerate(ranked):
            amount = max(0.05, 0.25 - 0.04 * rank)
            penalties[tag] = penalties.get(tag, 0.0) + amount
    return penalties


# ------------------------------------------------------------------ 构建

def _repo_self_tags(row) -> set[str]:
    """仓库自身名字派生出的标签（owner/name 片段）。

    ⚠️ 必须排除：实测模拟后画像 Top1 变成了 `elasticsearch` ——
       一个仓库名霸榜。这类标签只反映"用户看过某个仓库"，
       不反映技术兴趣方向，而且会让画像退化成"最近看过的项目名列表"。

    ⚠️ 领域方向词豁免（与 feed/refill._self_tags_of 同一口径）：
       `sovits` 出现在仓库名 gpt-sovits 里，但它本身是语音合成的
       通用技术词；`rvc` 在 rvc-project/... 下同理。
       如果不过滤掉这类误伤，画像和补货方向都会丢掉整个语音方向。
    """
    names: set[str] = set()
    for field in ("name", "full_name"):
        try:
            raw = row[field]
        except (IndexError, KeyError):
            continue
        if not raw:
            continue
        for part in str(raw).lower().replace("/", "-").split("-"):
            part = part.strip()
            if len(part) >= 3:
                names.add(part)
        names.add(str(raw).lower())
    try:
        from feed.refill import _DOMAIN_TERMS
    except Exception:       # 循环导入兜底：导入失败就不做豁免
        return names
    return {n for n in names if n not in _DOMAIN_TERMS}


def build_profile(conn: sqlite3.Connection, user_id: int) -> TagVector:
    """从用户的三层足迹构建标签画像。

    ⭐ 关键设计：IDF 式抑制 + 仓库名剔除。
       朴素做法是"把每个互动过的仓库标签直接相加"，
       但这样会导致两个退化：
         1. 仓库名霸榜（画像变成"最近看过的项目列表"）
         2. 互动多的少数仓库主导全部标签（过拟合 → 信息茧房）
       所以这里：
         - 剔除仓库自身名字派生的标签
         - 对"只被少数几个仓库拥有"的标签降权（IDF 思想）
    """
    user = conn.execute(
        "SELECT github_login FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    login = user["github_login"] if user else None

    owned = load_owned_repos(conn, login or "")
    starred = load_starred_repos(conn, user_id)
    read = load_read_repos(conn, user_id)

    # ── GitHub 实证圈：用户自建仓库 / 点星仓库（最强信号）──
    # 权重由 api/auth_service 按真实数据算好（自建看推送时间、点星看仓库星数排名），
    # 这里直接用；topics 是该仓库的 GitHub 官方话题，作为标签来源。
    from api.auth_service import load_footprint
    fp_rows = load_footprint(conn, user_id)

    def _fp_tags(raw: str | None) -> dict[str, float]:
        try:
            arr = json.loads(raw or "[]")
        except (json.JSONDecodeError, TypeError):
            return {}
        if not isinstance(arr, list):
            return {}
        return {str(t).strip().lower(): 1.0 for t in arr if str(t).strip()}

    # ── 先统计每个标签被多少个不同仓库"拥有"（文档频率）──
    doc_freq: dict[str, int] = {}
    all_rows = list(owned) + list(starred) + list(read)
    for row in all_rows:
        for tag in _parse_tags(row["tags_json"]):
            doc_freq[tag] = doc_freq.get(tag, 0) + 1
    for row in fp_rows:
        for tag in _fp_tags(row["topics_json"]):
            doc_freq[tag] = doc_freq.get(tag, 0) + 1
    n_docs = max(1, len(all_rows) + len(fp_rows))

    def idf(tag: str) -> float:
        """标签的稀有度权重。

        ⚠️ 修正：这里的目标是【抑制只属于单个仓库的专有词】，
           而不是压制通用技术词。之前写成 `1/√df` 对 df>1 生效，
           等于惩罚了 "llm"、"python" 这类出现在多个仓库的通用真兴趣词，
           结果画像 Top 被 components/runtime/static 这种泛词占满。

        正确做法（类 IDF 的"社区共识"权重）：
           df 越大 → 越是多个项目的共同技术方向 → 权重越高
           df = 1  → 只属于一个项目，可能是项目名/专有名词 → 降权
        上限 1.0，避免超级常见词（如 language）独占。
        """
        df = doc_freq.get(tag, 1)
        if df <= 1:
            return 0.4          # 单仓库专属：很可能是项目名，降权
        return min(1.0, math.log(1 + df) / math.log(1 + 5))

    tv = TagVector()

    # ── 实证圈（最强）：GitHub 自建仓库 / 点星仓库 ──
    # ⚠️ 这里是"用户亲手做过的东西"，比刷到停一会儿（dwell）可信得多，
    #    所以权重最高并且乘 GITHUB_SIGNAL_BOOST 放大 —— 用户要求"非常猛地推"。
    from core.config import GITHUB_SIGNAL_BOOST
    for row in fp_rows:
        tags = _fp_tags(row["topics_json"])
        if not tags:
            continue
        tags = {t: v * idf(t) for t, v in tags.items()}
        src = "github_owned" if row["source"] == "owned" else "github_starred"
        tv.add(tags, float(row["weight"]) * GITHUB_SIGNAL_BOOST, src)

    # ── 能力圈：自建仓库 ×1.0（含降噪）──
    for row in owned:
        w = owned_repo_weight(
            is_fork=bool(row["is_archived"]),
            stars=row["stars"] or 0,
            size_kb=row["size_kb"] or 0,
            has_ci=bool(row["has_ci"]),
            name=row["name"] or "",
            description=row["description"],
        )
        if w <= 0:
            continue
        tags = _filter_self_tags(_parse_tags(row["tags_json"]), row)
        if not tags:
            continue
        tags = _rerank_within_repo(tags)          # ← 消除 README 长度偏差
        tags = {t: v * idf(t) for t, v in tags.items()}
        tv.add(tags, w * PROFILE_SOURCE_WEIGHTS["owned"], "owned")

    # ── 兴趣圈：Star 仓库 ×0.6 ──
    for row in starred:
        tags = _filter_self_tags(_parse_tags(row["tags_json"]), row)
        if not tags:
            continue
        tags = _rerank_within_repo(tags)
        tags = {t: v * idf(t) for t, v in tags.items()}
        w = starred_repo_weight(row["stars"] or 0)
        tv.add(tags, w, "starred")

    # ── 注意力圈：完整阅读 ×0.3 ──
    for row in read:
        tags = _filter_self_tags(_parse_tags(row["tags_json"]), row)
        if not tags:
            continue
        tags = _rerank_within_repo(tags)
        tags = {t: v * idf(t) for t, v in tags.items()}
        tv.add(tags, PROFILE_SOURCE_WEIGHTS["read"], "read")

    # ── 负反馈惩罚 ──
    for tag, amount in load_dislike_penalties(conn, user_id).items():
        tv.apply_penalty(tag, amount)

    return tv.normalize()


def _filter_self_tags(tags: dict[str, float], row) -> dict[str, float]:
    """剔除仓库自身名字派生的标签。"""
    if not tags:
        return {}
    self_tags = _repo_self_tags(row)
    return {t: v for t, v in tags.items() if t not in self_tags}


def _rerank_within_repo(tags: dict[str, float]) -> dict[str, float]:
    """把单个仓库的标签归一到它自己的最大值。

    ⚠️ 必须做这一步，否则 README 短的仓库会因为"词少"而赢。

    实测证据：
        elastic/elasticsearch 的 README 只有 6 个词，
        extract_readme_tags + normalize 后每个词都是 1.71；
        而 ollama/ollama 的 README 有几十个词，最高词 gguf 才 0.56。

        于是画像汇总时：
            elasticsearch 的 search   → 1.71 × 0.6 × 0.4 = 0.41
            ollama 的       llm       → 0.28 × 0.6 × 0.4 = 0.067
        一个冷门仓库仅凭 README 短，就把 search/engine/restful/distributed
        全部顶进画像 Top5，画像彻底失真（实测 Top1 = search，而用户
        的真实兴趣是 llm / 推理 / python）。

    归一化后每个仓库"最多也只能贡献 1.0 的强度"，
    谁的标签更突出由仓库内部相对权重决定，与 README 长度无关。
    """
    if not tags:
        return {}
    mx = max(tags.values())
    if mx <= 0:
        return {}
    return {k: v / mx for k, v in tags.items()}


def save_profile(conn: sqlite3.Connection, user_id: int,
                 tv: TagVector) -> None:
    """把画像落库到 user_profiles。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    interests = [{"tag": k, "weight": round(v, 4)} for k, v in tv.top(50)]

    conn.execute(
        """INSERT INTO user_profiles
               (user_id, top_topics, top_languages, affinity_owners,
                interest_count, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
               top_topics     = excluded.top_topics,
               top_languages  = excluded.top_languages,
               affinity_owners= excluded.affinity_owners,
               interest_count = excluded.interest_count,
               updated_at     = excluded.updated_at""",
        (
            user_id,
            json.dumps(interests, ensure_ascii=False),
            json.dumps(_top_languages(conn, user_id), ensure_ascii=False),
            json.dumps([], ensure_ascii=False),
            tv.size,
            now,
        ),
    )


def _top_languages(conn: sqlite3.Connection,
                   user_id: int) -> list[dict]:
    """统计用户互动过的仓库语言分布。"""
    rows = conn.execute(
        """SELECT r.language, COUNT(*) AS n
           FROM user_events e JOIN repos r ON r.id = e.repo_id
           WHERE e.user_id = ? AND r.language IS NOT NULL
             AND e.event_type IN ('deep_read', 'star_click', 'like')
           GROUP BY r.language ORDER BY n DESC LIMIT 10""",
        (user_id,),
    ).fetchall()
    if not rows:
        return []
    total = sum(r["n"] for r in rows) or 1
    return [{"lang": r["language"], "score": round(r["n"] / total, 3)}
            for r in rows]


def rebuild_profile(conn: sqlite3.Connection, user_id: int) -> TagVector:
    """重建并落库（供定时任务与接口调用）。"""
    tv = build_profile(conn, user_id)
    save_profile(conn, user_id, tv)
    return tv


def is_profile_warm(tv: TagVector) -> bool:
    return tv.size >= COLD_START_MIN_TAGS


# ------------------------------------------------------------------ 时间感知

def read_seconds_for(readme_len: int) -> float:
    """按字数估算"完整阅读"所需秒数（用于前端判定深读阈值）。"""
    chars = max(1, readme_len)
    return max(15.0, min(180.0, chars / READ_WPM * 60))
