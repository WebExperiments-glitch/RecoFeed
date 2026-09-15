/**
 * 后端 API 契约类型。
 *
 * ⚠️ 这里的字段名必须与 backend/api/routes.py 的返回体逐字一致。
 *    改后端字段时要同步改这里，否则前端只会在运行时静默变成 undefined。
 */

// ------------------------------------------------------------------ Feed

export interface RepoTag {
  tag: string
  weight: number
}

/** Feed 卡片（/api/feed 的 items[] 元素） */
export interface FeedItem {
  repo_id: number
  full_name: string
  owner: string
  name: string
  description: string | null
  /** README 纯文本摘要（后端按 markdown 清洗过，约 420 字）——用于填充卡片版面 */
  readme_excerpt?: string
  /** 个人模型打分（0~1）；有它时「匹配度」展示这个，而不是结构化质量分 */
  personal_score?: number
  language: string | null
  stars: number
  topics: string[]
  tags: RepoTag[]
  license_spdx: string | null
  license_risk: 'safe' | 'caution' | 'danger' | null
  /** 推荐分（0~1 量级） */
  score: number
  /** 召回通路，如 ["refill"] */
  channels: string[]
  /** 推荐理由文案（后端已生成好，前端直接展示） */
  reason: string
  /** 遗珠分（≥0.7 前端打「💎 遗珠」标记） */
  forgotten_score: number
  /** 最近推送时间，用于展示"更新于 N 天前" */
  pushed_at_gh: string | null
  created_at_gh: string | null
  url: string
}

export interface RefillPlan {
  queries: string[]
  languages: string[]
  labels: string[]
  domains: string[]
  per_query: number
  prefer_forgotten: boolean
  explore_ratio: number
}

export interface RefillInfo {
  refilled: boolean
  success?: boolean
  reason?: string
  queue_before?: number
  queue_after?: number
  fetched?: number
  enqueued?: number
  min_gain?: number
  batch_id?: string
  partial?: boolean
  note?: string
}

export interface FeedMeta {
  /** "queue" = 来自缓存池；"queue+realtime" = 补货没跟上、实时兜底 */
  source: 'queue' | 'queue+realtime' | 'realtime_search'
  popped?: number
  /** 队列剩余条数 */
  queue_size: number
  /** 是否已到低水位，需要补货 */
  needs_refill: boolean
  refill?: RefillInfo
  refill_plan?: RefillPlan
  realtime?: number
}

export interface FeedResponse {
  items: FeedItem[]
  meta: FeedMeta
}

// ------------------------------------------------------------------ 详情

/** 仓库详情（/api/repos/{id}）—— 比 FeedItem 多出流量池与统计字段 */
export interface RepoDetail {
  id: number
  owner: string
  name: string
  full_name: string
  description: string | null
  /** 仅当 with_readme=true 时下发 */
  readme_md?: string | null
  readme_len: number | null
  homepage: string | null
  default_branch: string | null
  language: string | null
  topics: string[]
  license_spdx: string | null
  license_risk: 'safe' | 'caution' | 'danger' | null
  stars: number
  forks: number
  watchers: number
  open_issues: number
  contributors: number | null
  has_ci: number
  has_tests: number
  size_kb: number | null
  created_at_gh: string | null
  pushed_at_gh: string | null
  quality_score: number
  velocity_score: number
  forgotten_score: number
  freshness_score: number
  tags_json: string | null
  tags_updated_at: string | null
  is_archived: number
  is_dead: number
  /** 流量池等级：0 cold / 1 basic / 2 mid / 3 high / 4 viral / -1 淘汰 */
  current_pool: number | null
  impressions: number | null
  deep_rate: number | null
  star_rate: number | null
  ctr: number | null
  liked: boolean
  starred: boolean
  url: string
}

// ------------------------------------------------------------------ 搜索

export interface SearchResultItem extends FeedItem {
  rank: number
  search_reasons: string[]
  forgotten_score: number
  quality_score: number
}

export interface SearchResponse {
  items: SearchResultItem[]
  meta: {
    query: string
    terms: string[]
    total: number
    limit: number
    offset: number
    has_more: boolean
    sort: string
    filters: { language: string | null; min_stars: number | null }
  }
}

export interface SuggestResponse {
  items: string[]
  meta: { query: string; count: number }
}

export interface HotResponse {
  /** ⚠️ 后端返回的是 hot/word/count，不是 items/word/n —— 以实际契约为准 */
  hot: { word: string; count: number }[]
}

// ------------------------------------------------------------------ 队列

export interface QueueStats {
  size: number
  low_watermark: number
  target_size: number
  needs_refill: boolean
  by_channel: { channel: string; n: number }[]
  recent_refills: {
    trigger: string
    queue_before: number
    fetched: number
    enqueued: number
    strategy: RefillPlan | null
    created_at: string
  }[]
}

// ------------------------------------------------------------------ 画像

export interface ProfileItem {
  tag: string
  weight: number
}

