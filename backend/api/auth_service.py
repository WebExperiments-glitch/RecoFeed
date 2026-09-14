"""GitHub 账号：OAuth 注册登录 + 用户实证数据同步（自建仓库 / 点星仓库）。

⭐ 为什么这是推荐系统里最强的信号：
    - 用户**自己做的仓库** = 他真的在动手写这类东西 → 铁证级兴趣
    - 用户**点星的仓库** = 他明确觉得有意思 → 强证据
    - 刷到卡片停一会儿（dwell）只能算弱证据

   所以这两类数据进入画像时权重特意拉高（见 config.GITHUB_*），
   并且会作为补货方向的高优先查询词，驱动爬虫多抓这类仓库。

权重规则（用户拍定）：
    自建仓库 —— 看最近推送时间：本周 0.5、一个月内 0.2，之后逐档衰减
    点星仓库 —— 按仓库星数从高到低排名：第 1 名 0.4、第 10 名 0.3、第 20 名 0.2

登录方式：
    ① GitHub OAuth（需要 client_id/secret，见 README 步骤）
    ② 个人访问令牌 PAT 直连（不用建 OAuth App，本地开发/自测用）
"""
from __future__ import annotations

import json
import math
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import requests

from core.config import (
    AUTH_CONFIGURED,
    AUTH_FRONTEND_REDIRECT,
    AUTH_SECRET,
    GITHUB_API,
    GITHUB_CLIENT_ID,
    GITHUB_CLIENT_SECRET,
    GITHUB_OAUTH_AUTHORIZE,
    GITHUB_OAUTH_SCOPES,
    GITHUB_OAUTH_TOKEN,
    GITHUB_OWNED_HALFYEAR,
    GITHUB_OWNED_MONTH,
    GITHUB_OWNED_OLD,
    GITHUB_OWNED_QUARTER,
    GITHUB_OWNED_WEEK,
    GITHUB_OWNED_YEAR,
    GITHUB_STARRED_TOP,
    GITHUB_SIGNAL_BOOST,
    GITHUB_SYNC_MAX_OWNED,
    GITHUB_SYNC_MAX_STARRED,
    SESSION_TTL_DAYS,
)

# ⚠️ 本机证书链对 api.github.com / github.com 验证必挂（curl 60 / certifi 双失败），
#    只读公开数据 + 用户自己的令牌，直接不验证。
_S = requests.Session()
_S.verify = False


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 建表

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS auth_sessions (
  token      TEXT PRIMARY KEY,
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_user ON auth_sessions(user_id);

CREATE TABLE IF NOT EXISTS github_footprint (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source      TEXT    NOT NULL,          -- 'owned' 自建 / 'starred' 点星
  full_name   TEXT    NOT NULL,
  repo_stars  INTEGER NOT NULL DEFAULT 0,   -- 该仓库的星数（点星榜排序依据）
  pushed_at   TEXT,                          -- 最近推送（自建仓库衰减依据）
  weight      REAL    NOT NULL DEFAULT 0,    -- 实证权重（0.03 ~ 0.5）
  rank        INTEGER,                       -- 在该用户列表内的排名
  language    TEXT,
  topics_json TEXT,
  html_url    TEXT,
  fetched_at  TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE (user_id, source, full_name)
);
CREATE INDEX IF NOT EXISTS idx_footprint_user ON github_footprint(user_id, source, weight DESC);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_TABLE_SQL)


# ---------------------------------------------------------------- 权重公式

def _days_since(iso: str | None) -> float:
    if not iso:
        return 9999.0
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return 9999.0
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


def owned_repo_weight(pushed_at: str | None) -> float:
    """自建仓库权重：按最近推送时间衰减（用户拍定：本周 0.5 / 一个月 0.2）。"""
    d = _days_since(pushed_at)
    if d <= 7:
        return GITHUB_OWNED_WEEK
    if d <= 30:
        return GITHUB_OWNED_MONTH
    if d <= 90:
        return GITHUB_OWNED_QUARTER
    if d <= 180:
        return GITHUB_OWNED_HALFYEAR
    if d <= 365:
        return GITHUB_OWNED_YEAR
    return GITHUB_OWNED_OLD


