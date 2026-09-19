"""AI 项目速读：把 README 变成「一眼看懂这个项目」的结构化简介。

为什么做
    用户/评测反馈：卡片只给 star/fork/一行描述，判断不了项目的真实深度；
    而 README 动辄几千字，前端又不可能让人先读完再决定要不要收藏。
    于是让 LLM 先读一遍，输出固定结构的速读卡：
        一句话定位 / 技术栈 / 核心亮点（优先取自 Features、Quick Start）/ 适合谁 / 成熟度

成本控制
    - **按需生成**（前端点「生成 AI 速读」才调用），不随 Feed 自动跑
    - 结果落库缓存，并以 `readme_len` 作为失效依据（README 变了才重生成）
    - 一次只处理一个仓库：README 进上下文很长，批量会把几个项目的 README 混在一起

复用
    走 translate_service 的模型级联（Agnes 免费档 → 备用模型），与翻译/解释共用限额与冷却。
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

from api.insight_service import _llm_json  # 复用级联调用 + JSON 解析

BRIEF_SYSTEM = (
    "你是开源项目速读器。用户会在信息流里刷到大量仓库，没时间读 README。\n"
    "请阅读给定的 README，输出一份**结构化速读**，让人 10 秒内判断这个项目值不值得深入。\n"
    "要求：\n"
    "1. one_liner：一句话说清「这是什么、解决什么问题」，≤40 字，不要复述仓库名；\n"
    "2. stack：从 README 里能确认的技术栈/依赖（如 Python、PyTorch、FastAPI、Docker、CUDA），"
    "只列真实出现的，最多 6 个；没有就留空数组；\n"
    "3. highlights：3~5 条核心亮点，**优先取 README 的 Features / Quick Start / 架构 部分**，"
    "每条 ≤28 字，要有信息量（能做什么、比同类强在哪、有什么独特设计），不要写「支持多种功能」这种空话；\n"
    "4. audience：适合谁用，≤28 字（如「想做本地语音克隆的开发者」）；\n"
    "5. maturity：成熟度判断，从这几个词里选一个并补一句依据："
    "「工业级」「生产可用」「实验性」「个人项目」「停更/归档」，≤24 字；\n"
    "6. 如果 README 内容太少、看不出项目在做什么，就在 one_liner 里直说"
    "「README 未提供足够信息」，其余字段留空。\n"
    "只输出 JSON：{\"one_liner\":\"…\",\"stack\":[],\"highlights\":[],\"audience\":\"…\",\"maturity\":\"…\"}"
)

_CACHE_TTL_DAYS = 90          # 兜底过期（正常靠 readme_len 变化失效）
README_BUDGET = 6000          # 送进上下文的 README 上限（实测最长 5.8KB，基本全量）


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS repo_briefs (
          repo_id     INTEGER PRIMARY KEY,
          readme_len  INTEGER NOT NULL,
          brief_json  TEXT    NOT NULL,
          model       TEXT,
          created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        """
    )


def get_cached(conn: sqlite3.Connection, repo_id: int,
               readme_len: int) -> dict[str, Any] | None:
    """命中缓存才返回（README 长度变了视为失效 —— 内容更新过就该重新速读）。"""
    ensure_tables(conn)
    row = conn.execute(
        "SELECT readme_len, brief_json FROM repo_briefs WHERE repo_id = ?",
        (repo_id,),
    ).fetchone()
    if not row or int(row["readme_len"]) != int(readme_len):
        return None
    try:
        return json.loads(row["brief_json"])
    except json.JSONDecodeError:
        return None


def _readme_of(conn: sqlite3.Connection, repo_id: int) -> dict[str, Any] | None:
    return conn.execute(
        """SELECT id, full_name, name, description, topics, language, stars, readme_md
           FROM repos WHERE id = ?""",
        (repo_id,),
    ).fetchone()


def _clean(data: Any) -> dict[str, Any]:
    """规整模型输出：字段缺失/类型不对都不要让前端炸。"""
    if not isinstance(data, dict):
        data = {}
    stack = data.get("stack") or []
    highs = data.get("highlights") or []
    return {
        "one_liner": str(data.get("one_liner") or "").strip()[:120],
        "stack": [str(x).strip()[:24] for x in stack if str(x).strip()][:6]
        if isinstance(stack, list) else [],
        "highlights": [str(x).strip()[:80] for x in highs if str(x).strip()][:5]
        if isinstance(highs, list) else [],
        "audience": str(data.get("audience") or "").strip()[:60],
        "maturity": str(data.get("maturity") or "").strip()[:60],
    }


