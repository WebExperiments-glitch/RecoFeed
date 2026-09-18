"""AI 意图解析器：把一句大白话变成可执行的结构化检索意图。

为什么做（用户提的 P0）
    想在信息流里找到 TTS 项目，用户得自己知道该填 `tts`、`voice conversion`、`sovits` ——
    这是"懂技术标签"的门槛，普通用户跨不过去。
    改成：用户写一句自然语言（"我想找能在本地跑、支持声音克隆、最好带界面的 TTS 项目"），
    LLM 负责翻译成机器能用的结构：

        {"keywords": ["tts", "voice-cloning", "local-inference"],
         "tags":     ["webui", "onnx"],          ← 用于召回/排序加权
         "exclude":  ["api-wrapper"],            ← 用于过滤掉不要的形态
         "summary":  "本地优先、带界面的语音克隆 TTS"}

    产出直接喂给两处：
      ① 爬虫（keywords 写入 user_keywords → refill 计划按它去 GitHub 抓）
      ② 召回/排序（tags / exclude 参与候选打分与过滤）

设计取舍
    - keywords 必须是**英文技术词**（GitHub 搜索与 tags_json 都是英文），中文词会被丢弃；
    - 数量收敛（各 ≤6 个）：太散会让补货方向失去焦点；
    - exclude 也交给爬虫方向做"负向"表达（目前先落到画像上下文，供后续过滤使用）。
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from api.crawl_service import add_keyword, list_keywords
from api.insight_service import _llm_json

INTENT_SYSTEM = (
    "你是检索意图解析器。用户用一句自然语言描述他想找什么样的开源项目，"
    "你要把它翻译成**机器可用的结构化检索意图**。\n"
    "要求：\n"
    "1. keywords：3~6 个**英文技术词**（GitHub 上真实存在的词，如 tts / voice-cloning / "
    "local-inference / onnx），用于去 GitHub 搜索；不要中文、不要整句、不要空泛词（如 ai、tool）；\n"
    "2. tags：0~6 个描述「项目形态」的英文标签（如 webui / cli / onnx / docker / gradio），"
    "用于给候选加权；没有就给空数组；\n"
    "3. exclude：0~4 个用户明确不想要的形态（如 api-wrapper / cloud-only / mobile），没有就空数组；\n"
    "4. summary：一句话复述你理解到的需求（≤40 字，中文）；\n"
    "5. 如果用户描述太模糊（比如只有一个字），keywords 也要给出最接近的英文方向词，"
    "并在 summary 里说明「理解可能不准确」。\n"
    '只输出 JSON：{"keywords":[],"tags":[],"exclude":[],"summary":"…"}'
)

_KEY_OK = re.compile(r"^[a-z0-9][a-z0-9._\-+#]{1,29}$")     # 英文技术词形态
MAX_KEYWORDS = 12


def _clean_list(raw: Any, limit: int) -> list[str]:
    out: list[str] = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        t = str(x).strip().lower().replace(" ", "-")
        if not t or not _KEY_OK.match(t):
            continue
        if t not in out:
            out.append(t)
        if len(out) >= limit:
            break
    return out


def parse_intent(conn: sqlite3.Connection, user_id: int, text: str,
                 *, apply_keywords: bool = True) -> dict[str, Any]:
    """自然语言 → 结构化意图；默认同时把 keywords 写入 user_keywords（驱动爬虫）。"""
    text = (text or "").strip()
    if len(text) < 2:
        return {"ok": False, "reason": "描述太短，至少写几个字"}

    data, model = _llm_json(conn, INTENT_SYSTEM, f"用户描述：{text}", temperature=0.2)
    if not data:
        return {"ok": False, "reason": "AI 暂时不可用（限流或额度用尽）"}

    intent = {
        "keywords": _clean_list(data.get("keywords"), 6),
        "tags": _clean_list(data.get("tags"), 6),
        "exclude": _clean_list(data.get("exclude"), 4),
        "summary": str(data.get("summary") or "").strip()[:80],
    }
    if not intent["keywords"]:
        return {"ok": False, "reason": "AI 没能提取出可用的英文关键词，换个说法再试"}

    added: list[str] = []
    skipped: list[str] = []
    if apply_keywords:
        existing = {k["keyword"] for k in list_keywords(conn, user_id)}
        cur = len(existing)
        for kw in intent["keywords"]:
            if kw in existing:
                skipped.append(kw)
                continue
            if cur >= MAX_KEYWORDS:
                skipped.append(kw)
                continue
            try:
                add_keyword(conn, user_id, kw)
                existing.add(kw)
                cur += 1
                added.append(kw)
            except ValueError:
                skipped.append(kw)

    return {"ok": True, "intent": intent, "model": model,
            "added_keywords": added, "skipped_keywords": skipped}


def extract_tags_for_recall(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """把最近一次意图解析出的 tags/exclude 取出来，供召回加权/过滤使用。

    简化实现：直接读 user_intents 表最近一条（避免每次再调 LLM）。
    """
    ensure_tables(conn)
    row = conn.execute(
        """SELECT tags_json FROM user_intents WHERE user_id = ?
           ORDER BY id DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    if not row:
        return []
    try:
        return [str(t) for t in json.loads(row["tags_json"] or "[]")]
    except json.JSONDecodeError:
        return []


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_intents (
          id         INTEGER PRIMARY KEY AUTOINCREMENT,
          user_id    INTEGER NOT NULL,
          text       TEXT    NOT NULL,
          intent_json TEXT   NOT NULL,
          tags_json  TEXT,
          model      TEXT,
          created_at TEXT    NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_user_intents_user ON user_intents(user_id, id DESC);
        """
    )


def save_intent(conn: sqlite3.Connection, user_id: int, text: str,
                intent: dict[str, Any], model: str | None) -> None:
    ensure_tables(conn)
    conn.execute(
        """INSERT INTO user_intents (user_id, text, intent_json, tags_json, model)
           VALUES (?,?,?,?,?)""",
        (user_id, text[:200], json.dumps(intent, ensure_ascii=False),
         json.dumps(intent.get("tags") or [], ensure_ascii=False), model),
    )