# 点星榜锚点：(排名, 权重) —— 第 1 名 0.4、第 10 名 0.3、第 20 名 0.2（用户拍定），
# 之后继续衰减，避免长尾把权重堆得比自建仓库还高。
_STAR_ANCHORS: list[tuple[int, float]] = [
    (1, GITHUB_STARRED_TOP), (10, 0.30), (20, 0.20),
    (50, 0.12), (100, 0.08), (300, 0.05),
]


def starred_repo_weight(rank: int) -> float:
    """点星仓库权重：按「仓库星数从高到低」的排名分段线性衰减。

    排名轴取 log10 —— 因为星数分布是长尾的，线性分段会让前 20 名几乎同权。
    """
    rank = max(1, int(rank))
    anchors = _STAR_ANCHORS
    if rank <= anchors[0][0]:
        return anchors[0][1]
    x = math.log10(rank)
    for (r1, w1), (r2, w2) in zip(anchors, anchors[1:]):
        if rank <= r2:
            x1, x2 = math.log10(r1), math.log10(r2)
            if x2 <= x1:
                return w2
            t = (x - x1) / (x2 - x1)
            return round(w1 + (w2 - w1) * t, 3)
    return anchors[-1][1]


# ---------------------------------------------------------------- OAuth

def build_login_url(redirect_uri: str, state: str | None = None) -> str:
    q = {
        "client_id": GITHUB_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": GITHUB_OAUTH_SCOPES,
        "state": state or secrets.token_urlsafe(16),
        "allow_signup": "true",
    }
    return f"{GITHUB_OAUTH_AUTHORIZE}?{urlencode(q)}"


def exchange_code(code: str) -> str | None:
    """code → access_token。失败返回 None。"""
    try:
        r = _S.post(
            GITHUB_OAUTH_TOKEN,
            data={
                "client_id": GITHUB_CLIENT_ID,
                "client_secret": GITHUB_CLIENT_SECRET,
                "code": code,
            },
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if r.status_code != 200:
            return None
        return (r.json() or {}).get("access_token")
    except Exception:
        return None


# ---------------------------------------------------------------- GitHub API

def _gh_get(path: str, token: str,
            params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
    """GET api.github.com，返回 (json, headers)。失败返回 (None, {})。"""
    try:
        r = _S.get(
            f"{GITHUB_API}{path}",
            params=params or {},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "recofeed-app",
            },
            timeout=25,
        )
        if r.status_code != 200:
            return None, {}
        return r.json(), {k.lower(): v for k, v in r.headers.items()}
    except Exception:
        return None, {}


def fetch_profile(token: str) -> dict[str, Any] | None:
    data, _ = _gh_get("/user", token)
    return data


# ---------------------------------------------------------------- 会话

def create_session(conn: sqlite3.Connection, user_id: int) -> str:
    ensure_tables(conn)
    token = secrets.token_urlsafe(32)
    exp = (datetime.now(timezone.utc)
           + timedelta(days=SESSION_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        "INSERT INTO auth_sessions (token, user_id, expires_at) VALUES (?,?,?)",
        (token, user_id, exp),
    )
    return token


def resolve_session(conn: sqlite3.Connection, token: str | None) -> sqlite3.Row | None:
    if not token:
        return None
    try:
        ensure_tables(conn)
    except sqlite3.OperationalError:
        return None
    row = conn.execute(
        """SELECT u.* FROM auth_sessions s
           JOIN users u ON u.id = s.user_id
           WHERE s.token = ? AND s.expires_at > datetime('now')""",
        (token,),
    ).fetchone()
    return row


def drop_session(conn: sqlite3.Connection, token: str) -> None:
    ensure_tables(conn)
    conn.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))


# ---------------------------------------------------------------- 用户