def _fetch_readme_from_github(full_name: str) -> str | None:
    """本地没有实质 README → 现场去 GitHub 抓（用户要求："没有 md 直接去 GitHub 现抓"）。

    走 /repos/{full}/readme（Accept: raw 直接返回正文）。
    令牌：gh CLI（限额 5000/h）；失败退化为匿名（60/h，单次按需抓取也够用）。
    返回正文或 None（404/网络失败）。
    """
    import subprocess as _sp

    global _GH_TOKEN_CACHE
    if _GH_TOKEN_CACHE is None:
        try:
            _GH_TOKEN_CACHE = _sp.run(["gh", "auth", "token"], capture_output=True,
                                      text=True, timeout=15).stdout.strip() or ""
        except Exception:
            _GH_TOKEN_CACHE = ""
    headers = {"Accept": "application/vnd.github.raw", "User-Agent": "recofeed/1.0"}
    if _GH_TOKEN_CACHE:
        headers["Authorization"] = f"Bearer {_GH_TOKEN_CACHE}"
    try:
        import requests as _requests
        # ⚠️ verify=False 是必须的：本机证书链对 api.github.com 验证必挂（见 crawl_service 注释）
        r = _requests.get(
            f"https://api.github.com/repos/{full_name}/readme",
            headers=headers, timeout=20, verify=False,
        )
        if r.status_code == 200 and r.text.strip():
            return r.text[:6000]
    except Exception:
        pass
    return None


_GH_TOKEN_CACHE: str | None = None


def make_brief(conn: sqlite3.Connection, repo_id: int,
               *, force: bool = False) -> dict[str, Any]:
    """生成（或读取缓存）某个仓库的 AI 速读。返回 {ok, brief, cached, model, reason}。"""
    ensure_tables(conn)
    r = _readme_of(conn, repo_id)
    if not r:
        return {"ok": False, "reason": "仓库不存在", "brief": None}

    readme = (r["readme_md"] or "").strip()
    readme_len = len(readme)

    # ⭐ 本地没有实质 README（兜底文本「# 名 + 一句话」）→ 现场去 GitHub 抓真的。
    #    用户要求："AI 速读如果程序没有发现 md，直接去 GitHub 现抓"。
    #    抓到后顺手落库（readme_md / 重提标签 / 脱敏），后续卡片与画像都受益。
    if readme_len < 200:
        fetched = _fetch_readme_from_github(r["full_name"])
        if not fetched or len(fetched.strip()) < 200:
            return {"ok": False, "reason": "GitHub 上也没有实质 README，无法速读",
                    "brief": None, "brief_short_readme": True}
        from quality.redact import redact_secrets
        from tags.extractor import extract_readme_tags
        fetched = redact_secrets(fetched)          # 第三方内容，先抹掉别人的密钥
        try:
            topics = json.loads(r["topics"] or "[]")
        except json.JSONDecodeError:
            topics = []
        tags = extract_readme_tags(fetched, topk=50, topics=topics, name=r["name"])
        conn.execute(
            """UPDATE repos SET readme_md=?, readme_len=?,
                   tags_json=?, tags_updated_at=datetime('now')
               WHERE id=?""",
            (fetched, len(fetched), json.dumps(tags, ensure_ascii=False), repo_id),
        )
        readme = fetched
        readme_len = len(readme)

    if not force:
        cached = get_cached(conn, repo_id, readme_len)
        if cached:
            return {"ok": True, "brief": cached, "cached": True}

    try:
        topics = json.loads(r["topics"] or "[]")
    except json.JSONDecodeError:
        topics = []

    user_prompt = (
        f"仓库：{r['full_name']}\n"
        f"语言：{r['language'] or '未知'} | 星数：{r['stars']}\n"
        f"GitHub topics：{', '.join(str(t) for t in topics[:10]) or '（无）'}\n"
        f"一句话描述：{r['description'] or '（无）'}\n\n"
        f"README 原文（截断）：\n{readme[:README_BUDGET]}"
    )

    data, model = _llm_json(conn, BRIEF_SYSTEM, user_prompt, temperature=0.3)
    if not data:
        return {"ok": False, "reason": "AI 暂时不可用（限流或额度用尽）", "brief": None}

    brief = _clean(data)
    if not brief["one_liner"]:
        return {"ok": False, "reason": "AI 返回内容无法解析", "brief": None}

    conn.execute(
        """INSERT INTO repo_briefs (repo_id, readme_len, brief_json, model, created_at)
           VALUES (?,?,?,?,datetime('now'))
           ON CONFLICT(repo_id) DO UPDATE SET
               readme_len = excluded.readme_len, brief_json = excluded.brief_json,
               model = excluded.model, created_at = datetime('now')""",
        (repo_id, readme_len, json.dumps(brief, ensure_ascii=False), model),
    )
    return {"ok": True, "brief": brief, "cached": False, "model": model}


def brief_cached_only(conn: sqlite3.Connection, repo_id: int) -> dict[str, Any] | None:
    """只读缓存（给 feed 用：绝不因为展示而触发 LLM 调用）。"""
    ensure_tables(conn)
    row = conn.execute(
        """SELECT b.brief_json FROM repo_briefs b JOIN repos r ON r.id = b.repo_id
           WHERE b.repo_id = ? AND b.readme_len = length(COALESCE(r.readme_md,''))""",
        (repo_id,),
    ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["brief_json"])
    except json.JSONDecodeError:
        return None
