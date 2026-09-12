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
