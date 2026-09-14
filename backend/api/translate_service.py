"""LLM 中译服务：OpenRouter 免费模型链 + DeepSeek 付费兜底。

把仓库的 标题 / description / README 翻译成简体中文，解决"信息流全是英文看不懂"。

防护措施：
    1. 双窗口本地限流 —— 60 秒 / 24 小时滑动窗口（仅统计免费模型调用），
       用量落 SQLite，重启不丢当日额度
    2. 模型级联降级 —— 免费模型 429/5xx/超时 → 自动切下一个；
       DeepSeek（deepseek-flash，付费）是链尾兜底，只有免费模型全崩了才用
    3. 付费防护 —— deepseek-flash 独立日限额（DEEPSEEK_MAX_PER_DAY），
       且调用时 thinking.type=disabled 关闭思考模式（官方文档确认的开关），
       翻译任务不需要推理，关掉后更快更省
    4. 429 冷却 —— 收到 429 的模型独享冷却 120s，链上模型全部冷却时
       全局冷却 45s，期内请求直接拒绝，绝不打上游
    5. 翻译缓存 —— SQLite 持久化，重复查看零消耗
       （translations: 详情 desc+README 按内容 hash；translations_desc: 卡片标题+描述）
    6. 并发合并 —— 同仓库详情请求共享结果（防连点）；批量描述共享全局批量锁
    7. 输入截断 —— description ≤400 字符、README ≤2400 字符进 LLM
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from typing import Any

import httpx
from fastapi import HTTPException

from core.config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MAX_PER_DAY,
    LLM_CHAIN,
    LLM_DESC_MAX_CHARS,
    LLM_GLOBAL_COOLDOWN_SEC,
    LLM_MAX_PER_DAY,
    LLM_MAX_PER_MINUTE,
    LLM_MAX_TOKENS,
    LLM_MODEL_COOLDOWN_SEC,
    LLM_README_MAX_CHARS,
    LLM_TIMEOUT_SEC,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
)

log = logging.getLogger("recofeed.translate")

# ---------------------------------------------------------------- 表结构
# 运行时自建表（CREATE IF NOT EXISTS），不进 docs/schema.sql。
_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS translations (
    repo_id       INTEGER NOT NULL,
    content_hash  TEXT    NOT NULL,
    zh_description TEXT,
    zh_readme     TEXT,
    model         TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (repo_id, content_hash)
);
CREATE TABLE IF NOT EXISTS llm_usage (
    ts    REAL NOT NULL,
    model TEXT
);
CREATE INDEX IF NOT EXISTS idx_llm_usage_ts ON llm_usage(ts);
CREATE TABLE IF NOT EXISTS translations_desc (
    repo_id          INTEGER PRIMARY KEY,
    description_hash TEXT NOT NULL,
    zh_name          TEXT,
    zh_description   TEXT,
    model            TEXT,
    updated_at       TEXT DEFAULT (datetime('now'))
);
"""


def ensure_tables(conn) -> None:
    conn.executescript(_TABLES_SQL)
    # 旧版 translations_desc 没有 zh_name 列（当天演进），补齐
    try:
        conn.execute("ALTER TABLE translations_desc ADD COLUMN zh_name TEXT")
    except Exception:
        pass  # 列已存在


# ---------------------------------------------------------------- 并发合并
class _Inflight:
    """同一仓库的并发详情翻译请求共享这一个实例。"""

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: dict[str, Any] | None = None
        self.error: HTTPException | None = None


_inflight: dict[int, _Inflight] = {}
_inflight_lock = threading.Lock()

# 429 冷却：模型级 + 全局（进程内存态，重启自然清零）
_model_cooldown: dict[str, float] = {}
_global_cooldown_until = 0.0
_cooldown_lock = threading.Lock()

# 批量描述翻译互斥锁：预热任务与前端拉取并发时只跑一次 LLM
_batch_lock = threading.Lock()
_BATCH_LOCK_TIMEOUT = 30.0


