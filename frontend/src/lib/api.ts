/**
 * 后端 API 客户端。
 *
 * 设计原则：
 *   ① 所有请求走同一个 request()，统一错误处理与超时
 *   ② 事件上报单独一批（见 events.ts），因为它需要批量/去重
 *   ③ 不引入 axios —— fetch 够用，且少一个依赖
 */
import type {
  FeedResponse,
  HotResponse,
  QueueStats,
  RepoDetail,
  SearchResponse,
  SuggestResponse,
  UserProfile,
} from '@/types/api'

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
    const res = await fetch(`${BASE}${path}`, {
      ...init,
      signal: ctrl.signal,
      headers: {
        'Content-Type': 'application/json',
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
): Promise<FeedResponse> {
  const p = new URLSearchParams({
    user_id: String(userId),
    limit: String(limit),
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
