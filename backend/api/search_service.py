"""搜索服务 —— 真正的检索，不是"给推荐结果打个分"。

⚠️ 为什么必须单独做一个：

    原来的搜索走 `build_feed(search_query=...)`，本质是
    "先跑 7 路个性化召回，再给命中搜索词的候选加一点分"。
    实测结果完全是错的：

        搜 "rust"           → 返回 tailwindcss / chroma / duckdb
        搜 "vector database"→ 返回 bytedance-ui/ui-tars / vuejs/core

    因为召回集合里根本没有"匹配 rust 的仓库"这个约束，
    加多少分也救不回来 —— 没进候选池的东西永远不会出现。

    搜索按钮需要的是检索：全库扫，按匹配度排序，与个性化无关。

匹配策略（按可信度降级）：
    ① 精确短语命中   full_name / name / description 里直接含 query
    ② 标签命中       tags_json 里有 query 提取出的标签（权重加权）
    ③ topics 命中    GitHub topics 数组
    ④ 分词全命中     查询分词后所有词都出现在可搜文本里
    ⑤ 分词部分命中   至少命中一个词（兜底，保证不空结果）

排序：
    相关度（匹配分）× 仓库质量分，质量分只占小权重 ——
    搜索的首要目标是"找得到"，不是"推得准"。
"""
from __future__ import annotations

import json
import re
import sqlite3

from core.config import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_MIN_KEEP,
    SEARCH_MIN_SCORE_RATIO,
    SEARCH_QUALITY_WEIGHT,
    SEARCH_SUGGEST_MIN_DF,
)
from tags.extractor import extract_query_tags, init_jieba


# ------------------------------------------------------------------ 数据装配

def _load_all_repos(conn: sqlite3.Connection) -> list[dict]:
    """载入全部可搜索仓库。

    规模说明：本项目是"本地自推流"，仓库量级在万级以内，
    全量载入内存做匹配完全够用（84 个仓库实测 < 5ms）。
    若将来接入真实爬虫到十万级，应改为 SQL LIKE 预筛 + 只在候选集上算分。
    """
    rows = conn.execute(
        """SELECT id, owner, name, full_name, description, language,
                  topics, tags_json, stars, license_spdx, license_risk,
                  forgotten_score, quality_score, velocity_score,
                  freshness_score, pushed_at_gh, created_at_gh,
                  is_archived, is_dead,
                  substr(readme_md, 1, 4000) AS readme_head
           FROM repos
           WHERE is_archived = 0 AND is_dead = 0"""
    ).fetchall()

    out: list[dict] = []
    for r in rows:
        try:
            topics = json.loads(r["topics"]) if r["topics"] else []
        except (json.JSONDecodeError, TypeError):
            topics = []
        try:
            tags = json.loads(r["tags_json"]) if r["tags_json"] else {}
        except (json.JSONDecodeError, TypeError):
            tags = {}
        # ⚠️ README 正文必须参与匹配。
        #    中文查询（"推理"、"量化"）在标签里几乎找不到 ——
        #    实测 84 个仓库只有 8 个带中文标签。
        #    但 llama.cpp 的 README 正文里就有"推理加速"，能救回这类查询。
        #    截 4000 字符：足够覆盖开头的能力描述，又不至于把整篇吃进内存。
        readme = (r["readme_head"] or "").lower()
        out.append({
            "id": r["id"],
            "owner": r["owner"] or "",
            "name": r["name"] or "",
            "full_name": r["full_name"] or "",
            "description": r["description"] or "",
            "language": r["language"] or "",
            "topics": topics if isinstance(topics, list) else [],
            "tags": {k: float(v) for k, v in tags.items()},
            "readme": readme,
            "stars": r["stars"] or 0,
            "license_spdx": r["license_spdx"],
            "license_risk": r["license_risk"],
            "forgotten_score": r["forgotten_score"] or 0.0,
            "quality_score": r["quality_score"] or 0.0,
            "velocity_score": r["velocity_score"] or 0.0,
            "freshness_score": r["freshness_score"] or 0.0,
            "pushed_at_gh": r["pushed_at_gh"],
            "created_at_gh": r["created_at_gh"],
        })
    return out


# ------------------------------------------------------------------ 匹配打分