# ---------------------------------------------------------------- 限流
def _is_paid(model_id: str) -> bool:
    return model_id.startswith("deepseek")


def _free_usage_counts(conn, now: float) -> tuple[int, int]:
    """免费模型的（分钟内, 24h 内）调用数。DeepSeek 付费调用不计入免费额度。"""
    rows = conn.execute(
        "SELECT ts, model FROM llm_usage WHERE ts > ?",
        (now - 90_000,),
    ).fetchall()
    n_min = n_day = 0
    for r in rows:
        m = r["model"] or ""
        if _is_paid(m):
            continue
        if r["ts"] > now - 60:
            n_min += 1
        n_day += 1
    conn.execute("DELETE FROM llm_usage WHERE ts < ?", (now - 90_000,))
    return n_min, n_day


def _deepseek_used_today(conn, now: float) -> int:
    """DeepSeek 付费调用今日用量（独立限额，防烧钱）。"""
    day_start = now - 86_400
    return conn.execute(
        "SELECT COUNT(*) AS n FROM llm_usage WHERE ts > ? AND model LIKE 'deepseek%'",
        (day_start,),
    ).fetchone()["n"]


def _quota_reject_reason(conn) -> str | None:
    """检查免费额度与全局冷却。可消费返回 None（并计入一次免费用量）。"""
    global _global_cooldown_until
    now = time.time()

    with _cooldown_lock:
        if now < _global_cooldown_until:
            return (
                f"翻译服务冷却中（上游限流），"
                f"约 {int(_global_cooldown_until - now)} 秒后自动恢复"
            )

    n_min, n_day = _free_usage_counts(conn, now)
    if n_min >= LLM_MAX_PER_MINUTE:
        return f"每分钟翻译次数已达上限（{LLM_MAX_PER_MINUTE} 次/分钟），稍后再试"
    if n_day >= LLM_MAX_PER_DAY:
        return f"今日免费翻译额度已用完（{LLM_MAX_PER_DAY} 次/天），明天自动恢复"

    conn.execute(
        "INSERT INTO llm_usage (ts, model) VALUES (?, 'free-slot')", (now,))
    return None


def _check_rate_limit(conn) -> None:
    """单仓库详情翻译的限流入口（超限抛 429）。"""
    reason = _quota_reject_reason(conn)
    if reason:
        raise HTTPException(429, reason)


def _mark_model_429(model_id: str) -> None:
    """某模型 429：独享冷却。链上模型全部冷却时加全局冷却。"""
    global _global_cooldown_until
    with _cooldown_lock:
        _model_cooldown[model_id] = time.time() + LLM_MODEL_COOLDOWN_SEC
        now = time.time()
        alive = [
            e["id"] for e in LLM_CHAIN
            if _model_cooldown.get(e["id"], 0) < now and _provider_ready(e)
        ]
        if not alive:
            _global_cooldown_until = now + LLM_GLOBAL_COOLDOWN_SEC
            log.warning("链上全部模型 429，全局冷却 %ss", LLM_GLOBAL_COOLDOWN_SEC)


def _provider_ready(entry: dict[str, str]) -> bool:
    from core.config import DEEPSEEK_API_KEY, OPENROUTER_API_KEY
    if entry["provider"] == "openrouter":
        return bool(OPENROUTER_API_KEY)
    if entry["provider"] == "deepseek":
        return bool(DEEPSEEK_API_KEY)
    return False


def _pick_model() -> dict[str, str] | None:
    """挑链上第一个不在冷却中的模型（DeepSeek 的日限额在调用前单独查库）。"""
    now = time.time()
    for e in LLM_CHAIN:
        if not _provider_ready(e):
            continue
        if _model_cooldown.get(e["id"], 0) < now:
            return e
    return None


def _deepseek_quota_ok(conn) -> bool:
    """付费兜底日限额检查（超限时把 deepseek 冷却到明天，天然退回免费链重试）。"""
    used = _deepseek_used_today(conn, time.time())
    if used >= DEEPSEEK_MAX_PER_DAY:
        with _cooldown_lock:
            _model_cooldown["deepseek-flash"] = time.time() + 86_400
        log.warning("DeepSeek 日限额已用完（%d/%d），今日不再使用付费兜底",
                    used, DEEPSEEK_MAX_PER_DAY)
        return False
    return True


