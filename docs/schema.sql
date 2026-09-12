-- ============================================================
--  推流引擎数据库 Schema（SQLite 实现）
--  设计来源：抖音流量池 + 多路召回架构的公开机制
--  适配对象：GitHub 仓库推荐
-- ============================================================
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------
-- 1. 用户
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  username        TEXT    NOT NULL UNIQUE,
  password_hash   TEXT    NOT NULL,
  display_name    TEXT,
  avatar_url      TEXT,

  -- GitHub 授权（用于同步 star / 拉取用户自己的兴趣仓库）
  github_login    TEXT,
  github_token    TEXT,          -- ⚠️ 生产环境必须加密存储

  -- 账号权重（对应抖音"账号权重"概念）
  account_score   REAL    NOT NULL DEFAULT 1.0,
  is_new_user     INTEGER NOT NULL DEFAULT 1,   -- 冷启动标记

  created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
  last_active_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_active ON users(last_active_at DESC);


-- ------------------------------------------------------------
-- 2. 仓库（核心实体 = 抖音里的"视频"）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repos (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  github_id       INTEGER UNIQUE,               -- GitHub 的 repo id，用于去重
  owner           TEXT    NOT NULL,
  name            TEXT    NOT NULL,
  full_name       TEXT    NOT NULL UNIQUE,      -- owner/name
  description     TEXT,
  readme_md       TEXT,
  readme_len      INTEGER DEFAULT 0,
  homepage        TEXT,
  default_branch  TEXT    DEFAULT 'main',

  -- 内容标签（= 抖音的"内容标签"，用于召回匹配）
  language        TEXT,
  topics          TEXT,                          -- JSON 数组
  license_spdx    TEXT,
  license_risk    TEXT,                          -- safe / caution / risky / unknown

  -- 元数据
  stars           INTEGER NOT NULL DEFAULT 0,
  forks           INTEGER NOT NULL DEFAULT 0,
  watchers        INTEGER NOT NULL DEFAULT 0,
  open_issues     INTEGER NOT NULL DEFAULT 0,
  contributors    INTEGER NOT NULL DEFAULT 0,
  has_ci          INTEGER NOT NULL DEFAULT 0,
  has_tests       INTEGER NOT NULL DEFAULT 0,
  size_kb         INTEGER DEFAULT 0,

  -- GitHub 原生时间
  created_at_gh   TEXT,
  pushed_at_gh    TEXT,

  -- ===== 我们自己的评分（推流引擎的输入）=====
  quality_score   REAL    DEFAULT 0,   -- 质量分（CI/测试/文档/维护活跃度）
  velocity_score  REAL    DEFAULT 0,   -- Star 增速分（关键！不是总量）
  forgotten_score REAL    DEFAULT 0,   -- ⭐ 遗忘分：遗珠召回专用
  freshness_score REAL    DEFAULT 0,   -- 新鲜度分
  content_vec     BLOB,                -- 512 维内容向量（双塔召回用）

  -- 内容标签（TF-IDF 提取结果，JSON: {"tag": weight}）
  -- ⚠️ 必须落库缓存：jieba 提取一个 README 约 3~8ms，
  --    每次 Feed 请求都对几十个候选重新提取会让响应时间爆炸。
  --    召回/排序/搜索干预全都依赖这一列，缺了它整条链路是死的。
  tags_json       TEXT,
  tags_updated_at TEXT,

  -- 首次进入候选池时间 & 最近刷新
  first_seen_at   TEXT    NOT NULL DEFAULT (datetime('now')),
  last_refreshed_at TEXT,
  last_pushed_at  TEXT,
  is_archived     INTEGER NOT NULL DEFAULT 0,
  is_dead         INTEGER NOT NULL DEFAULT 0   -- 长期无 push
);
CREATE INDEX IF NOT EXISTS idx_repos_lang      ON repos(language);
CREATE INDEX IF NOT EXISTS idx_repos_velocity  ON repos(velocity_score DESC);
CREATE INDEX IF NOT EXISTS idx_repos_forgotten ON repos(forgotten_score DESC);
CREATE INDEX IF NOT EXISTS idx_repos_created   ON repos(created_at_gh DESC);
CREATE INDEX IF NOT EXISTS idx_repos_stars     ON repos(stars DESC);