_WORD_SPLIT = re.compile(r"[\s,+/]+")
# 仓库名/owner 里的分词边界：- _ . 和斜杠都算分隔符
_NAME_SPLIT = re.compile(r"[-_./\s]+")
# 短于此长度的查询词一律要求"整词命中"，不做子串包含
_WHOLE_WORD_MAX = 3

# 判定"实质命中"的分数门槛（见 _match_score 的层说明）。
#
# ⚠️ 设计依据（本轮踩坑）：
#    ⑦⑧⑨ 三层是"碎片层"——按命中词个数一点点加分，单次只给 0.35~0.6。
#    如果只用"最终分 > 0"判断是否相关，那么任何大仓库只要在 README 里
#    偶然出现几个查询词，就能拿到 1.0 左右，再叠加 quality_score×0.35
#    的兜底分（torvalds/linux 质量分 0.97 → +0.34），直接挤掉真正相关的仓库。
#
#    实测：搜「向量数据库」时 nektos/act（GitHub Actions 工具）排第 2，
#    它只是在 README 里顺带提了"数据"两个字。
#
#    所以必须区分「实质命中」和「碎片命中」：
#      实质命中 = 名称/描述/topics/标签这些结构化字段真命中（①~⑥）
#      碎片命中 = 只在正文/长文本里出现过（⑦⑧⑨）
#    只有拿到实质命中的仓库才有资格进入结果集。
_SUBSTANTIVE_MIN_SCORE = 2.0

# 判断"英文词 t 是否作为一个独立单词出现在文本里"用的边界正则。
#
# ⚠️ 为什么必须有这个（本轮新修 bug）：
#    搜「AI绘画」时 _query_terms 会切出 ['ai绘画','ai','绘画']，
#    而 ⑦ 层用的是裸子串 `t in haystack`。于是 2 字符的 "ai" 命中了
#    facebook/react 的 README（`maintain` / `certain` / `domain` 里都有 "ai"），
#    microsoft/vscode、sqlite-org/sqlite 同理。三个词各命中一次 →
#    `0.6×3 = 1.8` 加分，结果一屏全是不相关的头部仓库，且分数整齐落在 1.28。
#
#    同理 ⑧ 层 `t in desc`、⑨ 层 `t in readme` 也是裸子串，
#    中文还好（不会被拉丁字母单词包含），但短英文词会被大量误伤。
_EN_TOKEN_RE = re.compile(r"^[a-z0-9][a-z0-9+#.\-]*$")


def _has_en_word(hay: str, needle: str) -> bool:
    """英文 needle 是否作为独立单词出现在 hay 中（要求词边界）。

    用正则前后断言 (?<![a-z0-9]) / (?![a-z0-9])：
        "ai" 在 "maintain" 里 → 前面是 m，不匹配 ✅
        "ai" 在 "coqui-ai/tts" 里 → 前后都是非字母数字，匹配 ✅

    仅对形如纯英文/数字的短词启用；中文词直接返回子串判断结果
    （中文没有"字母被单词包含"的问题，且 jieba 已切好词）。
    """
    if not needle or not _EN_TOKEN_RE.match(needle):
        return needle in hay
    if len(needle) > _WHOLE_WORD_MAX:
        return needle in hay          # 长英文词子串匹配足够可靠
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", hay) is not None


def _name_contains(hay: str, needle: str, *, whole: bool = False) -> bool:
    """判断仓库名里是否真的包含搜索词。

    ⚠️ 短词必须整词匹配。
       实测：搜 "Ai" 命中 `tailwindlabs/tailwindcss`（owner 含 "ai"）、
       搜 "go" 会命中一堆无关仓库。
       这类假阳性会让搜索框看起来"什么都搜得出来"，实际完全不准。

    规则：
        needle 长度 ≤ _WHOLE_WORD_MAX(3) → 必须作为独立 token 出现
        needle 更长                      → 允许子串（如 "langchain" 匹配 "langchain-proto"）
        whole=True 时无条件要求整词（用于 full_name 这种长串，避免 owner 误伤）
    """
    if not needle:
        return False
    if whole or len(needle) <= _WHOLE_WORD_MAX:
        return needle in _NAME_SPLIT.split(hay)
    return needle in hay


