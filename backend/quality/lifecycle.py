"""仓库生命周期判定：识别「已停更/不再维护」的项目，避免把它们推荐给用户。

为什么需要
    用户实测反馈：Feed 里出现了 README 明确写着"本项目停更"的仓库。
    推荐"遗珠"的前提是**它还有生命**；给用户推一个官方声明不再维护的项目，
    既浪费曝光，也损害推荐系统的可信度。

判定来源
    只依赖仓库自身的文本：README 正文 + 描述。不额外发请求（爬虫抓取时已经拿到）。

落库
    `repos.is_dead = 1`（这一列此前从未被写入，所有召回/补货/搜索路径都已在过滤它，
    所以标记即生效）+ `repos.lifecycle_note` 记录命中的原句，便于追溯与人工复核。

误判取舍
    "deprecated / archived" 这类词太常见（可能只是某个子模块被弃用），
    因此把它们归为**弱信号**：只有当它们出现在 README 开头（项目自述区）才算数。
    中文强信号（"本项目停更""停止维护"）则全文命中即判定 —— 这些词几乎不会用于子模块。
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 清洗

# 先剥掉 URL 与 markdown 链接：实测 `awesome-selfhosted` 的 README 里有
# `.../workflows/check-unmaintained-projects.yml/badge.svg` 这种 CI 徽章链接，
# 直接匹配 "unmaintained" 会误杀整个仓库。
_LINKISH = re.compile(
    r"https?://\S+"
    r"|!\[[^\]]*\]\([^)]*\)"
    r"|\[[^\]]*\]\([^)]*\)"
    r"|```.*?```",
    re.DOTALL,
)


def _strip_noise(text: str) -> str:
    return _LINKISH.sub(" ", text)


# ---------------------------------------------------------------- 强信号
# 中文措辞几乎只用于项目整体，全文命中即判定
STRONG_ZH: list[tuple[str, re.Pattern[str]]] = [
    ("本项目停更", re.compile(r"本\s*项\s*目\s*停\s*更")),
    ("停止维护", re.compile(r"(?:已)?\s*停\s*止\s*维\s*护")),
    ("不再维护", re.compile(r"不\s*再\s*维\s*护")),
    ("停止更新", re.compile(r"(?:已)?\s*停\s*止\s*更\s*新")),
    ("不再更新", re.compile(r"不\s*再\s*更\s*新")),
    ("停止开发", re.compile(r"(?:已)?\s*停\s*止\s*开\s*发")),
    ("项目已归档", re.compile(r"项目\s*已\s*归\s*档")),
    ("本仓库已废弃", re.compile(r"本\s*仓\s*库\s*已\s*废\s*弃")),
    ("不再开发", re.compile(r"不\s*再\s*开\s*发")),
    ("弃坑", re.compile(r"弃\s*坑")),
    ("停更", re.compile(r"停\s*更")),
]

# 英文必须**带项目级主语或独立标签**才判定 —— 否则会误杀"列表里描述其他项目"的 README。
# 实测误判样本：
#   avelino/awesome-go  → "…project here that is no longer maintained or is not a good fit"
#   vuejs/vue           → "…requirements about unmaintained software, check out Vue 2"
STRONG_EN: list[tuple[str, re.Pattern[str]]] = [
    # 带主语的明确声明：this repository / the project is no longer maintained
    ("this repo is no longer maintained",
     re.compile(r"\b(?:this|the)\s+(?:repository|repo|project|library|tool|package|"
                r"framework|plugin)\b[^.\n]{0,60}?\b(?:no\s+longer|not)\s+"
                r"(?:being\s+)?(?:actively\s+)?maintained", re.I)),
    # 独立标注：必须是**括号内**或**整行**的标签，不能是裸词 ——
    # 否则 "unmaintained software"、"no longer maintained or …" 这类普通句子会被误杀
    ("括号标注：不再维护",
     re.compile(r"\(\s*(?:no\s+longer\s+|not\s+)?(?:un)?maintained\s*\)", re.I)),
    ("整行标注：UNMAINTAINED",
     re.compile(r"^[\s#*|>_-]{0,6}(?:UN)?MAINTAINED[\s.!:,]*$", re.M | re.I)),
    ("整行标注：DISCONTINUED",
     re.compile(r"^[\s#*|>_-]{0,6}(?:DISCONTINUED|DEPRECATED)[\s.!:,]*$", re.M | re.I)),
    # 无主语的固定句（这些措辞本身就是项目级的）
    ("development has stopped",
     re.compile(r"development\s+has\s+(?:been\s+)?(?:stopped|ceased|halted)", re.I)),
    ("no longer under development",
     re.compile(r"no\s+longer\s+under\s+development", re.I)),
    ("no longer under maintenance",
     re.compile(r"no\s+longer\s+under\s+maintenance", re.I)),
]

# 弱信号：只有出现在 README 开头（项目自述区）才算项目级停更。
# ⚠️ 刻意**不收** archived / deprecated / inactive：
#    这些词在 README 里太常见且多为局部语义（"Deprecated: 某 API 请改用 v2"），
#    实测会产生误杀。项目级"归档"另有 GitHub 自己的 is_archived 字段兜底。
WEAK_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("不再支持", re.compile(r"no\s+longer\s+supported", re.I)),
    ("not actively maintained", re.compile(r"not\s+actively\s+maintained", re.I)),
]

_HEAD_CHARS = 800          # 弱信号只在开头这么多字符内生效
_SCAN_CHARS = 6000         # 扫描上限（README 最长实测 5.8KB，够用）


def detect_stopped(*texts: str | None) -> str | None:
    """检测文本是否声明"已停更/不再维护"，命中则返回命中的原句（用于落库追溯）。

    返回 None 表示未命中。
    """
    joined = "\n".join(t for t in texts if t)
    if not joined:
        return None
    # 先剥掉链接/代码块 —— 徽章 URL 里的 unmaintained 字样不能算
    clean = _strip_noise(joined)
    body = clean[:_SCAN_CHARS]
    head = clean[:_HEAD_CHARS]

    for label, pat in STRONG_ZH + STRONG_EN:
        m = pat.search(body)
        if m:
            return _snippet(body, m.start()) or label
    for label, pat in WEAK_PATTERNS:
        m = pat.search(head)
        if m:
            return _snippet(head, m.start()) or label
    return None


def _snippet(text: str, pos: int, width: int = 60) -> str:
    """取命中位置周围的一小段（去掉换行，便于存库/展示）。"""
    start = max(0, pos - 20)
    frag = text[start:start + width]
    return " ".join(frag.split())[:60]


def is_stopped(*texts: str | None) -> bool:
    return detect_stopped(*texts) is not None
