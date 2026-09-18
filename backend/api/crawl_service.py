"""自定义关键词爬虫：用户在画像面板添加关键词，驱动 Scrapling 去 GitHub 抓真实仓库。

流程（用户视角）：
    画像面板加关键词 → 点「让爬虫去抓」→ 几秒后刷到新仓库

流程（实现视角）：
    ① Scrapling Fetcher 请求 GitHub Search API
       （curl_cffi 浏览器指纹伪装 + 自动重试，比裸 requests 抗封）
    ② 结果规范化：沿用 seed_real 的字段映射 / 分数公式 / 许可证语义
    ③ 入库（INSERT OR IGNORE，已存在的顺手刷新 stars）+ ensure_pool
    ④ 直接入队（reason="你抓的关键词「xx」"），用户马上能刷到
    ⑤ README/标签由后台任务异步补齐（jsdelivr CDN，接口秒回）

⚠️ 本机网络环境：证书链对 api.github.com 验证必挂（curl 60 / certifi 双双失败），
   只读公开数据，直接 verify=False。若日后网络环境修复，可改回 True。
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

from db.connection import get_conn
from rank.scorer import Candidate
from tags.extractor import extract_readme_tags

# Scrapling 主通道（浏览器指纹伪装）；requests 兜底由调用方感知失败即可
from scrapling.fetchers import Fetcher

# ---------------------------------------------------------------- 常量

MAX_KEYWORDS_PER_USER = 12
MAX_KEYWORDS_PER_CRAWL = 5
CRAWL_COOLDOWN_SEC = 60.0        # 两次抓取的最小间隔（GitHub search 限流 10/min）
SEARCH_URL = "https://api.github.com/search/repositories"
README_CANDIDATES = ("README.md", "readme.md")

LICENSE_SAFE = {
    "MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "Unlicense", "CC0-1.0", "PostgreSQL", "PSF-2.0", "0BSD", "Zlib",
}
LICENSE_CAUTION = {
    "GPL-3.0", "AGPL-3.0", "MPL-2.0", "EPL-2.0", "CC-BY-NC-SA-4.0",
    "CC-BY-NC-4.0", "LGPL-3.0", "LGPL-2.1",
}
LICENSE_RISKY = {"GPL-2.0", "SSPL-1.0", "RSALv2", "EUPL-1.2"}

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS user_keywords (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  keyword    TEXT    NOT NULL,
  weight     REAL    NOT NULL DEFAULT 1.0,
  created_at TEXT    NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, keyword)
);
CREATE INDEX IF NOT EXISTS idx_kw_user ON user_keywords(user_id, weight DESC);
"""

_crawl_lock = threading.Lock()
_last_crawl_at = 0.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_gh_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def license_risk(spdx: str | None) -> tuple[str | None, str]:
    if not spdx or spdx in ("NOASSERTION", "Other"):
        return (spdx if spdx not in ("NOASSERTION", "Other") else None, "unknown")
    if spdx in LICENSE_SAFE:
        return spdx, "safe"
    if spdx in LICENSE_CAUTION:
        return spdx, "caution"
    if spdx in LICENSE_RISKY:
        return spdx, "risky"
    return spdx, "unknown"


# ---------------------------------------------------------------- 关键词 CRUD

def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_TABLE_SQL)