def _query_terms(query: str) -> list[str]:
    """把查询拆成用于匹配的词。

    ⚠️ 中文必须补子词切分。
       实测 bug：搜「向量数据库」命中 0 条 ——
       因为 _WORD_SPLIT 按空格/逗号切，中文连写切不开；
       而 extract_query_tags 对短查询直接原样返回整串
       （它本来是给 README 提取标签用的，不负责切查询词）。
       结果 terms 只有 ['向量数据库'] 一项，而库里的标签是
       `向量` / `数据库` / `vector` / `database`，一个字都对不上。

    拆词优先级（越靠前越可靠）：
        ① 完整短语        —— "vector database" 整体命中
        ② 分隔符切分      —— 英文空格/逗号场景
        ③ jieba 分词      —— 中文连写场景 ← 本次新增
        ④ jieba 关键词提取 —— 长查询的语义核心
    """
    q = (query or "").strip().lower()
    if not q:
        return []

    terms: list[str] = []

    def _add(w: str) -> None:
        w = (w or "").strip()
        if len(w) >= 2 and w not in terms:
            terms.append(w)

    # ① 整体短语（最高优先级，用于精确短语命中）
    _add(q)
    # ② 分隔符切分
    for w in _WORD_SPLIT.split(q):
        _add(w)
    # ③④ jieba 分词 + 关键词提取
    try:
        init_jieba()
        import jieba
        # ⚠️ 必须用 cut_for_search，不能用 cut。
        #    tech_terms.txt 把「向量数据库」注册成了整词（对 README 提标签有好处），
        #    于是 jieba.cut('向量数据库') → ['向量数据库'] 一个词都不切；
        #    用户搜「向量数据库」时匹配不到库里的 `向量` / `数据库` 标签 → 0 结果。
        #    cut_for_search 会额外切出子词：
        #      '向量数据库' → 向量 / 数据 / 据库 / 数据库 / 向量数据库
        for w in jieba.cut_for_search(q):
            _add(w)
        # 关键词提取：长查询兜底，补上语义核心词
        for t in extract_query_tags(query, topk=8):
            _add(t)
    except Exception:
        pass
    return terms


def _is_subsumed(t: str, terms: list[str]) -> bool:
    """t 是否只是某个更长查询词的连续子串。

    ⚠️ 「向量数据库」会被 jieba 切出 ['向量数据库','向量','数据','据库','数据库']。
       后四个都是第一个的碎片，它们作为独立查询词参与 ⑧⑨ 碎片层没问题，
       但不该在 ⑥ 标签层拿到"整词命中"资格 ——
       实测 bug：nektos/act 的标签里恰好有「数据」（README 里提了一句），
       于是搜「向量数据库」它拿到实质分过闸，排在结果第 2。
       同理「ai绘画」里的「ai」也不该独立代表查询意图。
    """
    return any(t != o and t in o for o in terms if len(o) > len(t))