def _record_usage(conn, model_id: str) -> None:
    """把真实使用的模型记入用量表（用于免费/付费额度分别统计）。"""
    # 记录时替换占位符：_quota_reject_reason 已插入过一条 'free-slot'
    conn.execute(
        "UPDATE llm_usage SET model = ? "
        "WHERE model = 'free-slot' AND ts = ("
        "  SELECT MAX(ts) FROM llm_usage WHERE model = 'free-slot')",
        (model_id,),
    )


# ---------------------------------------------------------------- 原文处理
def _english_ratio(text: str) -> float:
    """英文段落占比（按字母 vs CJK 字符计数）。<0.3 视为无需翻译。"""
    if not text:
        return 0.0
    letters = sum(1 for c in text if c.isascii() and c.isalpha())
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    total = letters + cjk
    if total < 20:
        return 0.0
    return letters / total


def _is_real_translation(zh: str | None, src: str | None) -> bool:
    """判断"译文"是否真的是中文译文。

    ⚠️ 实测踩坑：免费模型偶尔**原样回吐原文**（ipazc/mtcnn 的简介被"翻译"成
       同一句英文），而下游把它当译文渲染 → 卡片上同一句话出现两遍
       （一行"译文"、一行原文）。所以译文必须过这道门：
         ① 不能与原文相同（忽略大小写/空白）
         ② 必须真的有汉字（≥4 个且占比 ≥15%）
    """
    if not zh or not src:
        return False
    a = zh.strip()
    b = src.strip()
    if not a:
        return False
    if a.lower() == b.lower():
        return False
    if a.lower().replace(" ", "") == b.lower().replace(" ", ""):
        return False
    cjk = sum(1 for c in a if "\u4e00" <= c <= "\u9fff")
    return cjk >= max(4, int(len(a) * 0.15))


def _build_source(row) -> dict[str, str]:
    """取仓库原文并按上限截断。"""
    desc = (row["description"] or "").strip()
    readme = (row["readme_md"] or "").strip()
    # 粗去 markdown 噪声：图片、链接 URL、HTML 标签、连续空行
    readme = re.sub(r"<[^>]+>", " ", readme)
    readme = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", readme)
    readme = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", readme)
    readme = re.sub(r"https?://\S+", " ", readme)
    readme = re.sub(r"\n{3,}", "\n\n", readme)
    readme = re.sub(r"[ \t]{2,}", " ", readme)
    return {
        "desc": desc[:LLM_DESC_MAX_CHARS],
        "readme": readme[:LLM_README_MAX_CHARS],
        "readme_truncated": len(readme) > LLM_README_MAX_CHARS,
    }