def list_keywords(conn: sqlite3.Connection, user_id: int) -> list[dict[str, Any]]:
    ensure_tables(conn)
    rows = conn.execute(
        """SELECT id, keyword, weight, created_at
           FROM user_keywords WHERE user_id = ? ORDER BY weight DESC, id DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def add_keyword(conn: sqlite3.Connection, user_id: int, keyword: str) -> dict[str, Any]:
    ensure_tables(conn)
    kw = keyword.strip().lower()
    if not (2 <= len(kw) <= 40):
        raise ValueError("关键词长度需在 2~40 字符之间")
    n = conn.execute(
        "SELECT COUNT(*) AS n FROM user_keywords WHERE user_id = ?", (user_id,)
    ).fetchone()["n"]
    if n >= MAX_KEYWORDS_PER_USER:
        raise ValueError(f"最多 {MAX_KEYWORDS_PER_USER} 个自定义关键词，先删一个再加")
    cur = conn.execute(
        """INSERT INTO user_keywords (user_id, keyword) VALUES (?, ?)
           ON CONFLICT(user_id, keyword) DO NOTHING""",
        (user_id, kw),
    )
    row = conn.execute(
        "SELECT id, keyword, weight, created_at FROM user_keywords WHERE user_id = ? AND keyword = ?",
        (user_id, kw),
    ).fetchone()
    return dict(row) | {"inserted": cur.rowcount > 0}


def remove_keyword(conn: sqlite3.Connection, user_id: int, kid: int) -> bool:
    ensure_tables(conn)
    cur = conn.execute(
        "DELETE FROM user_keywords WHERE id = ? AND user_id = ?", (kid, user_id)
    )
    return cur.rowcount > 0


def get_user_keywords(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """给补货方向注入用：只返回关键词字符串列表。"""
    try:
        ensure_tables(conn)
    except sqlite3.OperationalError:
        return []
    return [
        r["keyword"]
        for r in conn.execute(
            """SELECT keyword FROM user_keywords
               WHERE user_id = ? ORDER BY weight DESC, id DESC""",
            (user_id,),
        ).fetchall()
    ]


# ---------------------------------------------------------------- Scrapling 抓取

def _gh_search(keyword: str, per_page: int) -> list[dict[str, Any]]:
    """用 Scrapling Fetcher 搜 GitHub。任何失败返回空列表（不拖垮整次抓取）。"""
    try:
        page = Fetcher.get(
            SEARCH_URL,
            params={
                "q": f"{keyword} stars:>20",
                "per_page": per_page,
            },
            timeout=25,
            verify=False,   # 本机证书链对 GitHub 验证必挂，只读公开数据
        )
        if page.status != 200:
            return []
        body = page.body
        if isinstance(body, bytes):
            body = body.decode("utf-8", "replace")
        data = json.loads(body)
        return data.get("items", []) or []
    except Exception:
        return []


# ---------------------------------------------------------------- 规范化入库

def _normalize(it: dict[str, Any], *, strict: bool = True,
               source: str = "crawl") -> dict[str, Any] | None:
    """GitHub item → repos 行。

    strict=True（爬虫/点星）：过滤 fork、归档、无描述、低星 —— 保语料质量
    strict=False（自建仓库）：无条件收 —— 用户亲手做的仓库是 0.5 权重的最强信号，
                              哪怕 0 star、没写描述也必须进语料库
    """
    if it.get("fork") and source != "owned":
        return None
    if it.get("archived") and source != "owned":
        return None
    desc = (it.get("description") or "").strip()
    stars = int(it.get("stargazers_count") or 0)
    if strict:
        if stars < 20:
            return None
        if not desc:
            return None
    owner, _, name = it["full_name"].partition("/")
    if not desc:
        desc = f"{owner}/{name}"
    pushed = _parse_gh_time(it["pushed_at"])
    created = _parse_gh_time(it["created_at"])
    days_push = max(0.0, (_now_utc() - pushed).total_seconds() / 86400)
    days_created = max(0.0, (_now_utc() - created).total_seconds() / 86400)
    stars_norm = min(1.0, math.log10(stars + 1) / 5.3)
    lic = it.get("license") or {}
    spdx, risk = license_risk(lic.get("spdx_id"))
    return dict(
        github_id=it["id"],
        owner=owner,
        name=name,
        full_name=it["full_name"],
        description=desc,
        readme=f"# {name}\n\n{desc}\n",   # 先兜底，后台任务再补真 README
        language=it.get("language"),
        topics=it.get("topics") or [],
        license=spdx,
        risk=risk,
        stars=stars,
        forks=int(it.get("forks_count") or 0),
        open_issues=int(it.get("open_issues_count") or 0),
        size_kb=int(it.get("size") or 0),
        created=_fmt(created),
        pushed=_fmt(pushed),
        homepage=it.get("homepage") or None,
        branch=it.get("default_branch"),
        quality=round(min(0.97, 0.55 + 0.38 * stars_norm), 2),
        velocity=round(min(0.98, math.exp(-days_push / 90)), 2),
        freshness=round(min(1.0, math.exp(-days_created / 120)), 2),
        forgotten=round(max(0.02, 0.10 * (1 - stars_norm)), 2),
    )


_INSERT_SQL = """
INSERT INTO repos (
    github_id, owner, name, full_name, description, readme_md, readme_len,
    language, topics, license_spdx, license_risk,
    stars, forks, watchers, open_issues, contributors,
    has_ci, has_tests, size_kb, homepage, default_branch,
    created_at_gh, pushed_at_gh,
    quality_score, velocity_score, forgotten_score, freshness_score,
    content_vec, tags_json, tags_updated_at, first_seen_at, is_archived, is_dead
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
ON CONFLICT(full_name) DO UPDATE SET
    stars           = excluded.stars,
    forks           = excluded.forks,
    pushed_at_gh    = excluded.pushed_at_gh,
    velocity_score  = excluded.velocity_score,
    quality_score   = excluded.quality_score,
    description     = excluded.description
-- ⚠️ 必须再处理 github_id 冲突：repos 上 full_name 与 github_id **都是 UNIQUE**，
--    仓库改名后 full_name 变了、github_id 不变，只写 full_name 冲突会直接
--    IntegrityError: UNIQUE constraint failed: repos.github_id（实测把整轮爬虫打断）。
--    这里顺便把 full_name/owner/name 一起更新 —— 改名要跟着走。
ON CONFLICT(github_id) DO UPDATE SET
    full_name       = excluded.full_name,
    owner           = excluded.owner,
    name            = excluded.name,
    stars           = excluded.stars,
    forks           = excluded.forks,
    pushed_at_gh    = excluded.pushed_at_gh,
    velocity_score  = excluded.velocity_score,
    quality_score   = excluded.quality_score,
    description     = excluded.description
"""


def _insert_repo(conn: sqlite3.Connection, r: dict[str, Any]) -> int:
    """入库并返回 repo id。已存在（full_name 冲突）时刷新数据。

    ⚠️ 入库前对 README/描述做**密钥脱敏**：第三方 README 里常有别人泄露的真 key
       （实测 skyagi 的 README 里就有一个 sk- 开头的 OpenAI Key），
       收进数据集等于帮它二次传播，也会被 GitHub Push Protection 拦下。
    """
    from quality.redact import redact_secrets
    r = {**r, "readme": redact_secrets(r.get("readme")),
         "description": redact_secrets(r.get("description"))}
    rng = random.Random(r["github_id"] or r["full_name"])
    tags = extract_readme_tags(r["readme"], topk=50,
                                   topics=r["topics"], name=r["name"])
    for t in r["topics"][:8]:
        t = str(t).strip().lower()
        if t and len(t) > 1 and t not in tags:
            tags[t] = round(0.5 + rng.random() * 0.3, 3)
    tags = dict(sorted(tags.items(), key=lambda kv: -kv[1])[:50])
    conn.execute(
        _INSERT_SQL,
        (
            r["github_id"], r["owner"], r["name"], r["full_name"],
            r["description"], r["readme"], len(r["readme"]),
            r["language"], json.dumps(r["topics"]), r["license"], r["risk"],
            r["stars"], r["forks"], int(r["stars"] * 0.03),
            r["open_issues"], rng.randint(2, 40),
            0, 0, r["size_kb"], r["homepage"], r["branch"],
            r["created"], r["pushed"],
            r["quality"], r["velocity"], r["forgotten"], r["freshness"],
            None,
            json.dumps(tags, ensure_ascii=False),
            _fmt(_now_utc()),
            r["created"],
        ),
    )
    row = conn.execute(
        "SELECT id FROM repos WHERE full_name = ?", (r["full_name"],)
    ).fetchone()
    return int(row["id"]) if row else 0


# ---------------------------------------------------------------- 后台 README 补齐

def _bg_enrich_readmes(repo_ids: list[int]) -> None:
    """后台任务：给刚抓的仓库拉真 README（jsdelivr CDN）并重提标签。"""

    def fetch(full_name: str, branch: str) -> str | None:
        for cand in (
            f"https://cdn.jsdelivr.net/gh/{full_name}@{branch}/README.md",
            f"https://cdn.jsdelivr.net/gh/{full_name}@{branch}/readme.md",
        ):
            try:
                import requests as _rq
                resp = _rq.get(cand, timeout=8, verify=False,
                               headers={"User-Agent": "recofeed/1.0"})
                if resp.status_code == 200 and resp.text.strip():
                    return resp.text[:4000]
            except Exception:
                continue
        return None

    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, full_name, COALESCE(default_branch,'main') AS b, topics
               FROM repos WHERE id IN (%s)""" %
            ",".join("?" * len(repo_ids)),
            repo_ids,
        ).fetchall()
        todo = [
            (r["id"], r["full_name"], r["b"], json.loads(r["topics"] or "[]"))
            for r in rows
        ]

    def fetch_one(t):
        rid, full, branch, _ = t
        return rid, fetch(full, branch)

    with ThreadPoolExecutor(max_workers=6) as ex:
        fetched = list(ex.map(fetch_one, todo))

    rng = random.Random(42)
    with get_conn() as conn:
        for (rid, _f, _b, topics), (_r, md) in zip(todo, fetched):
            if not md:
                continue
            tags = extract_readme_tags(md, topk=50,
                                       topics=r["topics"], name=r["name"])
            for t in topics[:8]:
                t = str(t).strip().lower()
                if t and len(t) > 1 and t not in tags:
                    tags[t] = round(0.5 + rng.random() * 0.3, 3)
            tags = dict(sorted(tags.items(), key=lambda kv: -kv[1])[:50])
            # ⭐ 拿到 README 的同时判定「是否已停更」：README 里写着"本项目停更/
            #    不再维护"的仓库不该被推荐（用户实测反馈），标记 is_dead 即被
            #    召回/补货/搜索全线排除。判定逻辑见 quality/lifecycle.py。
            from quality.lifecycle import detect_stopped
            note = detect_stopped(md)
            conn.execute(
                """UPDATE repos SET readme_md=?, readme_len=?,
                       tags_json=?, tags_updated_at=datetime('now'),
                       is_dead = CASE WHEN ? IS NULL THEN is_dead ELSE 1 END,
                       lifecycle_note = COALESCE(?, lifecycle_note)
                   WHERE id=?""",
                (md, len(md), json.dumps(tags, ensure_ascii=False), note, note, rid),
            )


# ---------------------------------------------------------------- 主入口

def crawl_keywords(
    conn: sqlite3.Connection,
    user_id: int,
    keywords: list[str] | None = None,
    per_keyword: int = 8,
    background_enrich=None,     # fastapi BackgroundTasks，可空
) -> dict[str, Any]:
    """按关键词驱动爬虫：抓 GitHub → 入库 → 直接入队。

    keywords 为空时使用用户保存的自定义关键词。
    """
    global _last_crawl_at

    # 抓取频控：GitHub search 限流 10 次/分钟，连续点会烧配额
    with _crawl_lock:
        wait = CRAWL_COOLDOWN_SEC - (time.time() - _last_crawl_at)
        if wait > 0:
            raise ValueError(f"抓得太频繁，{int(wait)} 秒后再试")
        _last_crawl_at = time.time()

    if not keywords:
        keywords = get_user_keywords(conn, user_id)
    keywords = [k.strip().lower() for k in (keywords or []) if k.strip()][:MAX_KEYWORDS_PER_CRAWL]
    if not keywords:
        raise ValueError("没有可用的关键词：先在画像面板添加，或请求里带上 keywords")
    per_keyword = max(1, min(15, per_keyword))

    # ① 逐关键词抓取（串行 —— search API 限流 10/min，串行最稳）
    details: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for kw in keywords:
        items = _gh_search(kw, per_keyword)
        kept = []
        for it in items:
            r = _normalize(it)
            if r:
                r["_kw"] = kw
                kept.append(r)
        rows.extend(kept)
        details.append({"keyword": kw, "found": len(items), "kept": len(kept)})

    # ② 入库 + 建池
    new_repo_ids: list[int] = []
    inserted_now = 0
    with get_conn() as wconn:
        wconn.executescript(_TABLE_SQL)
        for r in rows:
            before = wconn.execute(
                "SELECT 1 FROM repos WHERE full_name = ?", (r["full_name"],)
            ).fetchone()
            rid = _insert_repo(wconn, r)
            if rid:
                from pool.state_machine import ensure_pool
                ensure_pool(wconn, rid)
                if not before:
                    inserted_now += 1
                    new_repo_ids.append(rid)

    # ③ 入队：排除已 dislike 的仓库
    with get_conn() as wconn:
        disliked = {
            r["repo_id"] for r in wconn.execute(
                """SELECT DISTINCT repo_id FROM user_events
                   WHERE user_id = ? AND event_type = 'dislike'""",
                (user_id,),
            )
        }
        candidates: list[Candidate] = []
        reasons: dict[int, str] = {}
        for r in rows:
            rid_row = wconn.execute(
                "SELECT id FROM repos WHERE full_name = ?", (r["full_name"],)
            ).fetchone()
            if not rid_row:
                continue
            rid = int(rid_row["id"])
            if rid in disliked:
                continue
            inq = wconn.execute(
                "SELECT 1 FROM feed_queue WHERE user_id = ? AND repo_id = ?",
                (user_id, rid),
            ).fetchone()
            if inq:
                continue
            candidates.append(Candidate(
                repo_id=rid,
                full_name=r["full_name"],
                owner=r["owner"],
                name=r["name"],
                description=r["description"],
                language=r["language"],
                stars=r["stars"],
                topics=r["topics"],
                channel="custom_crawl",
                # ⚠️ 必须给 score：出队时队列里存的 score 直接作为「匹配度」下发，
                #    不传就是 0.00（用户主动抓的关键词，匹配度理应偏高）。
                score=float(r["quality"]),
            ))
            reasons[rid] = f"你抓的关键词「{r['_kw']}」"

        enqueued = 0
        if candidates:
            from feed import queue as Q
            enqueued = Q.enqueue_candidates(
                wconn, user_id, candidates,
                batch_id=Q.new_batch_id(),
                reasons=reasons,
            )
            Q.log_refill(
                wconn, user_id,
                trigger="custom_crawl",
                queue_before=0,
                fetched=len(rows),
                enqueued=enqueued,
                strategy={"queries": keywords, "source": "user_keywords"},
            )

    # ④ README 后台补齐（接口秒回）
    if background_enrich is not None and new_repo_ids:
        background_enrich.add_task(_bg_enrich_readmes, new_repo_ids)

    return {
        "ok": True,
        "keywords": keywords,
        "found": len(rows),
        "new_repos": inserted_now,
        "enqueued": enqueued,
        "details": details,
    }