def _match_score(repo: dict, terms: list[str], qtags: dict[str, float],
                 raw_query: str) -> tuple[float, list[str], float]:
    """返回 (总分, 命中原因列表, 实质命中分)。

    每一层给固定档位的分，越可靠的层分越高。

    ⚠️ 第三个返回值 `substantive` 是「结构化字段命中分」（①~⑥ 层累加），
       不含 ⑦⑧⑨ 的碎片分。调用方用它判断"这个仓库是否真的关于搜索词"，
       详见 _SUBSTANTIVE_MIN_SCORE 的注释。
    """
    if not terms:
        return 0.0, [], 0.0

    owner = repo["owner"].lower()
    name = repo["name"].lower()
    full = repo["full_name"].lower()
    desc = repo["description"].lower()
    lang = repo["language"].lower()
    topics = [str(t).lower() for t in repo["topics"]]
    tags = repo["tags"]
    q = raw_query.strip().lower()

    # ⚠️ 所有匹配字段都不含 owner（组织名）。
    #
    #    实测 bug：搜「AI绘画」时 `coqui-ai/TTS`、`chroma-core/chroma`、
    #    `quip-ai/spec-decode-kit` 全部命中 —— 只因组织名里带 `-ai`。
    #    组织名回答的是"谁发布的"，不是"这个仓库是关于什么的"。
    #    把它算进匹配等于按发布者名字搜，结果就是"凡是 -ai 结尾的 org
    #    发的所有仓库都会出现在 AI 相关搜索里"。
    #
    #    全名仍然参与"精确等于查询词"的判定（搜索框直接粘仓库全名要能命中），
    #    但不参与任何子串/分词匹配。
    name_field = name
    desc_field = desc
    topic_field = " ".join(topics)
    tag_field = " ".join(str(k).lower() for k in tags)
    readme_field = (repo.get("readme") or "").lower()

    # 结构化字段（用于实质命中判定）与全文字段（用于碎片兜底）
    short_field = f"{name_field} {desc_field} {topic_field} {tag_field}"

    score = 0.0
    reasons: list[str] = []

    # ① 仓库全名精确等于查询词（粘贴仓库名进来搜的场景）
    if q and q == full:
        score += 6.0
        reasons.append("仓库全名匹配")
    # ② 仓库名精确/包含（注意：只看 name，不含 owner）
    elif q and q == name:
        score += 6.0
        reasons.append("名称完全匹配")
    elif q and _name_contains(name_field, q):
        score += 4.0
        reasons.append("名称包含搜索词")

    # ③ 描述里出现完整查询短语
    if q and len(q) >= 3 and q in desc_field:
        score += 2.5
        reasons.append("简介含搜索词")

    # ④ 语言精确匹配（搜 "rust" 应该优先出 Rust 项目）
    #
    # ⚠️ 单字母/超短查询词不参与 —— 否则搜 "go" 会把所有 Go 项目拉进来，
    #    搜 "c" 会把所有 C 项目拉进来，用户想搜的是"名字里有 c 的仓库"。
    if len(q) >= 3:
        for t in terms:
            if t == lang:
                score += 3.0
                reasons.append(f"{repo['language']} 项目")
                break

    # ⑤ topics 命中
    topic_hits = 0
    for t in terms:
        for tp in topics:
            if t == tp:
                score += 2.2
                topic_hits += 1
            elif len(t) >= 4 and _has_en_word(tp, t):
                score += 1.1
                topic_hits += 1
    if topic_hits:
        reasons.append(f"标签 {topic_hits} 项匹配")

    # ⑥ 仓库标签命中（按标签权重加权，且要求查询词本身就是标签）
    #
    # ⚠️ 必须"整词相等"，不能用 `_has_en_word` 或子串。
    #    实测 bug：搜「向量数据库」时几乎所有仓库都"标签命中 1 个" ——
    #    因为 jieba 把「向量数据库」切出了「数据」，而「数据」是常见技术标签，
    #    于是 nektos/act（GitHub Actions 工具）也判定为相关。
    #    查询词必须原样等于标签名，才算这个仓库"关于"它。
    #
    # ⚠️ 实质分资格只给 len(t) >= 3 的词（第二层防线）：
    #    「向量数据库」切出的「数据」本身就是合法标签，整词相等拦不住它。
    #    但 2 字中文词太通用（数据/向量/据库），任何仓库都可能挂这类标签；
    #    真正的技术标签几乎都 ≥3 字符（rvc/llama/rust/语音克隆/向量数据库）。
    #    所以 2 字词命中标签只加普通分，不计入 substantive。
    #    （此时 substantive 还没初始化，先暂存，⑦ 层结算时并入）
    tag_hits = 0
    _tag_substantive = 0.0
    for t in terms:
        # ⚠️ 碎片子词（「数据库」⊂「向量数据库」）命中标签打 6 折：
        #    ① 单个碎片命中不足以过实质闸（nektos/act 只有「数据库」→ 1.8 < 2.0 被拦）
        #    ② 但两个碎片命中可以（coqui 有「语音」+「克隆」→ 3.6 过闸，
        #       db-internals 有「数据库」+「向量」→ 同理）。
        #    中文库的标签粒度本来就是 2-3 字词，全杀会把真结果一起杀掉。
        subsumed = _is_subsumed(t, terms)
        if t in tags:
            gain = 2.0 + min(1.0, tags[t])
            if subsumed:
                gain *= 0.6
            score += gain
            if len(t) >= 3 or subsumed:
                _tag_substantive += gain
            tag_hits += 1
    if tag_hits:
        reasons.append(f"技术标签命中 {tag_hits} 个")

    # ⑦ jieba 提取出的标签命中（中文查询走这里）
    for t, w in qtags.items():
        if t in tags and t not in terms:
            score += 1.5 + min(1.0, tags[t])
            tag_hits += 1

    # ══ 到这里为止的分数 = "结构化字段真命中"，作为实质命中分 ══
    #
    # ①~⑦ 层全部基于名称/描述/topics/技术标签 —— 这些字段是仓库的
    # "自我介绍"，命中了才说明"这个仓库是关于搜索词的"。
    # 调用方用这个值做硬闸，拦掉只在正文里蹭到只言片语的仓库。
    substantive = score + _tag_substantive

    # ⑧ 分词命中（碎片层，只加分不参与实质判定）
    #
    # ⚠️ 用 _has_en_word 而不是裸 `in`：
    #    见 _has_en_word 注释 —— 裸子串会让 "ai" 命中 maintain/certain/domain。
    hits = sum(1 for t in terms if _has_en_word(short_field, t))
    if hits == len(terms) and len(terms) > 1:
        score += 1.8
        reasons.append("全部关键词命中")
    elif hits:
        score += 0.6 * hits
        reasons.append(f"部分关键词命中({hits}/{len(terms)})")

    # ⑨ 简介逐词命中（弱信号，只补一点分）
    for t in terms:
        if len(t) >= 3 and _has_en_word(desc_field, t):
            score += 0.4

    # ⑩ README 正文命中
    #
    # 中文查询主要靠这一层 —— 中文标签太稀疏，不给正文兜底的话
    # 搜「推理」「量化」「文生图」会零结果。
    #
    # ⚠️ 但正文兜底不能给"实质命中"资格（substantive 已在上方结算）：
    #    README 动辄几十 KB，任何 2-3 字符英文词都能在任意大仓库里
    #    偶然出现一次。这是「搜 向量数据库 出来 nektos/act」的根源 ——
    #    act 的 README 里顺带提了"数据"两个字，就拿到 1.05 分。
    if readme_field:
        readme_hits = 0
        for t in terms:
            if len(t) >= 2 and _has_en_word(readme_field, t):
                score += 0.35
                readme_hits += 1
        if readme_hits:
            reasons.append(f"正文提到({readme_hits})")

    # ⑪ 中文语义加权
    #
    # 多字中文词（≥2 字）是用户真实意图所在。
    # 搜「语音克隆」时 jieba 给出 ['语音克隆','语音','克隆']，
    # 命中的中文词越多说明越相关，给它们额外加权拉开档次。
    #
    # ⚠️ 同样计入碎片层，不参与实质判定 —— 只看正文的中文命中
    #    依然可能是巧合（比如 README 里提了一句"语音"）。
    zh_terms = [t for t in terms if len(t) >= 2 and not _EN_TOKEN_RE.match(t)]
    if zh_terms:
        zh_hit = sum(1 for t in zh_terms if _has_en_word(short_field, t))
        if zh_hit:
            score += 1.2 * zh_hit
            reasons.append(f"中文语义命中({zh_hit})")

    return score, reasons, substantive