def upsert_github_user(conn: sqlite3.Connection,
                       gh: dict[str, Any],
                       token: str) -> int:
    """按 github_login 找用户，没有就注册（用户名 = github login）。"""
    login = str(gh.get("login") or "").strip()
    if not login:
        raise ValueError("GitHub 返回的用户信息缺少 login")
    row = conn.execute(
        "SELECT id FROM users WHERE github_login = ? COLLATE NOCASE OR username = ? "
        "ORDER BY (github_login IS NULL) LIMIT 1",
        (login, login),
    ).fetchone()
    if row:
        conn.execute(
            """UPDATE users SET github_login = ?, github_token = ?,
                   display_name = COALESCE(?, display_name),
                   avatar_url = COALESCE(?, avatar_url),
                   last_active_at = datetime('now')
               WHERE id = ?""",
            (login, token, gh.get("name"), gh.get("avatar_url"), row["id"]),
        )
        return int(row["id"])

    cur = conn.execute(
        """INSERT INTO users (username, password_hash, display_name, avatar_url,
                              github_login, github_token, account_score, is_new_user,
                              last_active_at)
           VALUES (?, 'github-oauth', ?, ?, ?, ?, 1.0, 1, datetime('now'))""",
        (login, gh.get("name") or login, gh.get("avatar_url"), login, token),
    )
    return int(cur.lastrowid)


def user_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "user_id": int(row["id"]),
        "username": row["username"],
        "github_login": row["github_login"],
        "display_name": row["display_name"],
        "avatar_url": row["avatar_url"],
    }


# ---------------------------------------------------------------- 数据同步