-- ------------------------------------------------------------
-- 3. Star 历史快照 ⭐ 算 Star Velocity 的唯一途径
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repo_star_snapshots (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  stars         INTEGER NOT NULL,
  forks         INTEGER DEFAULT 0,
  captured_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_snap_repo_time ON repo_star_snapshots(repo_id, captured_at DESC);


-- ------------------------------------------------------------
-- 4. 用户行为事件 ⭐ 推流算法唯一的数据来源
--    = 抖音的"用户行为采集"
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  repo_id       INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,

  event_type    TEXT    NOT NULL,
  /*  曝光类：impression
      负反馈：skip
      轻互动：view / dwell_3s / dwell_10s / dwell_30s
      深度：  read_readme / open_files / open_issues / click_release
      表态：  like / unlike / comment / share / copy_link
      转化：  star_click / follow_owner
  */

  -- 行为价值权重（写入时由后端按 EVENT_WEIGHTS 赋值，便于事后调参回溯）
  weight        REAL    NOT NULL DEFAULT 0,
  value         REAL    DEFAULT 0,      -- 归一化行为强度 0~1

  -- 上下文的量化指标
  dwell_ms      INTEGER DEFAULT 0,      -- 停留时长
  scroll_depth  REAL    DEFAULT 0,      -- README 滚动深度 0~1  ← 我们的"完播率"
  is_clicked    INTEGER DEFAULT 0,      -- 曝光后是否点击

  -- 归因（抖音的"多路召回"要能追踪效果）
  source_channel TEXT,                  -- 这条是哪个召回通路来的
  feed_impression_id INTEGER,           -- 关联具体一次曝光
  rank_position  INTEGER,               -- 当时排在第几位

  created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ev_user_time ON user_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ev_repo_type ON user_events(repo_id, event_type);
CREATE INDEX IF NOT EXISTS idx_ev_channel   ON user_events(source_channel);
CREATE INDEX IF NOT EXISTS idx_ev_type_time ON user_events(event_type, created_at DESC);


-- ------------------------------------------------------------
-- 5. 曝光记录（重排去重 + 效果分析）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feed_impressions (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  repo_id      INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,

  rank_position INTEGER NOT NULL,
  final_score   REAL    NOT NULL,     -- 重排后的最终分
  raw_score     REAL,                 -- 精排原始分
  channel       TEXT,                 -- 来自哪路召回
  batch_id      TEXT,                 -- 同一次请求的批次

  shown_at      TEXT    NOT NULL DEFAULT (datetime('now')),
  clicked_at    TEXT,                 -- 点击时间（非空 = 点击了）
  dwell_ms      INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_imp_user_time ON feed_impressions(user_id, shown_at DESC);
CREATE INDEX IF NOT EXISTS idx_imp_repo      ON feed_impressions(repo_id);


-- ------------------------------------------------------------
-- 6. 流量池状态机 ⭐⭐ 本项目的灵魂：把"仓库"当"内容"赛马
--    抖音是视频赛马，我们让仓库也赛马
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS repo_pools (
  repo_id        INTEGER PRIMARY KEY REFERENCES repos(id) ON DELETE CASCADE,

  current_pool   INTEGER NOT NULL DEFAULT 0,
  /*  0 = 冷启动池 (cold)
      1 = 初级池
      2 = 中级池
      3 = 高级池
      4 = 爆款池
      -1 = 已淘汰
  */

  -- 本池内已消耗的曝光数 / 本池配额
  exposures_used INTEGER NOT NULL DEFAULT 0,
  exposure_quota INTEGER NOT NULL DEFAULT 300,

  -- 本池内的表现指标（用于晋级判定）
  impressions    INTEGER NOT NULL DEFAULT 0,
  clicks         INTEGER NOT NULL DEFAULT 0,
  deep_reads     INTEGER NOT NULL DEFAULT 0,   -- 深读次数（≈完播）
  likes          INTEGER NOT NULL DEFAULT 0,
  stars_gained   INTEGER NOT NULL DEFAULT 0,   -- 获得 star（≈收藏，权重最高）
  comments       INTEGER NOT NULL DEFAULT 0,
  shares         INTEGER NOT NULL DEFAULT 0,
  dwell_sum_ms   INTEGER NOT NULL DEFAULT 0,

  -- 计算出的率值
  ctr            REAL NOT NULL DEFAULT 0,   -- 点击率
  deep_rate      REAL NOT NULL DEFAULT 0,   -- 深读率 ≈ 完播率
  star_rate      REAL NOT NULL DEFAULT 0,   -- 收藏率

  entered_pool_at TEXT   NOT NULL DEFAULT (datetime('now')),
  promoted_at     TEXT,
  demoted_at      TEXT,
  updated_at      TEXT   NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pool_level ON repo_pools(current_pool, exposure_quota DESC);
CREATE INDEX IF NOT EXISTS idx_pool_enter ON repo_pools(entered_pool_at);


-- ------------------------------------------------------------
-- 7. 用户兴趣画像（定期重算，对应"用户标签向量"）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_profiles (
  user_id          INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

  top_languages    TEXT,     -- JSON: [{"lang":"Python","score":0.82}, ...]
  top_topics       TEXT,     -- JSON: [{"topic":"llm","score":0.71}, ...]
  affinity_owners  TEXT,     -- JSON: [{"owner":"vercel","score":0.9}, ...]
  vec              BLOB,     -- 用户兴趣向量（与 content_vec 做内积）

  interest_count   INTEGER DEFAULT 0,   -- 已积累的兴趣数（判断冷启动是否结束）
  updated_at       TEXT NOT NULL DEFAULT (datetime('now'))
);


-- ------------------------------------------------------------
-- 8. 关联互动（用户明确要求的：点赞/收藏/评论）
-- ------------------------------------------------------------
-- 点赞 = 网页端记录
CREATE TABLE IF NOT EXISTS likes (
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  repo_id    INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, repo_id)
);

-- 收藏 = 同步 GitHub Star（用户明确定义）
CREATE TABLE IF NOT EXISTS stars (
  user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  repo_id      INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  gh_starred   INTEGER NOT NULL DEFAULT 0,   -- 是否真的在 GitHub 上点了星
  synced_at    TEXT,
  created_at   TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, repo_id)
);

-- 评论 = 对应 GitHub Discussion / Issue
CREATE TABLE IF NOT EXISTS comments (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id           INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  user_id           INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  body              TEXT    NOT NULL,
  body_len          INTEGER NOT NULL DEFAULT 0,   -- 评论深度（2026算法看重）
  gh_discussion_id  TEXT,
  gh_comment_id     TEXT,
  sync_status       TEXT DEFAULT 'pending',       -- pending/synced/failed
  created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_comment_repo ON comments(repo_id, created_at DESC);

-- 关注作者（最强正反馈）
CREATE TABLE IF NOT EXISTS follows (
  user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  owner      TEXT    NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (user_id, owner)
);


-- ------------------------------------------------------------
-- 9. 召回通道效果统计（用于动态调各路的配额）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS channel_stats (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  channel       TEXT NOT NULL,
  stat_date     TEXT NOT NULL,
  impressions   INTEGER DEFAULT 0,
  clicks        INTEGER DEFAULT 0,
  deep_reads    INTEGER DEFAULT 0,
  stars_gained  INTEGER DEFAULT 0,
  ctr           REAL DEFAULT 0,
  deep_rate     REAL DEFAULT 0,
  UNIQUE (channel, stat_date)
);


-- ------------------------------------------------------------
-- 10. 算法参数表（不改代码就能调权重，方便你边跑边调）
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS algo_config (
  key         TEXT PRIMARY KEY,
  value       TEXT NOT NULL,
  description TEXT,
  updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO algo_config (key, value, description) VALUES
  ('w_deep_read',   '0.35', '深读率权重（≈完播率，2026已下调）'),
  ('w_star',        '0.25', '收藏/star 率权重（2026最高）'),
  ('w_comment',     '0.12', '评论深度权重'),
  ('w_share',       '0.10', '分享权重'),
  ('w_like',        '0.08', '点赞权重（2026已降权）'),
  ('w_follow',      '0.10', '关注作者权重'),
  ('recency_decay', '0.08', '全局时效衰减系数'),
  ('velocity_boost', '1.5', 'Star 增速加成系数'),
  ('forgotten_boost','1.8', '遗珠加成系数 ⭐ 本项目特色'),
  ('explore_rate',  '0.15', '探索位占比'),
  ('dedup_window',  '200',  '已曝光去重窗口（条）'),
  ('pool_promote_deep_rate', '0.30', '晋级所需深读率阈值'),
  ('pool_promote_star_rate', '0.03', '晋级所需 star 率阈值'),
  ('pool_demote_deep_rate',  '0.08', '淘汰的深读率下限'),
  ('long_tail_days', '7',   '长尾评估周期（2026新规：7天）');


-- ------------------------------------------------------------
-- 11. 用户待刷队列（缓存池）⭐
-- ------------------------------------------------------------
-- 设计动机：
--   原来的做法是"实时跑完整推荐管线"，问题有三：
--     1. feed_impressions 只增不减，池子无限膨胀
--     2. 用户刷完一轮就没有新内容，只能靠去重降级重复推老仓库
--     3. 每次请求都要跑 7 路召回 + 打分 + 重排，浪费算力
--
--   改成"缓存池"模式：
--     爬虫按用户画像定向灌入队列 → 用户从队列取 → 取走即删除
--     → 队列见底（剩 6 条）时触发补货
--
--   ⚠️ 删除的是【队列项】，不是仓库本身。
--      仓库、标签、流量池统计全部保留，其他用户仍可刷到，
--      算法学习也不受影响。
CREATE TABLE IF NOT EXISTS feed_queue (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  repo_id     INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  position    INTEGER NOT NULL DEFAULT 0,   -- 队列内顺序（小的先出）
  score       REAL    NOT NULL DEFAULT 0,   -- 入队时的排序分
  channel     TEXT,                          -- 来源通路（便于分析）
  reason      TEXT,                          -- 推荐理由（前端直接用）
  enqueued_at TEXT    NOT NULL DEFAULT (datetime('now')),
  batch_id    TEXT,                          -- 哪一批补货进来的
  UNIQUE (user_id, repo_id)
);
CREATE INDEX IF NOT EXISTS idx_queue_user_pos ON feed_queue(user_id, position);
CREATE INDEX IF NOT EXISTS idx_queue_user_time ON feed_queue(user_id, enqueued_at DESC);


-- ------------------------------------------------------------
-- 12. 补货日志（记录每次爬虫按什么方向补的）
-- ------------------------------------------------------------
-- 用途：便于回答"为什么推给我这个"以及调参。
-- 例如发现"按 llm 标签补的货深读率 0.4，按 python 补的只有 0.05"，
-- 就能反过来优化画像权重。
CREATE TABLE IF NOT EXISTS refill_log (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  trigger       TEXT NOT NULL,          -- low_watermark / manual / initial
  queue_before  INTEGER NOT NULL DEFAULT 0,
  fetched       INTEGER NOT NULL DEFAULT 0,   -- 爬虫实际抓到几条
  enqueued      INTEGER NOT NULL DEFAULT 0,   -- 实际入队几条
  strategy      TEXT,                         -- JSON：本次抓取用的查询词/标签
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_refill_user_time ON refill_log(user_id, created_at DESC);