# ------------------------------------------------------------------ 主入口

def search(
    conn: sqlite3.Connection,
    user_id: int,
    query: str,
    *,
    limit: int = 20,
    offset: int = 0,
    language: str | None = None,
    min_stars: int | None = None,
    sort: str = "relevance",
) -> dict:
    """全库检索。

    参数：
        query      搜索词（必填，空则返回热门兜底）
        limit      每页数量
        offset     分页偏移
        language   按语言过滤（可选）
        min_stars  最低 star 数（可选）
        sort       relevance | stars | recent | forgotten

    ⚠️ 这个函数【不记录曝光】。
       搜索是用户主动发起的，不是推荐曝光；把它算进曝光会让
       流量池的 ctr 分母虚高。曝光只由 Feed 产生。
    """
    q = (query or "").strip()
    qtags = {}
    if q:
        try:
            qtags = extract_query_tags(q, topk=8)
        except Exception:
            qtags = {}

    terms = _query_terms(q)
    repos = _load_all_repos(conn)

    scored: list[tuple[float, dict, list[str]]] = []
    for r in repos:
        if language and (r["language"] or "").lower() != language.lower():
            continue
        if min_stars is not None and r["stars"] < min_stars:
            continue

        sc, reasons, substantive = _match_score(r, terms, qtags, q)

        # 硬闸用的字段（见下方注释）
        core_short_field = " ".join([
            r["name"].lower(),
            r["description"].lower(),
            " ".join(str(t).lower() for t in r["topics"]),
            " ".join(str(k).lower() for k in r["tags"].keys()),
        ])
        readme_field = (r.get("readme") or "").lower()

        if not q:
            # 空查询：按质量 × 遗珠排序（相当于"随便看看"）
            sc = r["quality_score"] + r["forgotten_score"] * 0.5
            reasons = ["随便看看"]
        else:
            # ── 硬闸：必须有"结构化字段命中" ──
            #
            # ⚠️ 这条闸是修「搜 AI绘画 出来一堆 vscode/react/chroma」的关键。
            #    之前只靠分数阈值（top × 0.45），但假阳性本身就能靠
            #    quality_score×0.35 撑到 1.0+，而真结果也就 1.2 —— 阈值区分不了。
            #
            #    实测污染路径：
            #      · chroma-core/chroma —— owner 里带 '-ai'，"ai" 被当成独立 token
            #      · facebook/react     —— README 的 maintain/certain 里含 "ai"
            #      · nektos/act         —— 只在 README 里顺带提了"数据"
            #
            #    判据分两种，满足其一即可：
            #      A. 结构化字段实质命中 ≥ _SUBSTANTIVE_MIN_SCORE
            #      B. 查询的"核心词"（完整查询串 / 最长的词）真命中
            #
            #    为什么需要 B：中文查询在标签体系里覆盖率极低。
            #    搜「文生图」「绘画」时库里的标签是英文的 `stable-diffusion`，
            #    只有 README 正文里写着"文生图"。若不给正文口子这类查询恒为空。
            #    但 B 必须限定在**核心词**上 —— 用「数据」这种子词去撞正文，
            #    就会撞出 nektos/act。
            core_hit = False
            # 核心词 = 完整查询串 + 最长的两个词；剔除只是碎片的子词
            core_terms = [q] + sorted(terms, key=lambda t: -len(t))[:2]
            core_terms = [t for t in dict.fromkeys(core_terms)
                          if len(t) >= 2 and not _is_subsumed(t, core_terms)]
            blob = f"{core_short_field} {readme_field}"
            for t in core_terms:
                is_zh = not _EN_TOKEN_RE.match(t)
                # 中文核心词放宽到正文（中文误伤率低）
                # 英文核心词只认结构化字段（正文太长不可信）
                if is_zh and _has_en_word(blob, t):
                    core_hit = True
                    break
                if not is_zh and _has_en_word(core_short_field, t):
                    core_hit = True
                    break

            if substantive < _SUBSTANTIVE_MIN_SCORE and not core_hit:
                continue

        if sc <= 0:
            continue

        # 质量分只占小权重 —— 搜索首要目标是"找得到"
        final = sc + r["quality_score"] * SEARCH_QUALITY_WEIGHT
        scored.append((final, r, reasons))

    # ── 相关性兜底过滤 ──
    # ⚠️ 为什么要砍尾部：低分项全是"沾一点边"的仓库。
    #    实测搜 "Ai" 时结果里混进了 microsoft/vscode、facebook/react
    #    （score 1.27，靠 quality_score 的兜底分撑上来），
    #    而真正相关的 newbie-ai/tiny-embed 是 4.23。
    #    1.27 分意味着"只在 README 正文里偶然出现过一次 ai"，
    #    不构成"这个仓库是关于 AI 的"。
    #
    #    规则：保留 ≥ 最高分 × SEARCH_MIN_SCORE_RATIO 的结果；
    #          只有当强相关结果一条都不剩时才整个放弃过滤 ——
    #          "有 4 条真正相关的" 远好过 "40 条里混着 4 条相关的"。
    if scored and q:
        scored.sort(key=lambda x: -x[0])
        top = scored[0][0]
        cutoff = top * SEARCH_MIN_SCORE_RATIO
        strong = [t for t in scored if t[0] >= cutoff]
        if strong:                     # 砍完还有 → 砍
            scored = strong

    # ── 排序 ──
    if sort == "stars":
        scored.sort(key=lambda x: (-x[1]["stars"], -x[0]))
    elif sort == "recent":
        scored.sort(key=lambda x: (x[1]["pushed_at_gh"] or "", -x[0]),
                    reverse=True)
    elif sort == "forgotten":
        scored.sort(key=lambda x: (-x[1]["forgotten_score"], -x[0]))
    else:
        scored.sort(key=lambda x: -x[0])

    total = len(scored)
    page = scored[offset:offset + limit]

    items = [
        _to_dict(r, sc, reasons, rank)
        for rank, (sc, r, reasons) in enumerate(page, start=offset)
    ]

    return {
        "items": items,
        "meta": {
            "query": q,
            "terms": terms,
            "total": total,
            "limit": limit,
            "offset": offset,
            "has_more": offset + limit < total,
            "sort": sort,
            "filters": {"language": language, "min_stars": min_stars},
        },
    }


