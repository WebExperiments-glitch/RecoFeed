/**
 * 后端 API 客户端。
 *
 * 设计原则：
 *   ① 所有请求走同一个 request()，统一错误处理与超时
 *   ② 事件上报单独一批（见 events.ts），因为它需要批量/去重
 *   ③ 不引入 axios —— fetch 够用，且少一个依赖
 */
import type {
  AuthStatus,
  BatchTranslateResponse,
  CrawlResult,
  CustomKeywordUpsert,
  ExplainResponse,
  FeedMode,
  FeedResponse,
  HotResponse,
  KeywordsResponse,
  MeResponse,
  ProfileInsight,
  QueueStats,
  RepoDetail,
  SearchResponse,
  SessionUser,
  SuggestResponse,
  SyncResult,
  TranslateResponse,
  TranslateStatus,
  UserProfile,
} from '@/types/api'
import { getToken } from '@/lib/session'

const BASE = '/api'
const DEFAULT_TIMEOUT = 20_000

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  timeout = DEFAULT_TIMEOUT,
): Promise<T> {
  const ctrl = new AbortController()
  const timer = setTimeout(() => ctrl.abort(), timeout)
  try {
    // 登录令牌自动带上（lib/session 管理）
    const token = getToken()
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        ...(init.headers ?? {}),
      },
    })
    if (!res.ok) {
      // 后端用 FastAPI，错误体形如 {"detail": "..."}
      let detail = `HTTP ${res.status}`
      try {
        const body = (await res.json()) as { detail?: unknown }
        if (typeof body.detail === 'string') detail = body.detail
      } catch {
        /* 响应不是 JSON，保留状态码文案 */
      }
      throw new ApiError(res.status, detail)
    }
    return (await res.json()) as T
  } catch (err) {
    if (err instanceof ApiError) throw err
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiError(0, '请求超时，后端可能未启动')
    }
    throw new ApiError(0, '网络异常，请检查后端服务是否运行')
  } finally {
    clearTimeout(timer)
  }
}

// ------------------------------------------------------------------ Feed

export function getFeed(
  userId: number,
  limit = 10,
  searchQuery?: string,
  /** all=全部 / hot=热门（≥1000 星）/ gem=遗珠（<1000 星） */
  mode: FeedMode = 'all',
): Promise<FeedResponse> {
  const p = new URLSearchParams({
    user_id: String(userId),
    limit: String(limit),
    mode,
  })
  if (searchQuery) p.set('search_query', searchQuery)
  return request<FeedResponse>(`/feed?${p}`)
}

export function getRepo(
  repoId: number,
  userId: number,
  withReadme = true,
): Promise<RepoDetail> {
  const p = new URLSearchParams({
    user_id: String(userId),
    with_readme: String(withReadme),
  })
  return request<RepoDetail>(`/repos/${repoId}?${p}`)
}

// ------------------------------------------------------------------ 队列

export function getQueueStats(userId: number): Promise<QueueStats> {
  return request<QueueStats>(`/stats/queue?user_id=${userId}`)
}

export function refillQueue(userId: number, target?: number): Promise<unknown> {
  return request('/queue/refill', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, target }),
  })
}

export function rebuildQueue(userId: number): Promise<unknown> {
  return request(`/queue/rebuild?user_id=${userId}`, { method: 'POST' })
}

// ------------------------------------------------------------------ 搜索

export interface SearchParams {
  q: string
  userId?: number
  limit?: number
  offset?: number
  language?: string
  minStars?: number
  sort?: 'relevance' | 'stars' | 'recent' | 'forgotten'
}

export function searchRepos(params: SearchParams): Promise<SearchResponse> {
  const p = new URLSearchParams({ q: params.q })
  if (params.userId !== undefined) p.set('user_id', String(params.userId))
  if (params.limit !== undefined) p.set('limit', String(params.limit))
  if (params.offset !== undefined) p.set('offset', String(params.offset))
  if (params.language) p.set('language', params.language)
  if (params.minStars !== undefined) p.set('min_stars', String(params.minStars))
  if (params.sort) p.set('sort', params.sort)
  return request<SearchResponse>(`/search?${p}`)
}

export function suggestKeywords(q: string): Promise<SuggestResponse> {
  return request<SuggestResponse>(`/search/suggest?q=${encodeURIComponent(q)}`)
}

export function hotKeywords(): Promise<HotResponse> {
  return request<HotResponse>('/search/hot')
}

// ------------------------------------------------------------------ 用户

export function getUserProfile(userId: number): Promise<UserProfile> {
  return request<UserProfile>(`/user/profile?user_id=${userId}`)
}

export function rebuildUserProfile(userId: number): Promise<unknown> {
  return request(`/user/profile/rebuild?user_id=${userId}`, { method: 'POST' })
}

// ------------------------------------------------------------------ 自定义关键词 & 爬虫

export function getCustomKeywords(userId: number): Promise<KeywordsResponse> {
  return request(`/user/keywords?user_id=${userId}`)
}

export function addCustomKeyword(
  userId: number,
  keyword: string,
): Promise<CustomKeywordUpsert> {
  return request('/user/keywords', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, keyword }),
  })
}

export function deleteCustomKeyword(
  id: number,
  userId: number,
): Promise<{ ok: boolean }> {
  return request(`/user/keywords/${id}?user_id=${userId}`, { method: 'DELETE' })
}

