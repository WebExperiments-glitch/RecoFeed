"""全局配置：路径、常量、算法默认参数。

所有可调参数集中在这里，便于热更新与测试覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 路径
BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = BACKEND_DIR / "data"
DICT_DIR = BACKEND_DIR / "dict"

DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("RECOFEED_DB", DATA_DIR / "recofeed.db"))
SCHEMA_PATH = PROJECT_DIR / "docs" / "schema.sql"

# ---------------------------------------------------------------- 服务
HOST = os.getenv("RECOFEED_HOST", "127.0.0.1")
PORT = int(os.getenv("RECOFEED_PORT", "8000"))
CORS_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

# ---------------------------------------------------------------- 行为权重
# 数值迁移自字节系公开的推荐信号设计（收藏 > 完播 > 点赞）
EVENT_WEIGHTS: dict[str, float] = {
    # 负反馈
    "skip": -2.0,
    # 曝光（只作分母）
    "impression": 0.0,
    # 轻互动
    "view": 0.4,
    "dwell_3s": 0.8,
    "dwell_10s": 1.2,
    "dwell_30s": 1.8,
    # 深度行为（等价"完播"）
    "read_readme": 2.5,
    "open_files": 3.0,
    "open_issues": 2.0,
    "click_release": 2.5,
    # 表态
    "like": 3.0,
    "comment": 5.0,
    "share": 5.0,
    "copy_link": 4.0,
    # 转化（最高价值）
    "star_click": 8.0,
    "follow_owner": 9.0,
}

# ---------------------------------------------------------------- 三层标签权重
TAG_LAYER_WEIGHTS: dict[str, float] = {
    "readme": 1.0,     # 第一层：项目核心
    "footprint": 0.6,  # 第二层：用户足迹（Star / 完读）
    "issue": 0.3,      # 第三层：Issue / PR 词云
}

# ---------------------------------------------------------------- 用户画像来源权重
PROFILE_SOURCE_WEIGHTS: dict[str, float] = {
    "owned": 1.0,     # 能力圈：自建仓库
    "starred": 0.6,   # 兴趣圈：Star 仓库
    "read": 0.3,      # 注意力圈：完整阅读
}

# ---------------------------------------------------------------- 自建仓库降噪
# ⚠️ 用户的仓库列表里往往混杂练手项目（hello-world / tutorial / fork 的课程作业），
#    不过滤会严重污染"能力圈"画像，把用户误判成初学者。
#    这些特征词只用于【仓库名 + 描述】的匹配，不影响 README 标签提取。
TUTORIAL_PATTERNS: list[str] = [
    "tutorial", "course", "learning", "learn-", "study", "practice",
    "exercise", "homework", "assignment", "training", "demo", "example",
    "hello-world", "helloworld", "test-repo", "my-first", "first-repo",
    "playground", "sandbox", "scratch", "notes", "note-", "blog",
    "awesome-", "cheatsheet", "cheat-sheet", "interview",
]

# ---------------------------------------------------------------- 排序权重
RANK_WEIGHTS: dict[str, float] = {
    "w_deep_read": 0.35,      # 深读率（≈完播，已降权）
    "w_star": 0.25,           # 收藏/Star 率（最高）
    "w_comment": 0.12,
    "w_share": 0.10,
    "w_follow": 0.10,
    "w_like": 0.08,           # 已降权

    # ⭐ 冷启动先验：无曝光仓库的 engagement 兜底系数。
    #    率值 = 行为数/曝光数，新仓库曝光为 0 → 所有率值为 0 → engagement=0。
    #    用 quality_score × 此系数作为"预期表现"的先验，避免新仓库永无出头之日。
    "cold_prior": 0.6,

    # ⭐ 时效衰减（已修正为分段 + 保底）
    "recency_decay": 0.08,        # 90 天内的衰减率（每天）
    "recency_knee_days": 90.0,    # 拐点：超过此天数改用尾巴衰减
    "recency_tail_decay": 0.0015, # 拐点后的缓降速率
    "recency_floor": 0.35,        # 时效分下界：老仓库最多损失 65%，
                                  # 不再像原实现那样衰减到 1e-25 直接归零。
                                  # 这是遗珠召回能生效的前提。

    "velocity_boost": 1.5,
    "forgotten_boost": 1.8,   # 遗珠加成（本项目特色）
    "explore_rate": 0.15,
    "dedup_window": 200,

    # 保底：base 分最低不低于 quality_score 的这个比例，防止 0 分候选
    "base_floor_ratio": 0.05,
}

# ---------------------------------------------------------------- 待刷队列（缓存池）
# 队列见底时触发定向补货：爬虫看用户画像 → 决定抓什么 → 灌进队列
# 用户刷一条就从队列删一条，仓库本体与流量池统计保留。
QUEUE_LOW_WATERMARK = 6      # 剩 ≤ 6 条就补货（用户指定的水位）
QUEUE_TARGET_SIZE = 60       # 每次补货后希望队列达到的规模
QUEUE_INITIAL_SIZE = 30      # 新用户首次灌入量
QUEUE_REFILL_COOLDOWN_SEC = 30   # 补货冷却，防止短时间内反复触发爬虫
# ⚠️ 补货"失败"不该冻结冷却。
#     实测 bug：入队 0 条也刷新 _last_refill_at，30 秒内所有补货请求被
#     cooldown 拒绝 → 队列只出不进，压测 200 条里 16/20 轮队列恒为 0。
#     入队量低于这个比例，视为"没补到货"，不锁冷却，允许立刻重试。
QUEUE_REFILL_MIN_GAIN_RATIO = 0.3
# 冷却期内的空转重试间隔（秒），避免每次刷卡都真的去扫全库
QUEUE_REFILL_RETRY_SEC = 3

# 定向补货：每次按画像挑几个方向去抓
REFILL_MAX_QUERIES = 5       # 单次补货最多用几个查询方向
REFILL_PER_QUERY = 15        # 每个方向抓多少条

# ---------------------------------------------------------------- 流量池
POOL_LEVELS = [
    {"level": 0, "name": "cold", "quota": 200},
    {"level": 1, "name": "basic", "quota": 2_000},
    {"level": 2, "name": "mid", "quota": 20_000},
    {"level": 3, "name": "high", "quota": 200_000},
    {"level": 4, "name": "viral", "quota": 2_000_000},
]

# ⚠️ 配额自适应：上面的配额是"平台级"数值（抖音冷启动池就是几百）。
#    但自建部署的仓库池可能只有几十个，永远填不满 200 的配额，
#    流量池就永远静止 —— 晋级/淘汰机制形同虚设。
#    这里按池子规模封顶：有效配额 = min(配置配额, 池子规模 × 该系数)
#    系数 3 的含义：平均每个仓库被曝光 3 次即可评估其表现。
POOL_QUOTA_POOL_FACTOR = 3
POOL_QUOTA_MIN = 20        # 下限，避免池子极小时一两轮就跑完

POOL_PROMOTE_DEEP_RATE = 0.30   # 晋级所需深读率
POOL_PROMOTE_STAR_RATE = 0.03   # 晋级所需 Star 率
POOL_DEMOTE_DEEP_RATE = 0.08    # 淘汰的深读率下限

# ---------------------------------------------------------------- 搜索干预
SEARCH_WEIGHT = 2.0
SEARCH_DECAY_STEP = SEARCH_WEIGHT / 5   # = 0.4，5 次精确归零（修正乘性衰减缺陷）
SEARCH_MAX_REFRESH = 5
SEARCH_MIX_RATIO = 5                    # 每 5 个位置 1 个搜索位

# ---------------------------------------------------------------- 负反馈
NEG_FEEDBACK_MAX_TAGS = 5
NEG_FEEDBACK_BASE = 0.25         # 线性递减起始值
NEG_FEEDBACK_STEP = 0.04         # 每级递减
NEG_FEEDBACK_FLOOR = 0.05        # 保底值（防止第 4 个标签失效）
NEG_PENALTY_DAILY_DECAY = 0.95   # 惩罚每日衰减

# ---------------------------------------------------------------- 冷启动
COLD_START_PURE_TRENDING = 5     # 前 N 个纯 Trending
COLD_START_MIX_UNTIL = 20        # 到第 N 个为止是混合
COLD_START_TRENDING_RATIO = 0.7  # 混合阶段 Trending 占比
COLD_START_MIN_TAGS = 10         # 画像标签数达到此值算"热身完成"

# ---------------------------------------------------------------- 搜索
# ⚠️ 搜索与推荐的目标不同：
#     推荐要"推得准"（质量分权重要高），
#     搜索要"找得到"（匹配度必须压过质量分）。
#   实测教训：给质量分太高的权重，会导致搜 "rust" 返回 tailwindcss
#   —— 因为 tailwindcss 质量分高，把真正的 Rust 项目挤下去了。
#   所以这里只给质量分一点加成，匹配度始终是主排序依据。
SEARCH_QUALITY_WEIGHT = 0.35
SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 50
SEARCH_SUGGEST_MIN_DF = 2        # 联想词至少出现在 N 个仓库中
# 相关性兜底过滤：砍掉"只沾一点边"的低分结果。
# 实测搜 "Ai" 时 microsoft/vscode(1.27分) 会被混进
# newbie-ai/tiny-embed(4.23分) 的结果里 —— 靠的是 quality_score 兜底分。
# 它只在 README 正文里偶然出现过一次 "ai"，不代表这个仓库属于 AI 方向。
SEARCH_MIN_SCORE_RATIO = 0.45    # 低于最高分 45% 的结果不展示
SEARCH_MIN_KEEP = 5              # 但至少保留这么多条，避免阈值过严导致搜不到

# ---------------------------------------------------------------- 补货
# ⚠️ skip 的冷却窗口。
#    "划过去了"是信息流的默认行为，不是"不想看"。
#    把它当永久排除会导致：刷完一轮后候选池见底（实测 84 仓库剩 0），
#    队列再也补不满。所以只做短期冷却，过期后可以重新推。
#    dislike 才是永久排除。
SKIP_COOLDOWN_DAYS = 7

# ---------------------------------------------------------------- 完读判定
READ_WPM = 300                   # 中文阅读速度 字/分钟
DEEP_READ_MIN_SEC = 15
DEEP_READ_MAX_SEC = 180          # ⭐ 上限，防止长 README 永久无法判定
DEEP_READ_RATIO = 0.5
DEEP_READ_SCROLL = 0.95

# ---------------------------------------------------------------- 召回配额
RECALL_QUOTA: dict[str, int] = {
    "interest": 150,
    "forgotten": 80,
    "trending": 80,
    "fresh": 60,
    "following": 40,
    "explore": 60,
    "similar": 60,
}

# ---------------------------------------------------------------- LLM 翻译（OpenRouter 免费链 + DeepSeek 付费兜底）
# 用途：把仓库 标题 / description / README 翻成中文（解决"全是英文看不懂"）。
#
# ⚠️ 模型选型实测记录（2026-09-12）：
#    MiniMax M3/M2.7 的 :free 通道已下架；未充值 OpenRouter key 走付费 402。
#    级联顺序即降级顺序：免费模型 429/5xx/超时 → 自动切下一个；
#    DeepSeek（付费）是链尾兜底 —— 只有免费模型全崩了才用，控制成本：
#      ① nvidia/nemotron-3-super-120b-a12b:free   实测 18.7s/篇（OpenRouter 免费）
#      ② nvidia/nemotron-3-ultra-550b-a55b:free   实测 77s/篇（OpenRouter 免费）
#      ③ inclusionai/ling-3.0-flash-vl:free       （OpenRouter 免费）
#      ④ deepseek-flash                           DeepSeek V4.1-Flash 付费兜底，
#                                                 实测 1.9s/篇；官方文档确认可用
#                                                 thinking.type=disabled 关闭思考模式，
#                                                 翻译任务关掉后更快更省

def _load_llm_keys() -> dict[str, str]:
    """LLM key：优先环境变量，其次 backend/core/llm_local.json（已 gitignore）。"""
    keys = {
        "openrouter": os.getenv("OPENROUTER_API_KEY", "").strip(),
        "deepseek": os.getenv("DEEPSEEK_API_KEY", "").strip(),
    }
    if keys["openrouter"] and keys["deepseek"]:
        return keys
    try:
        import json
        p = BACKEND_DIR / "core" / "llm_local.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        for name in ("openrouter", "deepseek"):
            if not keys[name]:
                keys[name] = str(data.get(f"{name}_api_key", "")).strip()
    except Exception:
        pass
    return keys

_LLM_KEYS = _load_llm_keys()
OPENROUTER_API_KEY = _LLM_KEYS["openrouter"]
DEEPSEEK_API_KEY = _LLM_KEYS["deepseek"]

OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
DEEPSEEK_BASE_URL = os.getenv(
    "DEEPSEEK_BASE_URL", "https://api.deepseek.com")

LLM_CHAIN: list[dict[str, str]] = [
    {"id": "nvidia/nemotron-3-super-120b-a12b:free", "provider": "openrouter"},
    {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "provider": "openrouter"},
    {"id": "inclusionai/ling-3.0-flash-vl:free", "provider": "openrouter"},
    {"id": "deepseek-flash", "provider": "deepseek"},
]

# DeepSeek 是付费模型：单独设日限额，防止免费模型集体故障时烧穿钱包
DEEPSEEK_MAX_PER_DAY = 15

# 防护参数 —— 免费档限流（未充值账户官方限制：20 req/min、50 req/day）
# 本地限额留出余量，超限直接拒绝，不打上游 API。
LLM_MAX_PER_MINUTE = 12        # 每分钟最多 12 次（官方 20）
LLM_MAX_PER_DAY = 45           # 每日最多 45 次（官方 50，仅统计免费模型调用）
LLM_MODEL_COOLDOWN_SEC = 120   # 某模型收到 429 后的独享冷却
LLM_GLOBAL_COOLDOWN_SEC = 45   # 免费模型全部 429 时的全局冷却（期内直接拒绝）
LLM_TIMEOUT_SEC = 75.0         # 单模型请求超时（免费档推理偏慢）
LLM_MAX_TOKENS = 2500          # 输出 token 上限
LLM_DESC_MAX_CHARS = 400       # description 送翻的最大长度
LLM_README_MAX_CHARS = 2400    # README 送翻的最大长度（控制延迟与额度）


# ---------------------------------------------------------------- GitHub OAuth
def _load_auth_config() -> dict[str, str]:
    """GitHub OAuth 配置：优先环境变量，其次 backend/core/auth_local.json（已 gitignore）。"""
    cfg = {
        "client_id": os.getenv("GITHUB_CLIENT_ID", "").strip(),
        "client_secret": os.getenv("GITHUB_CLIENT_SECRET", "").strip(),
        "auth_secret": os.getenv("RECOFEED_AUTH_SECRET", "").strip(),
    }
    if cfg["client_id"] and cfg["client_secret"] and cfg["auth_secret"]:
        return cfg
    try:
        import json
        p = BACKEND_DIR / "core" / "auth_local.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        if not cfg["client_id"]:
            cfg["client_id"] = str(data.get("github_client_id", "")).strip()
        if not cfg["client_secret"]:
            cfg["client_secret"] = str(data.get("github_client_secret", "")).strip()
        if not cfg["auth_secret"]:
            cfg["auth_secret"] = str(data.get("auth_secret", "")).strip()
    except Exception:
        pass
    return cfg


_AUTH = _load_auth_config()
GITHUB_CLIENT_ID = _AUTH["client_id"]
GITHUB_CLIENT_SECRET = _AUTH["client_secret"]
# 会话签名密钥：没配就用固定 dev 值（本地开发够用，生产必须换）
AUTH_SECRET = _AUTH["auth_secret"] or "recofeed-local-dev-secret"
GITHUB_OAUTH_SCOPES = "read:user"
GITHUB_API = "https://api.github.com"
GITHUB_OAUTH_AUTHORIZE = "https://github.com/login/oauth/authorize"
GITHUB_OAUTH_TOKEN = "https://github.com/login/oauth/access_token"
# 登录成功后回跳的前端地址（Vite dev server）
AUTH_FRONTEND_REDIRECT = os.getenv(
    "RECOFEED_FRONTEND", "http://localhost:5173").rstrip("/")
SESSION_TTL_DAYS = 30

# ⚠️ OAuth 回调地址：必须与 GitHub OAuth App 里登记的 Redirect URI **完全一致**，
#    差一个字符都会报 redirect_uri_mismatch。
#    这里固定成 127.0.0.1（不用 localhost）：因为 Vite 代理配了 changeOrigin=true，
#    Host 会被改写成 127.0.0.1:8000，若用 localhost 会出现两种写法不一致的坑。
GITHUB_OAUTH_REDIRECT_URI = os.getenv(
    "RECOFEED_OAUTH_REDIRECT",
    f"http://{HOST}:{PORT}/api/auth/github/callback",
).strip()

AUTH_CONFIGURED = bool(GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET)

# ---------------------------------------------------------------- GitHub 实证圈权重
# ⚠️ 数值由用户拍定：
#   自建仓库（用户真在做的）> 点星仓库（用户觉得有意思），且要"非常猛地推"。
#   自建仓库：看更新时间 —— 本周有更新 0.5，一个月内 0.2，之后继续衰减。
#   点星仓库：按仓库星数从高到低排名 —— 第 1 名 0.4，第 10 名 0.3，第 20 名 0.2。
GITHUB_OWNED_WEEK = 0.50      # 7 天内推送
GITHUB_OWNED_MONTH = 0.20     # 30 天内推送
GITHUB_OWNED_QUARTER = 0.12   # 90 天内
GITHUB_OWNED_HALFYEAR = 0.08  # 180 天内
GITHUB_OWNED_YEAR = 0.05      # 一年内
GITHUB_OWNED_OLD = 0.03       # 更久
GITHUB_STARRED_TOP = 0.40     # 点星榜第 1
# 强信号放大：GitHub 实证圈进入画像时整体乘这个系数（"猛推"的量化开关）
GITHUB_SIGNAL_BOOST = 2.0
# 单次同步最多拉取多少（控制 API 调用量；OAuth 应用限额 5000/h，足够）
GITHUB_SYNC_MAX_OWNED = 200
GITHUB_SYNC_MAX_STARRED = 300
