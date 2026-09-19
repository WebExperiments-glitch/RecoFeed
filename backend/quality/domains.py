"""兴趣方向聚合：把散标签聚成"方向"，让推荐不再被 `ai` 一个泛标签统治。

为什么需要（评测与用户共同的痛点）
    画像 Top1 长期是 `ai`（权重 1.00）—— 它匹配一切，于是：
      ① 补货查询永远是 "ai" → 抓回来的东西什么都有；
      ② 做语音的人被推营销号短视频工具；
      ③ 解释层只能说"偏 AI 方向"，没有信息量。

数据来源（单一事实来源）
    `dict/domain_terms.txt` —— 既有的人工维护词表（格式 `方向: 词1 词2 …`），
    覆盖 speech / llm / retrieval / diffusion / training / frontend / database /
    infra / agent / security 十个方向。本模块只是**读取与聚合**，不另立体系。

用法
    domain_scores(tag_weights)          # [{name, score, tags:[{tag,weight}]}]
    domain_queries(tag_weights, k=3)    # 按方向取补货查询词
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

DICT_PATH = Path(__file__).resolve().parent.parent / "dict" / "domain_terms.txt"

# 泛标签：横跨多个方向，正是要被拆掉的东西 —— 不参与方向聚合
UMBRELLA = {"ai", "llm", "llms", "openai", "gpt", "chatgpt", "machine-learning",
            "deep-learning", "artificial-intelligence", "genai", "generative-ai"}


@functools.lru_cache(maxsize=1)
def load_domains() -> list[tuple[str, list[str]]]:
    """读 dict/domain_terms.txt → [(方向名, [词])]（文件缺失时返回空，功能自动降级）。"""
    out: list[tuple[str, list[str]]] = []
    try:
        for raw in DICT_PATH.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            name, _, words = line.partition(":")
            name = name.strip()
            members = [w.strip().lower() for w in words.split() if w.strip()]
            if name and members:
                out.append((name, members))
    except OSError:
        pass
    return out


@functools.lru_cache(maxsize=1)
def tag_domain_map() -> dict[str, str]:
    """词 → 方向 的反查表。"""
    m: dict[str, str] = {}
    for name, words in load_domains():
        for w in words:
            m.setdefault(w, name)          # 先到先得
    return m


# 方向的中文显示名（前端直接用；没有映射的回退英文原名）
DOMAIN_ZH = {
    "speech": "语音合成 / TTS", "llm": "大模型 / 推理", "retrieval": "向量检索 / RAG",
    "diffusion": "图像 / 视频生成", "training": "训练 / 微调", "frontend": "前端 / Web UI",
    "database": "数据库 / 存储", "infra": "基础设施 / 部署", "agent": "Agent / 工具调用",
    "security": "安全 / 隐私",
}


def domain_zh(name: str) -> str:
    return DOMAIN_ZH.get(name, name)


def domain_of(tag: str) -> str | None:
    return tag_domain_map().get((tag or "").strip().lower())


def domain_scores(tag_weights: dict[str, float],
                  *, k: int = 5) -> list[dict[str, Any]]:
    """把标签权重聚合成方向分（方向分 = 成员标签权重之和），降序前 k 个。"""
    buckets: dict[str, dict[str, float]] = {}
    for tag, w in (tag_weights or {}).items():
        t = (tag or "").strip().lower()
        if t in UMBRELLA:
            continue
        dom = tag_domain_map().get(t)
        if not dom:
            continue
        b = buckets.setdefault(dom, {})
        b[t] = b.get(t, 0.0) + w

    out = []
    for dom, members in buckets.items():
        score = sum(members.values())
        tags = [{"tag": t, "weight": round(w, 3)}
                for t, w in sorted(members.items(), key=lambda kv: -kv[1])[:8]]
        out.append({"name": domain_zh(dom), "key": dom,
                    "score": round(score, 3), "tags": tags})
    out.sort(key=lambda x: -x["score"])
    return out[:k]


def domain_queries(tag_weights: dict[str, float], *, k: int = 3,
                   per_domain: int = 3) -> list[str]:
    """按方向生成补货查询词（每个方向取权重最高的几个成员词）。"""
    out: list[str] = []
    seen: set[str] = set()
    for dom in domain_scores(tag_weights, k=k):
        for t in dom["tags"]:
            if t["tag"] not in seen:
                out.append(t["tag"])
                seen.add(t["tag"])
    return out
