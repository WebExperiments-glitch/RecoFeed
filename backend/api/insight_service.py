"""LLM 洞察层：用大模型做小模型做不到的两件事。

⭐ 分工（这是本项目的核心判断）
    小模型（66K MLP）负责**排序**——每次请求给 300+ 候选打分，必须零成本、毫秒级；
    大模型负责**归纳与解释**——低频、可缓存、需要语言能力，小模型做不了：

     ① 画像归纳（summarize_profile）
        把 47 个证据仓库 + 50 个标签权重 + 语言偏好，归纳成
        「你是一个在做语音克隆与本地推理的开发者，最近在搞 Intel XPU 适配」
        —— 顺带剔除仍然残留的语义噪声（小模型无法判断"support 是不是兴趣"）。

     ② 推荐理由（explain_cards）
        「💡 因为你最近在写 indexTTS，这个项目提供一键训练的 Intel XPU 支持」

   成本控制：一页 10 张卡片**合并成 1 次调用**；结果按 (repo, user, 画像指纹) 缓存；
   走的是翻译同一条级联（免费档 3 模型 → DeepSeek 付费兜底，含限流与冷却）。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

from api.translate_service import (
    _chat,
    _check_rate_limit,
    _mark_model_429,
    _pick_model,
    _quota_reject_reason,
    _record_usage,
)

# 单次最多解释几张卡片（控制 prompt 长度与输出长度）
EXPLAIN_BATCH = 10
# 画像归纳的缓存有效期（小时）——画像指纹变了会自动失效
INSIGHT_TTL_HOURS = 24

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS llm_insights (
  user_id      INTEGER NOT NULL,
  kind         TEXT    NOT NULL,           -- profile_summary
  fingerprint  TEXT    NOT NULL,           -- 画像指纹：画像变了就失效
  content_json TEXT    NOT NULL,
  model        TEXT,
  created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, kind)
);

CREATE TABLE IF NOT EXISTS llm_explanations (
  user_id      INTEGER NOT NULL,
  repo_id      INTEGER NOT NULL,
  fingerprint  TEXT    NOT NULL,
  text         TEXT    NOT NULL,
  model        TEXT,
  created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, repo_id)
);
"""


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(_TABLE_SQL)


# ---------------------------------------------------------------- 通用调用

def _extract_json(text: str) -> Any | None:
    """从模型输出里抠出 JSON（免费模型常带 ```json 包裹或前后废话）。"""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^```(?:json)?|```$", "", t, flags=re.MULTILINE).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    # 退一步：抓第一个 { 到最后一个 }
    i, j = t.find("{"), t.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except json.JSONDecodeError:
            return None
    return None


def _llm_json(conn: sqlite3.Connection, system: str, user: str,
              *, temperature: float = 0.4) -> tuple[Any | None, str | None]:
    """走翻译同一条级联调用 LLM 并解析 JSON。

    返回 (数据, 模型名)；失败返回 (None, None)。
    """
    for _ in range(4):                      # 最多换 4 个模型
        reason = _quota_reject_reason(conn)
        if reason:
            return None, None
        try:
            _check_rate_limit(conn)
        except Exception:
            return None, None
        entry = _pick_model()
        if not entry:
            return None, None
        try:
            content = _chat(entry["provider"], entry["id"], system, user, temperature)
            _record_usage(conn, entry["id"])
            data = _extract_json(content)
            if data is not None:
                return data, entry["id"]
        except Exception as e:  # noqa: BLE001
            if "429" in str(e):
                _mark_model_429(entry["id"])
            continue
    return None, None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------- 画像指纹

