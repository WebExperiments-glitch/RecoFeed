"""多路召回。

7 路并行召回，各自取 Top-N 后合并去重。
对应抖音召回阶段："宁可多召回，不能漏掉"。

① interest   兴趣    画像标签匹配
② forgotten  遗珠 ⭐ 优质但被埋没（本项目特色）
③ trending   热榜    Star 增速（不是总量）
④ fresh      新鲜    新创建的优质仓库
⑤ following  关注    已关注作者
⑥ explore    探索    随机 + 多样性
⑦ similar    相似    item-based 协同（看 A 的人也看 B）
"""
from __future__ import annotations

import json
import random
import sqlite3

from core.config import RECALL_QUOTA
from profile.builder import TagVector
from rank.scorer import Candidate


def _row_to_candidate(row: sqlite3.Row, channel: str) -> Candidate:
    """把 JOIN 查询结果转为 Candidate。"""
    topics_raw = row["topics"] if "topics" in row.keys() else None
    try:
        topics = json.loads(topics_raw) if topics_raw else []
    except (json.JSONDecodeError, TypeError):
        topics = []

    tags_raw = row["tags_json"] if "tags_json" in row.keys() else None
    try:
        tags = json.loads(tags_raw) if tags_raw else {}
    except (json.JSONDecodeError, TypeError):
        tags = {}
    tags = {k: float(v) for k, v in tags.items()}

    def g(key: str, default=0):
        try:
            v = row[key]
            return default if v is None else v
        except (IndexError, KeyError):
            return default

    return Candidate(
        repo_id=row["id"],
        full_name=row["full_name"],
        owner=row["owner"],
        name=row["name"],
        description=g("description", None),
        language=g("language", None),
        stars=g("stars", 0),
        topics=topics if isinstance(topics, list) else [],
        tags=tags,
        impressions=g("impressions", 0),
        clicks=g("clicks", 0),
        deep_reads=g("deep_reads", 0),
        likes=g("likes", 0),
        stars_gained=g("stars_gained", 0),
        comments=g("comments", 0),
        shares=g("shares", 0),
        follows=g("follows", 0),
        quality_score=g("quality_score", 0.0),
        velocity_score=g("velocity_score", 0.0),
        forgotten_score=g("forgotten_score", 0.0),
        freshness_score=g("freshness_score", 0.0),
        pushed_at=g("pushed_at_gh", None),
        created_at=g("created_at_gh", None),
        channel=channel,
        source_channels=[channel],
    )


def _bind(params, user_id: int) -> dict:
    """把位置参数元组 + uid 合并成具名参数 dict。

    sqlite3 的 execute 只接受【一个】参数容器，
    而 _SELECT 里的 :uid 与各查询的位置占位符必须共存，
    所以在 Python 侧统一改写：所有 ? 依次命名为 q0, q1, ...
    """
    out = {"uid": user_id}
    for i, v in enumerate(params):
        out[f"q{i}"] = v
    return out


# 统一的 SELECT 片段
# ⚠️ 修正：repo_pools 没有 follows 列。
#    "关注"在本项目里是【关注作者】(follows 表)，不是仓库级的计数，
#    因此这里用 LEFT JOIN 现算该仓库作者被本用户关注的情况。
#    这同时修掉了 recall_trending 直接报 no such column 的崩溃。
_SELECT = """
SELECT r.*,
       p.impressions, p.clicks, p.deep_reads, p.likes,
       p.stars_gained, p.comments, p.shares,
       CASE WHEN f.owner IS NULL THEN 0 ELSE 1 END AS follows
FROM repos r
LEFT JOIN repo_pools p ON p.repo_id = r.id
LEFT JOIN (SELECT DISTINCT owner FROM follows WHERE user_id = :uid) f
       ON f.owner = r.owner
"""

_BASE_FILTER = """
  r.is_archived = 0 AND r.is_dead = 0
"""