/**
 * 用户画像（/api/user/profile）。
 *
 * ⚠️ 后端已经把 JSON 字符串解析成数组了，前端不要再 JSON.parse 一次。
 *    曾经按 {top_topics: string} 写类型，实际拿到的是 interests 数组，
 *    结果画像面板整页渲染不出内容。
 */
export interface UserProfile {
  user_id: number
  /** 兴趣标签，weight 从大到小 */
  interests: ProfileItem[]
  /** 语言偏好。后端给的是 [{lang, n}] 或纯字符串数组，两种都兜住 */
  languages: (string | { lang: string; n?: number })[]
  /** 行为事件总数 */
  events: number
  /** 曝光过的仓库数 */
  seen: number
  /** 画像数据是否还不足以形成有效推荐 */
  cold_start: boolean
  updated_at?: string
}

// ------------------------------------------------------------------ 自定义关键词 & 爬虫

/** 用户自定义兴趣关键词（/api/user/keywords） */
export interface CustomKeyword {
  id: number
  keyword: string
  weight: number
  created_at: string
}

export interface KeywordsResponse {
  keywords: CustomKeyword[]
}

export interface CustomKeywordUpsert extends CustomKeyword {
  /** false = 关键词已存在（幂等添加） */
  inserted: boolean
}

/** 爬虫抓取结果（POST /api/queue/crawl） */
export interface CrawlResult {
  ok: boolean
  keywords: string[]
  found: number
  new_repos: number
  enqueued: number
  details: { keyword: string; found: number; kept: number }[]
}

// ------------------------------------------------------------------ GitHub 登录 & 实证圈

export interface SessionUser {
  user_id: number
  username: string
  github_login: string | null
  display_name: string | null
  avatar_url: string | null
}

/** 单条实证记录（自建仓库 / 点星仓库） */
export interface FootprintItem {
  full_name: string
  weight: number
  /** 点星榜排名（starred 才有） */
  rank?: number | null
  /** 该仓库的星数 */
  stars?: number
  /** 自建仓库最近推送时间（owned 才有） */
  pushed_at?: string | null
}

export interface FootprintSummary {
  owned_count: number
  starred_count: number
  /** 强信号放大系数（"猛推"开关） */
  boost: number
  owned: FootprintItem[]
  starred: FootprintItem[]
}

export interface MeResponse extends SessionUser {
  footprint: FootprintSummary
}

export interface AuthStatus {
  /** OAuth 是否已配置（没配置就只用令牌登录） */
  configured: boolean
  callback_url: string
}

export interface SyncResult {
  owned: number
  starred: number
  owned_top: FootprintItem[]
  starred_top: FootprintItem[]
  footprint: FootprintSummary
}

// ------------------------------------------------------------------ LLM 洞察层

/** 推荐理由批量结果（一页卡片合并 1 次 LLM 调用） */
export interface ExplainResponse {
  ok: boolean
  /** repo_id → 一句话推荐理由 */
  explanations: Record<string, string>
  cached?: number
  generated?: number
  model?: string
  reason?: string
}

/** LLM 画像归纳结果 */
export interface ProfileInsight {
  ok: boolean
  /** 一句话技术画像 */
  summary: string
  /** LLM 提炼的精确技术方向 */
  interests: string[]
  /** LLM 判定为噪声的标签 */
  noise: string[]
  cached?: boolean
  model?: string
  created_at?: string
  reason?: string
}

// ------------------------------------------------------------------ 事件

export type EventType =
  | 'impression'
  | 'click'
  | 'deep_read'
  | 'skip'
  | 'like'
  | 'dislike'
  | 'star_click'
  | 'scroll'

export interface EventPayload {
  repo_id: number
  event_type: EventType
  dwell_ms?: number
  scroll_depth?: number
  source_channel?: string
  batch_id?: string
  rank_position?: number
}

// ------------------------------------------------------------------ 翻译

/** 仓库中文翻译（POST /api/repos/{id}/translate） */
export interface TranslateResponse {
  repo_id: number
  /** 缓存命中（不消耗 API 额度） */
  cached: boolean
  /** true = 内容本身是中文/无内容，无需翻译 */
  skipped?: boolean
  reason?: string
  zh_description: string | null
  zh_readme: string | null
  /** 实际使用的模型 */
  model: string | null
  /** README 超长被截断（只翻了开头部分） */
  readme_truncated?: boolean
  shared?: boolean
}

/** 翻译服务状态（GET /api/translate/status） */
export interface TranslateStatus {
  configured: boolean
  models: string[]
  used_last_minute: number
  limit_per_minute: number
  used_today: number
  limit_per_day: number
  remaining_today: number
  cache_entries: number
  model_cooldowns: { model: string; cooldown_left: number }[]
  global_cooldown_left: number
}

/** 单张卡片的中文翻译（标题短译 + 描述译文），null 表示暂不可用 */
export interface CardTranslation {
  zh_name: string | null
  zh_description: string | null
}

/** 批量描述翻译（POST /api/translate/batch）—— feed 卡片中文 */
export interface BatchTranslateResponse {
  /** key 是 repo_id 字符串 */
  translations: Record<string, CardTranslation | null>
}

// ------------------------------------------------------------------ 通用

export interface ApiError {
  detail: string
}