/**
 * 驱动 Scrapling 爬虫按关键词抓 GitHub 真实仓库 → 入库 → 直接入队。
 * keywords 留空 = 使用画像面板保存的自定义关键词。
 * 抓取 + 入库在前台完成（README 后台补齐），超时给足 90s。
 */
export function crawlByKeywords(
  userId: number,
  keywords?: string[],
): Promise<CrawlResult> {
  return request(
    '/queue/crawl',
    {
      method: 'POST',
      body: JSON.stringify({ user_id: userId, keywords: keywords ?? [] }),
    },
    90_000,
  )
}

// ------------------------------------------------------------------ 互动

export function likeRepo(repoId: number, userId: number): Promise<unknown> {
  return request(`/repos/${repoId}/like`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  })
}

export function unlikeRepo(repoId: number, userId: number): Promise<unknown> {
  return request(`/repos/${repoId}/like?user_id=${userId}`, {
    method: 'DELETE',
  })
}

export function starRepo(repoId: number, userId: number): Promise<unknown> {
  return request(`/repos/${repoId}/star`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId }),
  })
}

export function dislikeRepo(
  repoId: number,
  userId: number,
  reason: 'repo' | 'category' = 'category',
): Promise<unknown> {
  return request(`/repos/${repoId}/dislike`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, reason }),
  })
}

export function getComments(repoId: number): Promise<unknown> {
  return request(`/repos/${repoId}/comments`)
}

// ------------------------------------------------------------------ 翻译

/**
 * 翻译仓库简介 + README 为中文。
 * ⚠️ LLM 调用偏慢（免费档推理 10~40s），超时给到 100s，
 *    且缓存命中时是瞬时返回，不影响日常体验。
 */
export function translateRepo(repoId: number): Promise<TranslateResponse> {
  return request<TranslateResponse>(
    `/repos/${repoId}/translate`,
    { method: 'POST' },
    100_000,
  )
}

export function getTranslateStatus(): Promise<TranslateStatus> {
  return request<TranslateStatus>('/translate/status')
}

/**
 * 批量拉取 feed 卡片描述的中文译文。
 * 整批合并 1 次 LLM 调用；预热命中时秒回。超时给足 100s（未预热时需现场翻译）。
 */
export function translateBatch(
  repoIds: number[],
): Promise<BatchTranslateResponse> {
  return request<BatchTranslateResponse>(
    '/translate/batch',
    { method: 'POST', body: JSON.stringify({ repo_ids: repoIds }) },
    100_000,
  )
}

export function postComment(
  repoId: number,
  userId: number,
  body: string,
): Promise<unknown> {
  return request(`/repos/${repoId}/comments`, {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, body }),
  })
}

// ------------------------------------------------------------------ GitHub 登录 & 实证圈

export function getAuthStatus(): Promise<AuthStatus> {
  return request<AuthStatus>('/auth/status')
}

/**
 * OAuth 登录入口：**直连后端**（不走 Vite 代理），
 * 这样 redirect_uri 恒为 http://127.0.0.1:8000/api/auth/github/callback，
 * 与 GitHub OAuth App 里登记的地址严格一致，避免 redirect_uri_mismatch。
 */
const BACKEND_ORIGIN =
  (import.meta.env.VITE_BACKEND_ORIGIN as string | undefined) ??
  'http://127.0.0.1:8000'

export function githubLoginUrl(): string {
  return `${BACKEND_ORIGIN}${BASE}/auth/github/login`
}

/** 令牌登录（不用建 OAuth App；PAT 需含 read:user + public_repo 权限）。 */
export function loginWithPat(
  token: string,
): Promise<{ token: string; user: SessionUser }> {
  return request('/auth/github/pat', {
    method: 'POST',
    body: JSON.stringify({ token }),
  }, 40_000)
}

export function getMe(): Promise<MeResponse> {
  return request<MeResponse>('/auth/me')
}

/** 同步用户的自建仓库 + 点星仓库（权重按规则计算）并重建画像。 */
export function syncGithub(): Promise<SyncResult> {
  return request<SyncResult>('/auth/sync', { method: 'POST' }, 90_000)
}

export function logout(): Promise<unknown> {
  return request('/auth/logout', { method: 'POST' })
}

// ------------------------------------------------------------------ LLM 洞察层

/** 批量生成推荐理由（一页 10 张合并 1 次 LLM 调用，结果按画像指纹缓存）。 */
export function explainCards(
  userId: number,
  repoIds: number[],
): Promise<ExplainResponse> {
  return request('/ml/explain', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, repo_ids: repoIds }),
  }, 120_000)
}

/** 读取已缓存的 LLM 画像归纳（画像变了会返回 null）。 */
export function getProfileSummary(
  userId: number,
): Promise<{ insight: ProfileInsight | null }> {
  return request<{ insight: ProfileInsight | null }>(
    `/user/profile/summary?user_id=${userId}`,
  )
}

/** 让 LLM 归纳画像（1 次调用；force=true 忽略缓存重算）。 */
export function summarizeProfile(
  userId: number,
  force = false,
): Promise<ProfileInsight> {
  return request<ProfileInsight>(
    `/user/profile/summarize?user_id=${userId}&force=${force ? 'true' : 'false'}`,
    { method: 'POST' },
    150_000,
  )
}