# 当前生效的去重窗口（天）。recall_all 在降级重试时会临时改小它。
_DEDUP_DAYS: int = 7


def _exclude_recent(conn: sqlite3.Connection, user_id: int,
                    days: int | None = None) -> str:
    """生成"排除最近已曝光"的 SQL 片段。

    ⚠️ 这里必须做【分层去重】，不能一刀切排除：
       开发者自建部署时仓库池往往很小（几十到几百个），
       若把 7 天内看过的全部硬性排除，刷两轮就 no_candidates 空屏。
       实测：29 个仓库池，跑两轮 Feed 后第三轮直接返回 0 条。

       修正策略：
         - 7 天窗口内已曝光  → 仍排除（避免立刻重复，体验正常）
         - 但若排除后候选不足以撑起一次 Feed，由调用方走兜底
           （见 recall_all 的 fallback 逻辑），把窗口缩短到 1 天再召回。
    """
    d = _DEDUP_DAYS if days is None else days
    if d <= 0:
        return ""     # 窗口归零 = 不去重（池子已耗尽，允许重复）
    return f"""
      AND r.id NOT IN (
        SELECT repo_id FROM feed_impressions
        WHERE user_id = {int(user_id)}
          AND shown_at > datetime('now', '-{int(d)} day')
      )
    """


# 去重窗口的降级阶梯：7 天 → 1 天 → 不过滤
DEDUP_LADDER = (7, 1, 0)


# ------------------------------------------------------------------ ① 兴趣
def recall_interest(conn: sqlite3.Connection, profile: TagVector,
                    user_id: int, limit: int | None = None) -> list[Candidate]:
    """按用户画像标签匹配仓库标签。

    ⚠️ 历史缺陷：params 只传了 2 个值，而 SQL 里有 n+2 个占位符
       （n 个 top_tags + 1 个 tags_json + 1 个 LIMIT），
       参数数量对不上导致匹配永远为空 —— 兴趣召回从未生效过。
    """
    limit = limit or RECALL_QUOTA["interest"]
    if not profile.weights:
        return []

    # 取画像里权重最高的标签参与召回。
    # 但要注意：画像可能含 license 样板词（licensed/source 等），
    # 这些词匹配面太宽，这里靠停用词表过滤（见 dict/stop_words.txt）。
    top_tags = [t for t, _ in profile.top(12) if len(t) >= 2]
    if not top_tags:
        return []

    n = len(top_tags)
    # 每个标签分别匹配 topics(JSON 数组) 与 tags_json
    clauses = " OR ".join(
        f"r.topics LIKE :q{i} OR r.tags_json LIKE :q{i}" for i in range(n)
    )
    sql = f"""
      {_SELECT}
      WHERE {_BASE_FILTER}
        AND ({clauses})
        {_exclude_recent(conn, user_id)}
      ORDER BY r.quality_score DESC, r.stars DESC
      LIMIT :q{n}
    """
    # n 个标签的通配参数 + 1 个 LIMIT
    params: list = [f"%{t}%" for t in top_tags] + [limit * 4]
    rows = conn.execute(sql, _bind(params, user_id)).fetchall()

    from profile.builder import tag_overlap
    scored: list[tuple[float, Candidate]] = []
    for row in rows:
        c = _row_to_candidate(row, "interest")
        ov = tag_overlap(profile.weights, c.tags)
        if ov > 0:
            scored.append((ov, c))
    scored.sort(key=lambda kv: -kv[0])
    return [c for _, c in scored[:limit]]


