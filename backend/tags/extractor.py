"""TF-IDF 标签提取（三层权重体系）。

核心职责：
1. 从 README 提取关键词（jieba TF-IDF）
2. 合并用户足迹标签、Issue 热词标签
3. 归一化，保证不同长度的 README 可比

⚠️ 关键修正（实测发现）：
- jieba 默认会把 llama.cpp 切成 llama + cpp → 加载自定义词典解决
- jieba 默认停用词表极短 → 加载自定义停用词表
- 不归一化会被 README 长度带偏 → 归一化到 0~1
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

import jieba
import jieba.analyse

from core.config import DICT_DIR, TAG_LAYER_WEIGHTS

_initialized = False
_STOPWORDS_CACHE: set[str] | None = None
_DICT_TERMS_CACHE: set[str] | None = None


def _load_dict_file(path: Path) -> str:
    """读取词典文件，剔除注释行与空行。"""
    if not path.exists():
        return ""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        lines.append(stripped)
    return "\n".join(lines)


def init_jieba() -> None:
    """初始化 jieba：加载技术词典与停用词表。幂等。"""
    global _initialized
    if _initialized:
        return

    # ① 加载技术术语词典（解决 llama.cpp 被切碎）
    terms = _load_dict_file(DICT_DIR / "tech_terms.txt")
    for line in terms.splitlines():
        parts = line.split()
        if not parts:
            continue
        word = parts[0]
        freq = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10000
        jieba.add_word(word, freq=freq)

    # ② 加载停用词表（jieba 默认表只有 ~30 个英文词）
    stops = _load_dict_file(DICT_DIR / "stop_words.txt")
    stop_path = DICT_DIR / ".stop_words.resolved.txt"
    stop_path.write_text(stops, encoding="utf-8")
    jieba.analyse.set_stop_words(str(stop_path))

    _initialized = True


# ------------------------------------------------------------------ 清洗
_MD_NOISE = re.compile(
    r"```.*?```"          # 代码块
    r"|<[^>]+>"           # HTML 标签
    r"|!\[[^\]]*\]\([^)]*\)"   # 图片
    r"|\[([^\]]*)\]\([^)]*\)"  # 链接（保留文字）
    r"|^\s*[#>|\-=*+]{1,}\s*$"  # 表格线/分割线
    r"|\|",               # 表格竖线
    re.S | re.M,
)

_BADGE_LINE = re.compile(r"^\s*\[!\[.*$", re.M)

# ⚠️ LICENSE 样板段落必须整段剥离。
#    实测：README 里的 MIT/Apache 许可证文本会让 licensed / source /
#    community / permission / warranty 这些词拿到极高 TF-IDF 权重，
#    直接污染用户画像 Top10 —— 这些词不表达任何技术领域。
#    单纯加停用词不够（"model" 在 AI 项目里是真有意义的词），
#    所以选择整段切除。
_LICENSE_SECTION = re.compile(
    r"^#{1,4}[ \t]*(license|licence|licensing|许可|授权|版权)\b.*?(?=^#{1,4}[ \t]|\Z)",
    re.I | re.M | re.S,
)
# 无标题但成段的许可证样板句
_LICENSE_BOILERPLATE = re.compile(
    r"(permission is hereby granted.*?|"
    r"licensed under the apache license.*?|"
    r"this program is free software.*?|"
    r"without warranty of any kind.*?|"
    r"limitations under the license.*?)"
    r"(?=\n\n|\Z)",
    re.I | re.S,
)


def clean_readme(text: str, max_chars: int = 20_000) -> str:
    """清理 README：去代码块、徽章、HTML、表格符号、许可证样板。"""
    if not text:
        return ""

    t = _BADGE_LINE.sub("", text)
    # 先切许可证段落，再切零散样板句
    t = _LICENSE_SECTION.sub("", t)
    t = _LICENSE_BOILERPLATE.sub("", t)
    t = _MD_NOISE.sub(lambda m: f" {m.group(1)} " if m.lastindex else " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    return t.strip()[:max_chars]


# ------------------------------------------------------------------ 提取
def extract_readme_tags(readme_md: str, topk: int = 50, *,
                        topics: list[str] | None = None,
                        name: str = "") -> dict[str, float]:
    """从 README 提取关键词（第一层：项目核心）。

    topk 默认 50（而非 20）：实测发现词典中的复合词会被更长的词吸收，
    例如"单元测试"在"单元测试覆盖率"中被吞掉。
    topK 过小会导致有效标签被截断，交由后续 normalize + floor 过滤噪声。
    """
    init_jieba()
    clean = clean_readme(readme_md)
    if not clean:
        return {}

    pairs = jieba.analyse.extract_tags(clean, topK=topk, withWeight=True)
    stops = _stopwords()
    out: dict[str, float] = {}
    for word, weight in pairs:
        w = word.strip().lower()
        if len(w) < 2:
            continue
        # 纯数字/纯符号跳过
        if not re.search(r"[a-z\u4e00-\u9fff]", w):
            continue
        # ⚠️ 显式二次过滤停用词：
        #    jieba 的 set_stop_words 对 extract_tags 的过滤存在遗漏，
        #    这里做兜底，保证停用词一定不进入标签集。
        if w in stops:
            continue
        out[w] = float(weight)

    # ⭐ 噪声过滤（源头治理）：README 套话 / 无指向通用词不进标签集。
    #    只过滤 TF-IDF 部分 —— 词典回填的词是人工维护的技术术语，保留。
    out = filter_noise_tags(out)

    # ⭐ 技术性闸门（白名单）：英文标签必须有技术依据，否则一律丢弃。
    #    修的是"README 里的普通名词当作标签"（anything / track / checkpoint / available…）
    tset = _topic_set(topics)
    out = {t: v for t, v in out.items()
           if not t.isascii() or is_justified_tag(t, topics=tset)}

    # ⭐ 词典回填：技术词典里收录的词若在原文出现但未被 TF-IDF 选中，
    #    给予其一个不高不低的基准分，保证专有名词不被漏掉。
    #    （回填词本身来自人工维护的技术词典 = 天然有依据，不再过闸门）
    out = _backfill_dict_terms(clean, out)

    # 仓库名自标签顺手去掉（retag 时也能清掉历史遗留）
    if name:
        low = name.lower()
        out = {t: v for t, v in out.items() if t != low}
    return out


def _dict_terms() -> set[str]:
    """从技术词典文件读取所有术语（缓存）。"""
    global _DICT_TERMS_CACHE
    if _DICT_TERMS_CACHE is None:
        terms: set[str] = set()
        text = _load_dict_file(DICT_DIR / "tech_terms.txt")
        for line in text.splitlines():
            parts = line.split()
            if parts:
                terms.add(parts[0].lower())
        _DICT_TERMS_CACHE = terms
    return _DICT_TERMS_CACHE


_DICT_TERMS_CACHE: set[str] | None = None


# ---------------------------------------------------------------- 噪声标签

_NOISE_CACHE: set[str] | None = None


def tech_terms() -> set[str]:
    """技术词典里的术语集合（公开入口，供画像加权使用）。"""
    return _dict_terms()


# 允许保留的 2 字符标签（这些确实指向技术方向，不能一刀切）
_SHORT_OK = {
    "ai", "ml", "ui", "ux", "db", "os", "js", "ts", "go", "c", "r",
    "3d", "2d", "vr", "ar", "ci", "ip", "qt", "nlp", "cv", "k8s",
}


def noise_tags() -> set[str]:
    """噪声词表（dict/noise_tags.txt，可维护、可审计）。"""
    global _NOISE_CACHE
    if _NOISE_CACHE is None:
        words: set[str] = set()
        try:
            text = (DICT_DIR / "noise_tags.txt").read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                for w in line.split():
                    words.add(w.lower())
        except OSError:
            pass
        _NOISE_CACHE = words
    return _NOISE_CACHE


def is_noise_tag(tag: str) -> bool:
    """标签是否属于噪声。

    ⭐ 这是**统一入口**：画像构建、补货查询词、前端展示都必须过它 ——
       之前的问题正是"refill 有 _is_generic 过滤，但 GitHub topics 那条链路没过滤"，
       导致 support / install / https / easily 进了画像、还会被当成爬虫查询词。

    噪声的几类：
      ① README 模板套话（install / usage / license / contributing…）
      ② 无指向通用词（good / easily / version / tools / platform…）
      ③ 元信息与收录类（https / json / awesome-list / list…）
      ④ 短碎片（rc / lw / id 这类切词残留；见 _SHORT_OK 白名单）
    """
    t = (tag or "").strip().lower()
    if not t or len(t) < 2:
        return True
    if t in noise_tags():
        return True
    # 纯版本号 / 年份（v2 / 1.0.3 / 2024）
    if re.fullmatch(r"v?\d+(\.\d+)*", t) or re.fullmatch(r"(19|20)\d{2}", t):
        return True
    # 没有任何字母汉字（纯符号 / emoji）
    if not re.search(r"[a-z\u4e00-\u9fff]", t):
        return True
    # ⚠️ 2 字符英文标签通常是切词碎片（实测出现过 rc / lw / id / dsh），
    #    只保留确实指向技术方向的白名单（ai / ui / db / js…）。
    if len(t) == 2 and re.fullmatch(r"[a-z]{2}", t) and t not in _SHORT_OK:
        return True
    return False


def filter_noise_tags(tags: dict[str, float]) -> dict[str, float]:
    """按噪声表过滤标签字典（多处复用的便捷包装）。"""
    return {t: w for t, w in tags.items() if not is_noise_tag(t)}


def _stopwords() -> set[str]:
    """从停用词文件读取（缓存）。"""
    global _STOPWORDS_CACHE
    if _STOPWORDS_CACHE is None:
        words: set[str] = set()
        text = _load_dict_file(DICT_DIR / "stop_words.txt")
        for line in text.splitlines():
            for w in line.split():
                if w:
                    words.add(w.lower())
        _STOPWORDS_CACHE = words
    return _STOPWORDS_CACHE


_STOPWORDS_CACHE: set[str] | None = None


def _backfill_dict_terms(text: str,
                         tags: dict[str, float]) -> dict[str, float]:
    """把原文里出现、但未被 TF-IDF 选中的词典术语补回来。"""
    terms = _dict_terms()
    stops = _stopwords()
    if not terms or not tags:
        return tags

    baseline = min(tags.values()) * 0.9   # 略低于当前最低分
    lowered = text.lower()
    for term in terms:
        if term in tags:
            continue
        # ⚠️ 回填时也要排除停用词（词典与停用词表可能有交集）
        if term in stops:
            continue
        # 只回填确实出现在原文中的术语
        if term in lowered:
            tags[term] = baseline
    return tags


def extract_text_tags(text: str, topk: int = 15) -> dict[str, float]:
    """从任意文本提取标签（用于 Issue 热词、搜索词）。"""
    return extract_readme_tags(text, topk=topk)


def normalize(tags: dict[str, float], floor: float = 0.05) -> dict[str, float]:
    """归一化到 0~1。

    这是必需的修正：不做归一化，README 长的仓库所有标签分会虚高，
    排序会被 README 长度带偏。
    """
    if not tags:
        return {}
    mx = max(tags.values())
    if mx <= 0:
        return {}
    out = {k: v / mx for k, v in tags.items()}
    # 过滤掉相对贡献过低的噪声标签
    return {k: round(v, 4) for k, v in out.items() if v >= floor}


def merge_layers(
    readme_tags: dict[str, float] | None = None,
    footprint_tags: dict[str, float] | None = None,
    issue_tags: dict[str, float] | None = None,
    normalize_result: bool = True,
) -> dict[str, float]:
    """三层标签合并。

    第一层 README   ×1.0  项目核心
    第二层 用户足迹 ×0.6  Star / 完读
    第三层 Issue 热词 ×0.3

    同一个词出现在多层会被叠加强化 —— 这正确反映了
    "既是项目核心、又是用户关注点"的双重信号。
    """
    merged: dict[str, float] = {}

    def _add(tags: dict[str, float] | None, layer: str) -> None:
        if not tags:
            return
        w = TAG_LAYER_WEIGHTS[layer]
        for k, v in tags.items():
            key = k.strip().lower()
            if len(key) < 2:
                continue
            merged[key] = merged.get(key, 0.0) + float(v) * w

    _add(readme_tags, "readme")
    _add(footprint_tags, "footprint")
    _add(issue_tags, "issue")

    return normalize(merged) if normalize_result else merged


def tags_to_list(tags: dict[str, float], topk: int = 20) -> list[dict]:
    """转成排好序的列表，便于 JSON 输出。"""
    items = sorted(tags.items(), key=lambda kv: -kv[1])[:topk]
    return [{"tag": k, "weight": round(v, 4)} for k, v in items]


# ------------------------------------------------------------------ 关键词语句
def extract_query_tags(query: str, topk: int = 10) -> dict[str, float]:
    """从搜索词提取标签（搜索干预用）。

    搜索词通常很短，可能整句就是一个词（如 "llm inference"），
    此时 jieba 的 TF-IDF 可能因为词太短而返回空，需要降级到分词。
    """
    init_jieba()
    q = (query or "").strip()
    if not q:
        return {}

    long_enough = len(q) >= 6
    if long_enough:
        tags = extract_readme_tags(q, topk=topk)
        if tags:
            return normalize(tags, floor=0.0)

    # 降级：直接分词，每个词权重相同
    words = [w.strip().lower() for w in jieba.cut(q) if len(w.strip()) >= 2]
    if not words:
        return {}
    eq = 1.0 / len(words)
    return {w: eq for w in dict.fromkeys(words)}

# ---------------------------------------------------------------- 技术性闸门（白名单）

# 技术形态：含数字（gpt4 / llama3 / web3）、连字符（voice-cloning）、版本点号（vue3）
_TECH_SHAPE = re.compile(r"[a-z]+\d|\d+[a-z]|[a-z]+-[a-z]+")


def _topic_set(topics: list[str] | None) -> set[str]:
    out: set[str] = set()
    for t in (topics or []):
        tt = str(t).strip().lower()
        if tt:
            out.add(tt)
    return out


def is_justified_tag(tag: str, *, topics: set[str] | None = None) -> bool:
    """标签是否有"技术依据" —— 白名单闸门。

    ⭐ 为什么改成白名单（血泪教训）
       前几轮一直在扩黑名单（support/install/https/easily…），但**黑名单永远漏词**：
       接着又冒出 anything / track / checkpoint / available / segment，
       甚至仓库名本身（efficientsam）都能当标签出现在卡片上。
       README 是自然语言，TF-IDF 从里面捞出来的英文名词里混着大量普通词汇，
       逐个封杀是无底洞。

       换个方向：**只保留能证明是技术词的标签**，依据满足其一即可：
         ① 命中技术词典（人工维护的技术术语）
         ② 命中该仓库的 GitHub topics（仓库作者自己打的标签，最干净的一手信息）
         ③ 命中领域方向词表
         ④ 具备技术形态（含数字/连字符，如 gpt4、voice-cloning）
       中文标签维持原策略（黑名单 + 长度），因为中文技术词碎、白名单会误伤。
    """
    t = (tag or "").strip().lower()
    if not t:
        return False
    if not t.isascii():
        return True                      # 中文走黑名单逻辑，不做白名单
    if t in _dict_terms():
        return True
    if topics and t in topics:
        return True
    # 语料库 topics 并集（真实英文技术词表，见 dict/topics_vocab.txt）
    # —— 没有这份词表时，25% 的仓库会一个标签都留不下（实测），召回直接塌掉
    if t in _vocab_terms():
        return True
    if _TECH_SHAPE.search(t):
        return True
    return False


_VOCAB_CACHE: set[str] | None = None


def _vocab_terms() -> set[str]:
    """语料库 topics 并集（由 jobs/retag.py 自动生成）。"""
    global _VOCAB_CACHE
    if _VOCAB_CACHE is None:
        words: set[str] = set()
        try:
            text = (DICT_DIR / "topics_vocab.txt").read_text(encoding="utf-8")
            for line in text.splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    words.add(line.lower())
        except OSError:
            pass
        _VOCAB_CACHE = words
    return _VOCAB_CACHE


def display_tags(tags: dict[str, float], *, name: str = "", owner: str = "",
                 topics: list[str] | None = None, limit: int = 8,
                 ) -> list[tuple[str, float]]:
    """给前端展示用的标签列表：三层过滤后按权重排。

    卡片上显示的标签必须干净 —— 这是用户直接看到的东西。
    过滤顺序：噪声表 → 仓库名自标签 → 技术性闸门。
    若闸门后一个不剩，退回"噪声过滤后的前 3 个"（宁可少，也不要空）。
    """
    if not tags:
        return []
    clean = filter_noise_tags(tags)

    # 仓库名自标签（efficientsam 这种由名字派生的词）
    self_words: set[str] = set()
    for raw in (name, owner):
        if not raw:
            continue
        low = str(raw).lower()
        self_words.add(low)
        for part in low.replace("/", "-").replace("_", "-").split("-"):
            part = part.strip()
            if len(part) >= 3:
                self_words.add(part)
    clean = {t: v for t, v in clean.items() if t not in self_words}

    tset = _topic_set(topics)
    gated = {t: v for t, v in clean.items() if is_justified_tag(t, topics=tset)}
    chosen = gated or dict(sorted(clean.items(), key=lambda kv: -kv[1])[:3])
    return sorted(chosen.items(), key=lambda kv: -kv[1])[:limit]