def _content_hash(src: dict[str, str]) -> str:
    h = hashlib.sha256()
    h.update(src["desc"].encode("utf-8"))
    h.update(b"\x00")
    h.update(src["readme"].encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------- 通用调用
def _chat(provider: str, model_id: str, system: str, user: str,
          temperature: float) -> str:
    """按 provider 发起 chat completion，返回 content 文本。抛异常表示失败。"""
    if provider == "openrouter":
        base, key = OPENROUTER_BASE_URL, OPENROUTER_API_KEY
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "http://localhost:5173",
            "X-Title": "RecoFeed",
        }
        payload: dict[str, Any] = {
            "model": model_id,
            "temperature": temperature,
            "max_tokens": LLM_MAX_TOKENS,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    elif provider == "deepseek":
        base, key = DEEPSEEK_BASE_URL, DEEPSEEK_API_KEY
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_id,
            "temperature": temperature,
            "max_tokens": LLM_MAX_TOKENS,
            # 官方文档（thinking_mode 指南）：thinking.type=disabled 关闭思考模式。
            # 翻译不需要推理，关闭后更快（1.9s）更省（无 reasoning tokens）。
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
    else:
        raise RuntimeError(f"未知 provider: {provider}")

    with httpx.Client(timeout=LLM_TIMEOUT_SEC) as client:
        resp = client.post(f"{base}/chat/completions",
                           headers=headers, json=payload)

    if resp.status_code == 429:
        _mark_model_429(model_id)
        raise RuntimeError(f"{model_id}: 429 rate limited")
    if resp.status_code != 200:
        raise RuntimeError(f"{model_id}: HTTP {resp.status_code}")

    data = resp.json()
    content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
    return content.strip()


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ---------------------------------------------------------------- 详情翻译（desc + README）
_PROMPT_TEMPLATE = """You are a translator for a GitHub repository discovery app. Translate the English excerpts into natural, concise Simplified Chinese. Keep technical terms, library names and code identifiers in English. Output ONLY in the following format, keeping the marker lines exactly:

===DESC===
<translation of the description>
===README===
<translation of the readme excerpt>

Source description:
---
{desc}
---
Source readme excerpt:
---
{readme}
---"""


def _parse_llm_output(content: str) -> tuple[str | None, str]:
    """解析分隔符格式。解析失败时整个输出当 README 译文。"""
    text = _strip_think(content)
    if "===README===" in text:
        parts = text.split("===README===", 1)
        head = parts[0].replace("===DESC===", "").strip()
        readme = parts[1].strip()
        return (head or None), readme
    return None, text  # 回退：没有标记，当纯译文


def _translate_via_models(src: dict[str, str]) -> tuple[str | None, str, str]:
    """级联尝试链上模型，返回 (zh_desc, zh_readme, used_model_id)。"""
    errors: list[str] = []
    while True:
        entry = _pick_model()
        if entry is None:
            raise HTTPException(429, "上游模型暂时全部限流，请一两分钟后再试")
        if _is_paid(entry["id"]) and not _deepseek_quota_ok(
                _LAST_CONN_HOLDER.get("conn")):
            errors.append(f"{entry['id']}: 日限额")
            continue
        try:
            content = _chat(
                entry["provider"], entry["id"],
                "You are a professional EN→ZH translator for developer tools. "
                "Output only the translation in the requested marker format, "
                "no commentary.",
                _PROMPT_TEMPLATE.format(
                    desc=src["desc"] or "(none)",
                    readme=src["readme"] or "(none)",
                ),
                temperature=0.3,
            )
            zh_desc, zh_readme = _parse_llm_output(content)
            if not zh_readme or len(zh_readme) < 4:
                raise RuntimeError(f"{entry['id']}: empty translation")
            conn = _LAST_CONN_HOLDER.get("conn")
            if conn is not None:
                _record_usage(conn, entry["id"])
            return zh_desc, zh_readme, entry["id"]
        except HTTPException:
            raise  # 本地限流异常，直接透传
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            log.warning("翻译模型失败，切换下一个：%s", exc)
            if len(errors) >= len(LLM_CHAIN):
                raise HTTPException(
                    502, f"翻译失败（已尝试全部 {len(LLM_CHAIN)} 个模型）") from exc


# 级联调用里要把 conn 传给 DeepSeek 日限额检查 / 用量记录，
# 用 holder 避免改一长串函数签名（FastAPI 线程池逐请求使用，写入点都在请求生命周期内）。
_LAST_CONN_HOLDER: dict[str, Any] = {}


# ---------------------------------------------------------------- 主入口（详情）
def translate_repo(conn, repo_id: int) -> dict[str, Any]:
    """翻译仓库 description + README（带缓存 / 限流 / 并发合并）。

    返回:
        cached=True            → 缓存命中，零 API 消耗
        skipped=True           → 内容本身是中文/无内容，无需翻译
        zh_description/zh_readme → 中文译文（可能为 None）
    """
    ensure_tables(conn)
    _LAST_CONN_HOLDER["conn"] = conn

    row = conn.execute(
        "SELECT id, full_name, description, readme_md FROM repos WHERE id = ?",
        (repo_id,),
    ).fetchone()
    if not row:
        raise HTTPException(404, "仓库不存在")

    src = _build_source(row)
    if not src["desc"] and not src["readme"]:
        return {"repo_id": repo_id, "cached": False, "skipped": True,
                "reason": "no_content", "zh_description": None,
                "zh_readme": None, "model": None}

    ratio = max(_english_ratio(src["desc"]), _english_ratio(src["readme"]))
    if ratio < 0.3:
        return {"repo_id": repo_id, "cached": False, "skipped": True,
                "reason": "already_chinese", "zh_description": None,
                "zh_readme": None, "model": None}

    # ---- 描述缓存复用：Feed 批量翻译过的 desc 直接拿来用，LLM 只翻 README
    pre_desc = _cached_desc(conn, repo_id, src["desc"]) if src["desc"] else None
    llm_src = src
    chash = _content_hash(src)
    if pre_desc:
        llm_src = {**src, "desc": ""}
        chash = _content_hash(llm_src)

    # ---- 缓存命中（不打 API）
    hit = conn.execute(
        "SELECT zh_description, zh_readme, model FROM translations "
        "WHERE repo_id = ? AND content_hash = ?",
        (repo_id, chash),
    ).fetchone()
    if hit:
        return {"repo_id": repo_id, "cached": True, "skipped": False,
                "zh_description": hit["zh_description"],
                "zh_readme": hit["zh_readme"],
                "model": hit["model"],
                "readme_truncated": src["readme_truncated"]}

    # ---- 并发合并：同仓库进行中的请求直接等结果
    with _inflight_lock:
        job = _inflight.get(repo_id)
        owner = job is None
        if owner:
            job = _Inflight()
            _inflight[repo_id] = job

    if not owner:
        job.event.wait(timeout=90)
        if job.error:
            raise job.error
        if job.result is not None:
            return {**job.result, "shared": True}
        raise HTTPException(502, "翻译请求等待超时")

    # ---- 本线程是发起者：限流 → 调 LLM → 落缓存
    try:
        _check_rate_limit(conn)
        zh_desc, zh_readme, used_model = _translate_via_models(llm_src)
        # ⚠️ 这里以前是「翻译失败就退回原文（pre_desc）」—— 那会让前端把同一句话
        #    渲染两遍（一行当译文、一行当原文）。现在：校验不过就留空，
        #    前端只显示一次原文 + "翻译生成中"提示。
        if not _is_real_translation(zh_desc, src.get("desc")):
            zh_desc = None
        if zh_readme and not _is_real_translation(zh_readme, src.get("readme")):
            zh_readme = None

        conn.execute(
            """INSERT INTO translations
               (repo_id, content_hash, zh_description, zh_readme, model)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(repo_id, content_hash) DO UPDATE SET
                   zh_description = excluded.zh_description,
                   zh_readme = excluded.zh_readme,
                   model = excluded.model""",
            (repo_id, chash, zh_desc, zh_readme, used_model),
        )
        job.result = {
            "repo_id": repo_id, "cached": False, "skipped": False,
            "zh_description": zh_desc, "zh_readme": zh_readme,
            "model": used_model,
            "readme_truncated": src["readme_truncated"],
        }
        return job.result
    except HTTPException as exc:
        job.error = exc
        raise
    finally:
        job.event.set()
        with _inflight_lock:
            _inflight.pop(repo_id, None)
        _LAST_CONN_HOLDER["conn"] = None


def _cached_desc(conn, repo_id: int, desc: str) -> str | None:
    """读取单条描述缓存（hash 不匹配视为未命中）。"""
    row = conn.execute(
        "SELECT description_hash, zh_description FROM translations_desc "
        "WHERE repo_id = ?",
        (repo_id,),
    ).fetchone()
    if row and row["description_hash"] == _desc_hash(desc):
        return row["zh_description"]
    return None


def _desc_hash(desc: str) -> str:
    return hashlib.sha256(desc.encode("utf-8")).hexdigest()[:24]


# ==================================================================
# Feed 卡片 标题 + 描述 批量翻译
#
# 用户诉求：刷到卡片那一刻，标题和简介就已经是中文，一眼看懂讲什么。
# 设计：每页 feed（≤10 条）合并成 1 次 LLM 调用，返回 中文短名 + 中文描述；
#       一天 45 次免费额度 ≈ 45 页 ≈ 450 张卡片。双路径配合：
#         ① 预热 —— /api/feed 返回时 BackgroundTasks 后台翻好整页
#         ② 拉取 —— 前端调 /api/translate/batch，命中缓存秒回
# ==================================================================

_DESC_BATCH_PROMPT = """For each numbered GitHub repository, translate its name and description into Simplified Chinese.

- name: give a short, natural Chinese project title (≤ 12 chars). Keep product names and technical terms in English where appropriate (e.g. "llama.cpp" stays "llama.cpp").
- description: translate into concise, natural Simplified Chinese, keeping tech terms in English.
- If something is already Chinese, copy it as-is.

Input format:  N. name | description
Output EXACTLY one line per input item, format:  N. 中文名 | 描述译文
No extra commentary.

Input:
{items}

Output:"""


def _parse_desc_batch(content: str, n: int) -> dict[int, dict[str, str]]:
    """解析 "1. 中文名 | 描述译文" 逐行输出。"""
    text = _strip_think(content)
    out: dict[int, dict[str, str]] = {}
    for line in text.splitlines():
        m = re.match(r"^\s*(\d+)\s*[\.、)]\s*(.+)$", line.strip())
        if not m:
            continue
        idx = int(m.group(1))
        val = m.group(2).strip()
        if not (1 <= idx <= n) or not val or val in ("(none)", "-"):
            continue
        if " | " in val:
            name, _, desc = val.partition(" | ")
        elif "｜" in val:
            name, _, desc = val.partition("｜")
        else:
            name, desc = "", val  # 模型漏了标题：描述部分仍然可用
        # 清洗：部分模型会给译文包引号 / 冒号前后缀
        desc = desc.strip().strip('"“”「」『』').strip()
        name = name.strip().strip('"“”「」『』').strip()
        out[idx] = {"zh_name": name, "zh_description": desc}
    return out


def _call_llm_desc_batch(entry: dict[str, str], pairs) -> dict[int, dict[str, str]]:
    """单模型批量调用。抛异常表示该模型本次失败。"""
    items = "\n".join(
        f"{i}. {name} | {desc}" for i, (rid, name, desc) in enumerate(pairs, 1))
    content = _chat(
        entry["provider"], entry["id"],
        "You are a professional EN→ZH translator for developer tools. "
        "Output only the numbered translation lines.",
        _DESC_BATCH_PROMPT.format(items=items),
        temperature=0.2,
    )
    parsed = _parse_desc_batch(content, len(pairs))
    if len(parsed) < max(1, len(pairs) // 2):
        raise RuntimeError(f"{entry['id']}: 批量解析覆盖率低 {len(parsed)}/{len(pairs)}")
    return parsed


def _translate_desc_via_models(pairs) -> tuple[dict[int, dict[str, str]], str]:
    """批量描述级联翻译。返回 (译文 map, 使用的模型 id)。"""
    errors: list[str] = []
    while True:
        entry = _pick_model()
        if entry is None:
            raise HTTPException(429, "上游模型暂时全部限流，请一两分钟后再试")
        if _is_paid(entry["id"]) and not _deepseek_quota_ok(_LAST_CONN_HOLDER.get("conn")):
            errors.append(f"{entry['id']}: 日限额")
            continue
        try:
            return _call_llm_desc_batch(entry, pairs), entry["id"]
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
            log.warning("批量翻译模型失败，切换下一个：%s", exc)
            if len(errors) >= len(LLM_CHAIN):
                raise HTTPException(
                    502, f"翻译失败（已尝试全部 {len(LLM_CHAIN)} 个模型）") from exc


def translate_descriptions(
    conn,
    repo_ids: list[int],
) -> dict[int, dict[str, str | None]]:
    """批量翻译仓库 标题+描述（供 feed 卡片直接显示中文）。

    - 缓存命中的条目零消耗
    - 缺失条目合并成 1 次 LLM 调用（整批只计 1 次额度）
    - 限流/冷却时不抛异常，缺失条目返回 None（前端显示英文原文）
    - 全局批量锁：预热任务与前端拉取并发时只跑一次 LLM
    """
    ensure_tables(conn)
    _LAST_CONN_HOLDER["conn"] = conn
    try:
        return _translate_descriptions_inner(conn, repo_ids)
    finally:
        _LAST_CONN_HOLDER["conn"] = None


def _translate_descriptions_inner(
    conn,
    repo_ids: list[int],
) -> dict[int, dict[str, str | None]]:
    repo_ids = list(dict.fromkeys(repo_ids))[:50]  # 去重 + 上限
    if not repo_ids:
        return {}

    result: dict[int, dict[str, str | None]] = {}
    missing: list[tuple[int, str, str]] = []  # (repo_id, name, desc)

    rows = conn.execute(
        f"SELECT id, name, description FROM repos WHERE id IN "
        f"({','.join('?' for _ in repo_ids)})",
        repo_ids,
    ).fetchall()
    by_id = {r["id"]: r for r in rows}

    for rid in repo_ids:
        r = by_id.get(rid)
        desc = (r["description"] or "").strip() if r else ""
        name = (r["name"] or "").strip() if r else ""
        if not desc:
            result[rid] = {"zh_name": None, "zh_description": None}
            continue
        if not (_provider_ready({"provider": "openrouter"})
                or _provider_ready({"provider": "deepseek"})):
            result[rid] = {"zh_name": None, "zh_description": None}
            continue
        if _english_ratio(desc[:LLM_DESC_MAX_CHARS]) < 0.3:
            result[rid] = {"zh_name": None, "zh_description": None}  # 已是中文
            continue
        desc_c = desc[:LLM_DESC_MAX_CHARS]
        row = conn.execute(
            "SELECT description_hash, zh_name, zh_description "
            "FROM translations_desc WHERE repo_id = ?",
            (rid,),
        ).fetchone()
        if row and row["description_hash"] == _desc_hash(desc_c) \
                and row["zh_description"]:
            result[rid] = {"zh_name": row["zh_name"],
                           "zh_description": row["zh_description"]}
        else:
            missing.append((rid, name, desc_c))

    if not missing:
        return result

    # ---- 并发合并：拿不到批量锁的等一会儿再查缓存（预热方大概率已写好）
    got_lock = _batch_lock.acquire(timeout=_BATCH_LOCK_TIMEOUT)
    if not got_lock:
        for rid, _name, desc in missing:
            row = conn.execute(
                "SELECT description_hash, zh_name, zh_description "
                "FROM translations_desc WHERE repo_id = ?", (rid,)).fetchone()
            if row and row["description_hash"] == _desc_hash(desc) \
                    and row["zh_description"]:
                result[rid] = {"zh_name": row["zh_name"],
                               "zh_description": row["zh_description"]}
            else:
                result[rid] = {"zh_name": None, "zh_description": None}
        return result

    try:
        # 拿到锁后重查缓存（等锁期间别人可能已完成同一批）
        still_missing: list[tuple[int, str, str]] = []
        for rid, name, desc in missing:
            row = conn.execute(
                "SELECT description_hash, zh_name, zh_description "
                "FROM translations_desc WHERE repo_id = ?", (rid,)).fetchone()
            if row and row["description_hash"] == _desc_hash(desc) \
                    and row["zh_description"]:
                result[rid] = {"zh_name": row["zh_name"],
                               "zh_description": row["zh_description"]}
            else:
                still_missing.append((rid, name, desc))
        if not still_missing:
            return result

        # 额度检查：整批 1 次免费调用
        reason = _quota_reject_reason(conn)
        if reason:
            log.info("批量翻译被限流跳过：%s", reason)
            for rid, _n, _d in still_missing:
                result[rid] = {"zh_name": None, "zh_description": None}
            return result

        zh_map, used_model = _translate_desc_via_models(still_missing)
        for idx, (rid, _name, desc) in enumerate(still_missing, start=1):
            item = zh_map.get(idx) or {}
            zh_name = (item.get("zh_name") or "").strip() or None
            zh_desc = (item.get("zh_description") or "").strip() or None
            # ⭐ 质量门禁 ①：译文明显比原文长很多（≥2.5 倍）说明模型把解释
            #    文本混进了译文，不可信 → 不写缓存、返回 None（下次重试）。
            if zh_desc and len(zh_desc) > max(120, len(desc) * 2.5):
                log.warning("描述译文可疑（%d 字 vs 原文 %d 字），丢弃重试",
                            len(zh_desc), len(desc))
                zh_desc = None
            # ⭐ 质量门禁 ②：原样回吐原文 / 没有汉字 → 不算译文（否则界面重复渲染）
            if zh_desc and not _is_real_translation(zh_desc, desc):
                log.info("描述译文与原文相同或非中文，丢弃重试：%s", _name)
                zh_desc = None
            if zh_name and not _is_real_translation(zh_name, _name):
                zh_name = None
            result[rid] = {"zh_name": zh_name, "zh_description": zh_desc}
            if zh_desc or zh_name:
                conn.execute(
                    """INSERT INTO translations_desc
                       (repo_id, description_hash, zh_name, zh_description, model)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(repo_id) DO UPDATE SET
                           description_hash = excluded.description_hash,
                           zh_name = excluded.zh_name,
                           zh_description = excluded.zh_description,
                           model = excluded.model,
                           updated_at = datetime('now')""",
                    (rid, _desc_hash(desc), zh_name, zh_desc, used_model),
                )
        # 真实模型记账
        _record_usage(conn, used_model)
        return result
    finally:
        _batch_lock.release()


def preheat_descriptions(repo_ids: list[int]) -> None:
    """feed 返回后的后台预热（BackgroundTasks 调用，静默失败）。"""
    if not repo_ids:
        return
    try:
        from db.connection import get_conn
        with get_conn() as conn:
            translate_descriptions(conn, repo_ids)
    except Exception as exc:  # noqa: BLE001
        log.warning("描述预热失败（不影响 feed）：%s", exc)


def rate_status() -> dict[str, Any]:
    """当前限流状态（前端可展示"今日还能翻译 N 次"）。"""
    now = time.time()
    from db.connection import get_conn
    with get_conn() as conn:
        ensure_tables(conn)
        n_minute, n_day = _free_usage_counts(conn, now)
        ds_used = _deepseek_used_today(conn, now)
        n_cache = conn.execute(
            "SELECT COUNT(*) AS n FROM translations").fetchone()["n"]
        n_desc_cache = conn.execute(
            "SELECT COUNT(*) AS n FROM translations_desc").fetchone()["n"]

    with _cooldown_lock:
        chain = [
            {"model": e["id"], "provider": e["provider"],
             "paid": _is_paid(e["id"]),
             "configured": _provider_ready(e),
             "cooldown_left": max(0, int(_model_cooldown.get(e["id"], 0) - now))}
            for e in LLM_CHAIN
        ]
        global_left = max(0, int(_global_cooldown_until - now))

    return {
        "chain": chain,
        "models": [e["id"] for e in LLM_CHAIN],  # 兼容旧字段
        "used_last_minute": n_minute,
        "limit_per_minute": LLM_MAX_PER_MINUTE,
        "used_today": n_day,
        "limit_per_day": LLM_MAX_PER_DAY,
        "remaining_today": max(0, LLM_MAX_PER_DAY - n_day),
        "deepseek_used_today": ds_used,
        "deepseek_limit_per_day": DEEPSEEK_MAX_PER_DAY,
        "cache_entries": n_cache,
        "desc_cache_entries": n_desc_cache,
        "global_cooldown_left": global_left,
    }