# ------------------------------------------------------------------ ② 遗珠 ⭐
def recall_forgotten(conn: sqlite3.Connection, user_id: int,
                     limit: int | None = None,
                     max_impressions: int = 500) -> list[Candidate]:
    """⭐ 遗珠召回：优质但被埋没的仓库。

    这是本项目区别于所有商业推荐平台的核心通路：
    商业平台让热门更热，我们专门给冷门优质仓库曝光机会。
    """
    limit = limit or RECALL_QUOTA["forgotten"]
    sql = f"""
      {_SELECT}
      WHERE {_BASE_FILTER}
        AND r.forgotten_score > 0.4
        AND COALESCE(p.impressions, 0) < :q0
        {_exclude_recent(conn, user_id)}
      ORDER BY r.forgotten_score DESC, r.quality_score DESC
      LIMIT :q1
    """
    rows = conn.execute(sql, _bind((max_impressions, limit), user_id)).fetchall()
    return [_row_to_candidate(r, "forgotten") for r in rows]


# ------------------------------------------------------------------ ③ 热榜
def recall_trending(conn: sqlite3.Connection, user_id: int,
                    limit: int | None = None) -> list[Candidate]:
    """按 Star 增速（velocity）召回 —— 不是按总 star 数。"""
    limit = limit or RECALL_QUOTA["trending"]
    sql = f"""
      {_SELECT}
      WHERE {_BASE_FILTER}
        AND r.velocity_score > 0
        {_exclude_recent(conn, user_id)}
      ORDER BY r.velocity_score DESC
      LIMIT :q0
    """
    rows = conn.execute(sql, _bind((limit,), user_id)).fetchall()
    return [_row_to_candidate(r, "trending") for r in rows]


# ------------------------------------------------------------------ ④ 新鲜
def recall_fresh(conn: sqlite3.Connection, user_id: int,
                 limit: int | None = None,
                 days: int = 7) -> list[Candidate]:
    """新创建的优质仓库。"""
    limit = limit or RECALL_QUOTA["fresh"]
    sql = f"""
      {_SELECT}
      WHERE {_BASE_FILTER}
        AND r.created_at_gh > datetime('now', '-{int(days)} day')
        AND r.quality_score > 0.3
        {_exclude_recent(conn, user_id)}
      ORDER BY r.created_at_gh DESC
      LIMIT :q0
    """
    rows = conn.execute(sql, _bind((limit,), user_id)).fetchall()
    return [_row_to_candidate(r, "fresh") for r in rows]


# ------------------------------------------------------------------ ⑤ 关注
def recall_following(conn: sqlite3.Connection, user_id: int,
                     limit: int | None = None) -> list[Candidate]:
    """已关注作者的新动作。"""
    limit = limit or RECALL_QUOTA["following"]
    sql = f"""
      {_SELECT}
      WHERE {_BASE_FILTER}
        AND r.owner IN (SELECT owner FROM follows WHERE user_id = :q0)
        {_exclude_recent(conn, user_id)}
      ORDER BY r.pushed_at_gh DESC
      LIMIT :q1
    """
    rows = conn.execute(sql, _bind((user_id, limit), user_id)).fetchall()
    return [_row_to_candidate(r, "following") for r in rows]


# ------------------------------------------------------------------ ⑥ 探索
def recall_explore(conn: sqlite3.Connection, user_id: int,
                   limit: int | None = None,
                   rand: random.Random | None = None) -> list[Candidate]:
    """随机探索，打破信息茧房。"""
    limit = limit or RECALL_QUOTA["explore"]
    rng = rand or random.Random()
    sql = f"""
      {_SELECT}
      WHERE {_BASE_FILTER}
        AND r.quality_score > 0.2
        {_exclude_recent(conn, user_id)}
      ORDER BY RANDOM()
      LIMIT :q0
    """
    rows = conn.execute(sql, _bind((limit,), user_id)).fetchall()
    out = [_row_to_candidate(r, "explore") for r in rows]
    rng.shuffle(out)
    return out