def _top_tags(conn: sqlite3.Connection, user_id: int, limit: int = 30) -> list[dict[str, Any]]:
    """读画像标签。

    ⚠️ user_profiles 的真实结构是 top_topics（JSON 数组），不是 tag/weight 两列 ——
       一开始按两列写会 OperationalError: no such column: tag。
    """
    try:
        row = conn.execute(
            "SELECT top_topics FROM user_profiles WHERE user_id = ?", (user_id,)
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    if not row or not row["top_topics"]:
        return []
    try:
        arr = json.loads(row["top_topics"])
    except (json.JSONDecodeError, TypeError):
        return []
    out = [{"tag": str(x.get("tag", "")), "weight": round(float(x.get("weight", 0)), 3)}
           for x in arr if isinstance(x, dict) and x.get("tag")]
    return out[:limit]


def profile_fingerprint(conn: sqlite3.Connection, user_id: int) -> str:
    """画像指纹：标签权重排序 + 证据量 → 变了就说明画像更新了，缓存失效。"""
    ensure_tables(conn)
    rows = _top_tags(conn, user_id, 30)
    ev = conn.execute(
        "SELECT COUNT(*) AS n FROM github_footprint WHERE user_id = ?", (user_id,)
    ).fetchone()["n"]
    raw = "|".join(f"{r['tag']}:{r['weight']}" for r in rows) + f"#ev{ev}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _profile_context(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    """给 LLM 的画像上下文：标签 + 实证圈代表作 + 语言偏好。"""
    tags = _top_tags(conn, user_id, 25)
    owned, starred = [], []
    try:
        owned = [r["full_name"] for r in conn.execute(
            "SELECT full_name FROM github_footprint WHERE user_id = ? AND source = 'owned' "
            "ORDER BY weight DESC LIMIT 10", (user_id,))]
        starred = [r["full_name"] for r in conn.execute(
            "SELECT full_name FROM github_footprint WHERE user_id = ? AND source = 'starred' "
            "ORDER BY weight DESC LIMIT 12", (user_id,))]
    except sqlite3.OperationalError:
        pass
    langs = []
    try:
        langs = [r["lang"] for r in conn.execute(
            "SELECT language AS lang, COUNT(*) AS n FROM repos WHERE id IN ("
            "  SELECT repo_id FROM user_events WHERE user_id = ? AND event_type='deep_read'"
            ") GROUP BY language ORDER BY n DESC LIMIT 5", (user_id,))]
    except sqlite3.OperationalError:
        pass
    return {"tags": tags, "owned": owned, "starred": starred, "languages": langs}


# ---------------------------------------------------------------- ① 画像归纳

_SUMMARY_SYSTEM = (
    "你是推荐系统的用户画像分析器。你的输入是：用户在 GitHub 上的自建仓库、点星仓库，"
    "以及系统统计出的兴趣标签权重。\n"
    "你要做三件事，只输出 JSON，不要解释：\n"
    "1. summary：用一句中文概括这个用户的技术画像（≤60字，要具体到技术方向，"
    "不要写'热爱技术'这类空话）。\n"
    "2. interests：提炼 5-8 个**精确的技术方向词**（英文小写、可用连字符，如 "
    "voice-cloning / local-llm-inference / intel-xpu），按重要性排序。\n"
    "3. noise：从给定的标签里挑出**不构成技术兴趣**的噪声词（README 套话、"
    "通用词、平台名），最多 10 个。\n"
    '输出格式：{"summary": "...", "interests": ["..."], "noise": ["..."]}'
)


def summarize_profile(conn: sqlite3.Connection, user_id: int,
                      *, force: bool = False) -> dict[str, Any]:
    """LLM 画像归纳（带指纹缓存）。"""
    ensure_tables(conn)
    fp = profile_fingerprint(conn, user_id)
    if not force:
        row = conn.execute(
            "SELECT content_json, model, created_at FROM llm_insights "
            "WHERE user_id = ? AND kind = 'profile_summary' AND fingerprint = ?",
            (user_id, fp),
        ).fetchone()
        if row:
            out = json.loads(row["content_json"])
            out.update({"cached": True, "model": row["model"], "created_at": row["created_at"]})
            return out

    ctx = _profile_context(conn, user_id)
    if not ctx["tags"] and not ctx["owned"]:
        return {"ok": False, "reason": "画像为空：先刷几条或登录同步 GitHub"}

    user_prompt = (
        f"兴趣标签（tag: 权重）：\n{json.dumps(ctx['tags'], ensure_ascii=False)}\n\n"
        f"用户自建的仓库（最强证据）：\n{json.dumps(ctx['owned'], ensure_ascii=False)}\n\n"
        f"用户点星的仓库：\n{json.dumps(ctx['starred'], ensure_ascii=False)}\n\n"
        f"语言偏好：{json.dumps(ctx['languages'], ensure_ascii=False)}"
    )
    data, model = _llm_json(conn, _SUMMARY_SYSTEM, user_prompt, temperature=0.5)
    if not data:
        return {"ok": False, "reason": "LLM 暂时不可用（限流或额度用尽），稍后再试"}

    out = {
        "ok": True,
        "summary": str(data.get("summary", ""))[:120],
        "interests": [str(x)[:40] for x in (data.get("interests") or [])][:8],
        "noise": [str(x)[:30] for x in (data.get("noise") or [])][:10],
        "cached": False,
        "model": model,
        "fingerprint": fp,
        "created_at": _now(),
    }
    conn.execute(
        """INSERT INTO llm_insights (user_id, kind, fingerprint, content_json, model, created_at)
           VALUES (?, 'profile_summary', ?, ?, ?, datetime('now'))
           ON CONFLICT(user_id, kind) DO UPDATE SET
               fingerprint = excluded.fingerprint,
               content_json = excluded.content_json,
               model = excluded.model,
               created_at = excluded.created_at""",
        (user_id, fp, json.dumps({k: out[k] for k in
                                  ("summary", "interests", "noise")}, ensure_ascii=False), model),
    )
    return out


def get_cached_insight(conn: sqlite3.Connection, user_id: int) -> dict[str, Any] | None:
    ensure_tables(conn)
    row = conn.execute(
        "SELECT content_json, model, created_at, fingerprint FROM llm_insights "
        "WHERE user_id = ? AND kind = 'profile_summary'", (user_id,)
    ).fetchone()
    if not row:
        return None
    if row["fingerprint"] != profile_fingerprint(conn, user_id):
        return None          # 画像变了 → 旧归纳不再可信
    out = json.loads(row["content_json"])
    out.update({"ok": True, "cached": True, "model": row["model"],
                "created_at": row["created_at"]})
    return out


# ---------------------------------------------------------------- ② 推荐理由

_EXPLAIN_SYSTEM = (
    "你是推荐系统的解释器。用户会给你他的兴趣画像和一批候选仓库，"
    "你要为**每一个**仓库写一句中文推荐理由。\n"
    "要求：\n"
    "1. 必须结合用户的**具体**兴趣或他正在做的项目，说明这个仓库为什么适合他；\n"
    "2. 一句话，≤45 字，口语化，不要以'这个仓库'开头，不要复述仓库简介；\n"
    "3. 如果这个仓库与用户兴趣关系不大，就诚实地说出'偏 XX 方向，可能不合你的口味'；\n"
    "4. 只输出 JSON：{\"<repo_id>\": \"<理由>\"}，不要输出其它内容。"
)


def explain_cards(conn: sqlite3.Connection, user_id: int,
                  repo_ids: list[int]) -> dict[str, Any]:
    """为一批卡片生成一句话推荐理由（一次 LLM 调用 + 缓存）。"""
    ensure_tables(conn)
    repo_ids = [int(r) for r in repo_ids][:EXPLAIN_BATCH]
    if not repo_ids:
        return {"ok": True, "explanations": {}, "cached": 0, "generated": 0}

    fp = profile_fingerprint(conn, user_id)
    placeholders = ",".join("?" * len(repo_ids))

    # ① 命中缓存的直接用
    cached: dict[int, str] = {}
    for r in conn.execute(
        f"""SELECT repo_id, text FROM llm_explanations
            WHERE user_id = ? AND fingerprint = ? AND repo_id IN ({placeholders})""",
        (user_id, fp, *repo_ids),
    ):
        cached[int(r["repo_id"])] = r["text"]

    todo = [rid for rid in repo_ids if rid not in cached]
    if not todo:
        return {"ok": True, "explanations": {str(k): v for k, v in cached.items()},
                "cached": len(cached), "generated": 0}

    # ② 取候选信息
    ctx = _profile_context(conn, user_id)
    rows = conn.execute(
        f"""SELECT id, full_name, description, topics, stars, language
            FROM repos WHERE id IN ({','.join('?' * len(todo))})""",
        tuple(todo),
    ).fetchall()
    cards = []
    for r in rows:
        try:
            topics = json.loads(r["topics"] or "[]")
        except json.JSONDecodeError:
            topics = []
        cards.append({
            "id": int(r["id"]),
            "name": r["full_name"],
            "desc": (r["description"] or "")[:220],
            "topics": topics[:8],
            "stars": int(r["stars"] or 0),
        })

    user_prompt = (
        f"用户画像：{json.dumps(ctx['tags'][:12], ensure_ascii=False)}\n"
        f"用户在做的项目：{json.dumps(ctx['owned'][:6], ensure_ascii=False)}\n"
        f"用户点星过的项目：{json.dumps(ctx['starred'][:8], ensure_ascii=False)}\n\n"
        f"候选仓库（repo_id / 名称 / 简介 / topics / 星数）：\n"
        + "\n".join(
            f"{c['id']} | {c['name']} | {c['desc']} | {','.join(c['topics'])} | ★{c['stars']}"
            for c in cards
        )
    )
    data, model = _llm_json(conn, _EXPLAIN_SYSTEM, user_prompt, temperature=0.6)
    if not data:
        return {"ok": False, "reason": "LLM 暂时不可用（限流或额度用尽）",
                "explanations": {str(k): v for k, v in cached.items()},
                "cached": len(cached), "generated": 0}

    generated = 0
    for k, v in data.items():
        key = str(k).strip()
        if not key.isdigit():
            continue
        if int(key) not in todo:
            continue
        text = str(v).strip()[:80]
        if not text:
            continue
        conn.execute(
            """INSERT INTO llm_explanations (user_id, repo_id, fingerprint, text, model)
               VALUES (?,?,?,?,?)
               ON CONFLICT(user_id, repo_id) DO UPDATE SET
                   fingerprint = excluded.fingerprint, text = excluded.text,
                   model = excluded.model, created_at = datetime('now')""",
            (user_id, int(key), fp, text, model),
        )
        cached[int(key)] = text
        generated += 1

    return {"ok": True, "explanations": {str(k): v for k, v in cached.items()},
            "cached": len(cached) - generated, "generated": generated, "model": model}


def explain_prompt_demo(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    """返回将要发给 LLM 的 prompt（调试/展示用，不消耗额度）。"""
    ctx = _profile_context(conn, user_id)
    return {"system": _EXPLAIN_SYSTEM, "profile": ctx}
