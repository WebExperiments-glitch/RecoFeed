"""密钥脱敏：抓进来的 README 里可能藏着别人的真密钥，落库前先抹掉。

为什么必须做（2026-09-18 被 GitHub Push Protection 拦下推送）
    补齐 README 后，`backend/data/recofeed.seed.db` 里出现了疑似 OpenAI Key：
        litanlitudan/skyagi → sk-VXl2bPhNEeTaGBa…
    那是**别人 README 里泄露的真密钥**。我们把第三方 README 收进数据集，
    等于帮它二次传播 —— 既害了原作者，也让本项目被 secret scanning 拦下。
    所以在**入库时就脱敏**：数据集干净、界面也不会把别人的密钥显示出来。

设计取舍
    - 只匹配"长得像密钥"的高置信形态，不做激进匹配：
      例如 UniTask 的 `sk-v2-zero-allocat`、markdown 锚点 `sk-on-windows`
      都是含连字符的正常文字，**不能**误伤（否则 README 会被改得看不懂）。
    - 命中即替换为 `[REDACTED]`，保留周边上下文，便于阅读。
"""
from __future__ import annotations

import re

_PATTERNS: list[re.Pattern[str]] = [
    # OpenAI / DeepSeek 风格：sk- 后跟 20+ 位纯字母数字（不含连字符 —— 避免误伤普通词）
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    # OpenRouter
    re.compile(r"\bsk-or-v1-[A-Za-z0-9]{32,}\b"),
    # Anthropic
    re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}\b"),
    # GitHub
    re.compile(r"\bghp_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(r"\bgho_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bghs_[A-Za-z0-9]{30,}\b"),
    # 云厂商
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}\b"),
    # Slack
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    # 私钥
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----[\s\S]{0,4000}?"
               r"-----END (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
]

REDACTED = "[REDACTED]"


def redact_secrets(text: str | None) -> str:
    """把文本里长得像密钥的片段替换成 [REDACTED]。None → 空串。"""
    if not text:
        return ""
    out = text
    for pat in _PATTERNS:
        out = pat.sub(REDACTED, out)
    return out


def has_secret(text: str | None) -> bool:
    """是否含有疑似密钥（可用于入库审计/告警）。"""
    if not text:
        return False
    return any(pat.search(text) for pat in _PATTERNS)