# ------------------------------------------------------------------ ⑦ 相似
def recall_similar(conn: sqlite3.Connection, user_id: int,
                   limit: int | None = None,
                   history_n: int = 5) -> list[Candidate]:
    """item-based 协同：「看了 A 的人也看 B」。

    用 user_events 里"同一用户都深读过"的仓库做共现。
    """
    limit = limit or RECALL_QUOTA["similar"]
    sql = f"""
      {_SELECT}
      WHERE {_BASE_FILTER}
        AND r.id IN (
          SELECT DISTINCT e2.repo_id
          FROM user_events e1
          JOIN user_events e2 ON e1.user_id = e2.user_id
          WHERE e1.repo_id IN (
              SELECT repo_id FROM user_events
              WHERE user_id = :q0 AND event_type IN ('deep_read', 'star_click')
              ORDER BY created_at DESC LIMIT :q1
          )
          AND e2.repo_id != e1.repo_id
          AND e2.event_type IN ('deep_read', 'star_click')
        )
        {_exclude_recent(conn, user_id)}
      ORDER BY r.velocity_score DESC
      LIMIT :q2
    """
    rows = conn.execute(sql, _bind((user_id, history_n, limit), user_id)).fetchall()
    return [_row_to_candidate(r, "similar") for r in rows]


# ------------------------------------------------------------------ 汇总
ALL_CHANNELS = {
    "interest": recall_interest,
    "forgotten": recall_forgotten,
    "trending": recall_trending,
    "fresh": recall_fresh,
    "following": recall_following,
    "explore": recall_explore,
    "similar": recall_similar,
}


def recall_all(conn: sqlite3.Connection, profile: TagVector,
               user_id: int,
               rand: random.Random | None = None,
               min_needed: int | None = None) -> list[Candidate]:
    """执行全部召回通路，合并去重。

    同一仓库被多路召回时，保留最高分的实例，
    并记录所有来源通路（用于后续分析哪条通路有效）。

    ⭐ 去重降级：仓库池小时（自建部署常见），7 天窗口会把候选清空，
       刷两轮就空屏。这里按 7 天 → 1 天 → 不过滤 阶梯重试，
       保证"宁可重复也不空屏"——空屏比轻微重复严重得多。

    min_needed 默认取池子规模的 40%（而非固定常数），
    否则 29 个仓库的小池子永远够不到 30，每次都降级到全量不去重。
    """
    global _DEDUP_DAYS

    if min_needed is None:
        total = conn.execute("SELECT COUNT(*) AS c FROM repos").fetchone()["c"]
        min_needed = max(10, int(total * 0.4))

    original = _DEDUP_DAYS
    try:
        merged: list[Candidate] = []
        for days in DEDUP_LADDER:
            _DEDUP_DAYS = days
            merged = _do_recall(conn, profile, user_id, rand)
            if len(merged) >= min_needed or days == DEDUP_LADDER[-1]:
                if days != original:
                    print(f"[recall] 去重窗口降级 {original}d → {days}d，"
                          f"候选 {len(merged)} 条（阈值 {min_needed}）")
                return merged
        return merged
    finally:
        _DEDUP_DAYS = original


def _do_recall(conn: sqlite3.Connection, profile: TagVector,
               user_id: int,
               rand: random.Random | None = None) -> list[Candidate]:
    """单轮召回（使用当前 _DEDUP_DAYS 窗口）。"""
    results: list[Candidate] = []

    results += recall_interest(conn, profile, user_id)
    results += recall_forgotten(conn, user_id)
    results += recall_trending(conn, user_id)
    results += recall_fresh(conn, user_id)
    results += recall_following(conn, user_id)
    results += recall_explore(conn, user_id, rand=rand)
    results += recall_similar(conn, user_id)

    # 合并去重，累加来源通路
    merged: dict[int, Candidate] = {}
    for c in results:
        if c.repo_id in merged:
            exist = merged[c.repo_id]
            for ch in c.source_channels:
                if ch not in exist.source_channels:
                    exist.source_channels.append(ch)
        else:
            merged[c.repo_id] = c

    # 多路命中的仓库给一点加权（说明它被多个维度认可）
    for c in merged.values():
        c.score_detail["recall_hits"] = float(len(c.source_channels))

    return list(merged.values())