def _to_dict(repo: dict, score: float, reasons: list[str], rank: int) -> dict:
    """搜索结果 → 前端结构。

    ⚠️ 刻意与 Feed 的 item 结构保持一致（repo_id/full_name/... 字段同名），
       这样前端可以复用同一个卡片组件，不用写两套渲染。
    """
    tags = sorted(repo["tags"].items(), key=lambda kv: -kv[1])[:12]
    return {
        "repo_id": repo["id"],
        "full_name": repo["full_name"],
        "owner": repo["owner"],
        "name": repo["name"],
        "description": repo["description"],
        "language": repo["language"],
        "stars": repo["stars"],
        "topics": repo["topics"][:8],
        "tags": [{"tag": k, "weight": round(v, 3)} for k, v in tags],
        "license_spdx": repo["license_spdx"],
        "license_risk": repo["license_risk"],
        "score": round(score, 4),
        # 搜索特有的字段
        "search_reasons": reasons,
        "rank": rank,
        "url": f"https://github.com/{repo['full_name']}",
        "reason": "；".join(reasons[:2]) if reasons else "搜索结果",
    }


# ------------------------------------------------------------------ 推荐词

def suggest(conn: sqlite3.Connection, prefix: str = "",
            limit: int = 10) -> dict:
    """搜索联想：补全语言、topics、热门技术标签。

    用于搜索框的实时下拉提示。
    """
    p = (prefix or "").strip().lower()

    langs = [
        r["language"] for r in conn.execute(
            """SELECT language, COUNT(*) AS n FROM repos
               WHERE language IS NOT NULL AND language != ''
               GROUP BY language ORDER BY n DESC LIMIT 40"""
        )
    ]

    tag_rows = conn.execute(
        "SELECT tags_json FROM repos WHERE tags_json IS NOT NULL"
    ).fetchall()
    tag_count: dict[str, int] = {}
    for r in tag_rows:
        try:
            for k in json.loads(r["tags_json"]):
                tag_count[k] = tag_count.get(k, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue
    # 只推荐出现在 SEARCH_SUGGEST_MIN_DF 个以上仓库的标签
    # —— 单仓库的专有词做联想没意义
    popular_tags = sorted(
        ((k, v) for k, v in tag_count.items() if v >= SEARCH_SUGGEST_MIN_DF),
        key=lambda kv: -kv[1],
    )

    topic_rows = conn.execute(
        "SELECT topics FROM repos WHERE topics IS NOT NULL"
    ).fetchall()
    topic_count: dict[str, int] = {}
    for r in topic_rows:
        try:
            for t in json.loads(r["topics"]):
                topic_count[str(t)] = topic_count.get(str(t), 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue
    top_topics = sorted(topic_count.items(), key=lambda kv: -kv[1])

    def _f(items):
        if not p:
            return items[:limit]
        return [x for x in items if p in str(x[0]).lower()][:limit]

    return {
        "languages": [x for x in _f([(l, 0) for l in langs])],
        "tags": _f(popular_tags),
        "topics": _f(top_topics),
    }


# ------------------------------------------------------------------ 热门兜底

def hot(conn: sqlite3.Connection, *, limit: int = 10) -> dict:
    """热门搜索词（空搜索框时的默认展示）。

    用"出现在最多仓库里的技术标签"当热门词 ——
    这不是真实搜索日志统计（本地应用没有全局搜索日志），
    但它反映的是"这个库里最主流的技术方向"，作为默认提示是合理的。
    """
    rows = conn.execute(
        "SELECT tags_json FROM repos WHERE tags_json IS NOT NULL"
    ).fetchall()
    cnt: dict[str, int] = {}
    for r in rows:
        try:
            for k in json.loads(r["tags_json"]):
                cnt[k] = cnt.get(k, 0) + 1
        except (json.JSONDecodeError, TypeError):
            continue
    # 过滤掉太短和纯数字
    words = [
        (k, v) for k, v in cnt.items()
        if len(k) >= 3 and not k.isdigit() and v >= 3
    ]
    words.sort(key=lambda kv: -kv[1])
    return {"hot": [{"word": k, "count": v} for k, v in words[:limit]]}