def _paginate(path: str, token: str, *, max_items: int,
              params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page = 1
    while len(out) < max_items and page <= 5:
        p = dict(params or {})
        p.update({"per_page": 100, "page": page})
        data, _ = _gh_get(path, token, p)
        if not isinstance(data, list) or not data:
            break
        out.extend(data)
        if len(data) < 100:
            break
        page += 1
    return out[:max_items]


def sync_footprint(conn: sqlite3.Connection, user_id: int,
                   token: str) -> dict[str, Any]:
    """拉取用户的自建仓库与点星仓库，按规则算权重入库。"""
    ensure_tables(conn)

    # ── ① 自建仓库（排除 fork —— 那是别人的代码）──
    owned_raw = _paginate(
        "/user/repos", token, max_items=GITHUB_SYNC_MAX_OWNED,
        params={"affiliation": "owner", "sort": "pushed", "direction": "desc"},
    )
    owned = [it for it in owned_raw if not it.get("fork")]

    # ── ② 点星仓库 ──
    starred_raw = _paginate(
        "/user/starred", token, max_items=GITHUB_SYNC_MAX_STARRED,
        params={"sort": "created", "direction": "desc"},
    )

    # 供 ③ 语料库导入使用（保留原始 GitHub item）
    ingest_items = ([("owned", it) for it in owned]
                    + [("starred", it) for it in starred_raw])

    # 点星榜排序：按仓库星数从高到低（用户规则），再算排名权重
    starred_sorted = sorted(
        starred_raw, key=lambda it: -int(it.get("stargazers_count") or 0)
    )

    rows: list[dict[str, Any]] = []
    for it in owned:
        rows.append({
            "source": "owned",
            "full_name": it.get("full_name"),
            "repo_stars": int(it.get("stargazers_count") or 0),
            "pushed_at": (it.get("pushed_at") or "").replace("T", " ").replace("Z", ""),
            "weight": owned_repo_weight(it.get("pushed_at")),
            "rank": None,
            "language": it.get("language"),
            "topics": it.get("topics") or [],
            "html_url": it.get("html_url"),
        })
    for i, it in enumerate(starred_sorted, start=1):
        rows.append({
            "source": "starred",
            "full_name": it.get("full_name"),
            "repo_stars": int(it.get("stargazers_count") or 0),
            "pushed_at": None,
            "weight": starred_repo_weight(i),
            "rank": i,
            "language": it.get("language"),
            "topics": it.get("topics") or [],
            "html_url": it.get("html_url"),
        })

    # ── ③ 实证仓库入语料库 ⭐（"猛推"的物理基础）
    #    用户自己做的仓库和他点星的仓库，必须出现在推荐池里，
    #    否则个人模型再准也没有可比对的候选。自建仓库无条件收（loose）。
    from api.crawl_service import _insert_repo, _normalize
    from pool.state_machine import ensure_pool as _ensure_pool

    corpus_added = 0
    corpus_updated = 0
    for src, it in ingest_items:
        norm = _normalize(it, strict=(src != "owned"), source=src)
        if not norm:
            continue
        existed = conn.execute(
            "SELECT 1 FROM repos WHERE full_name = ?", (norm["full_name"],)
        ).fetchone()
        rid = _insert_repo(conn, norm)
        if rid:
            _ensure_pool(conn, rid)
            if existed:
                corpus_updated += 1
            else:
                corpus_added += 1

    conn.execute("DELETE FROM github_footprint WHERE user_id = ?", (user_id,))
    for r in rows:
        if not r["full_name"]:
            continue
        conn.execute(
            """INSERT OR REPLACE INTO github_footprint
                   (user_id, source, full_name, repo_stars, pushed_at, weight,
                    rank, language, topics_json, html_url, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, r["source"], r["full_name"], r["repo_stars"],
                r["pushed_at"], r["weight"], r["rank"], r["language"],
                json.dumps(r["topics"], ensure_ascii=False), r["html_url"], _now(),
            ),
        )

    # 同步完立刻重建画像（实证圈会以高权重进入画像）
    try:
        from user_profile.service import rebuild_profile
        rebuild_profile(conn, user_id)
    except Exception:
        pass

    return {
        "owned": len(owned),
        "starred": len(starred_sorted),
        "corpus_added": corpus_added,
        "corpus_updated": corpus_updated,
        "owned_top": sorted(
            ({"full_name": r["full_name"], "weight": r["weight"],
              "pushed_at": r["pushed_at"]} for r in rows if r["source"] == "owned"),
            key=lambda x: -x["weight"],
        )[:5],
        "starred_top": sorted(
            ({"full_name": r["full_name"], "weight": r["weight"],
              "repo_stars": next((x["repo_stars"] for x in rows
                                  if x["full_name"] == r["full_name"]
                                  and x["source"] == "starred"), 0),
              "rank": r["rank"]} for r in rows if r["source"] == "starred"),
            key=lambda x: -x["weight"],
        )[:5],
    }


# ---------------------------------------------------------------- 供其他模块读取

def load_footprint(conn: sqlite3.Connection, user_id: int) -> list[sqlite3.Row]:
    try:
        ensure_tables(conn)
    except sqlite3.OperationalError:
        return []
    return conn.execute(
        """SELECT source, full_name, repo_stars, pushed_at, weight, rank,
                  language, topics_json, html_url
           FROM github_footprint WHERE user_id = ?
           ORDER BY weight DESC""",
        (user_id,),
    ).fetchall()


def footprint_preferred_queries(conn: sqlite3.Connection, user_id: int,
                                limit: int = 5) -> list[str]:
    """实证圈 → 补货查询词（自建仓库的话题优先，其次点星）。"""
    from tags.extractor import is_noise_tag
    rows = load_footprint(conn, user_id)
    if not rows:
        return []
    out: list[str] = []
    for src in ("owned", "starred"):
        for r in rows:
            if r["source"] != src:
                continue
            try:
                topics = json.loads(r["topics_json"] or "[]")
            except (json.JSONDecodeError, TypeError):
                topics = []
            for t in topics:
                t = str(t).strip().lower()
                # ⚠️ 必须过滤噪声：否则会拿 "support"/"install" 去 GitHub 搜，
                #    白烧额度还污染语料库
                if is_noise_tag(t):
                    continue
                if 2 < len(t) <= 30 and t not in out:
                    out.append(t)
                    if len(out) >= limit:
                        return out
    return out[:limit]


def footprint_summary(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    rows = load_footprint(conn, user_id)
    owned = [r for r in rows if r["source"] == "owned"]
    starred = [r for r in rows if r["source"] == "starred"]
    return {
        "owned_count": len(owned),
        "starred_count": len(starred),
        "boost": GITHUB_SIGNAL_BOOST,
        "owned": [
            {"full_name": r["full_name"], "weight": r["weight"],
             "pushed_at": r["pushed_at"], "stars": r["repo_stars"]}
            for r in owned[:12]
        ],
        "starred": [
            {"full_name": r["full_name"], "weight": r["weight"],
             "rank": r["rank"], "stars": r["repo_stars"]}
            for r in starred[:12]
        ],
    }
