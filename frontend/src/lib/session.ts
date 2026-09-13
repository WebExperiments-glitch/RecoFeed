/**
 * 登录会话（前端侧）。
 *
 * 设计：
 *   · 令牌存 localStorage，所有 API 请求由 lib/api.ts 自动带上 Authorization
 *   · 登录回调把令牌放在 URL 上（?auth_token=xxx），这里消费后立刻从地址栏清掉
 *   · 支持"游客模式"：不登录也能刷（对应的后端用户是 demo/1）
 */

const TOKEN_KEY = 'recofeed_token'
const USER_KEY = 'recofeed_user'
const GUEST_KEY = 'recofeed_guest'

export interface SessionUser {
  user_id: number
  username: string
  github_login: string | null
  display_name: string | null
  avatar_url: string | null
}

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(TOKEN_KEY, token)
    localStorage.removeItem(GUEST_KEY)
  } catch {
    /* 隐私模式下 localStorage 可能不可用，忽略 */
  }
}

export function clearToken(): void {
  try {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(USER_KEY)
  } catch {
    /* ignore */
  }
}

export function getCachedUser(): SessionUser | null {
  try {
    const raw = localStorage.getItem(USER_KEY)
    return raw ? (JSON.parse(raw) as SessionUser) : null
  } catch {
    return null
  }
}

export function setCachedUser(u: SessionUser | null): void {
  try {
    if (u) localStorage.setItem(USER_KEY, JSON.stringify(u))
    else localStorage.removeItem(USER_KEY)
  } catch {
    /* ignore */
  }
}

export function isGuest(): boolean {
  try {
    return localStorage.getItem(GUEST_KEY) === '1' && !getToken()
  } catch {
    return false
  }
}

export function setGuest(v: boolean): void {
  try {
    if (v) localStorage.setItem(GUEST_KEY, '1')
    else localStorage.removeItem(GUEST_KEY)
  } catch {
    /* ignore */
  }
  if (v) clearToken()
}

/**
 * 消费 OAuth 回调参数：/&auth_token=...&auth_sync=1（或 auth_error=...）。
 * 读完立刻从地址栏抹掉，避免令牌留在 URL 里被复制外泄。
 */
export function consumeAuthFromUrl(): {
  token?: string
  error?: string
  needSync: boolean
} {
  try {
    const url = new URL(window.location.href)
    const token = url.searchParams.get('auth_token') ?? undefined
    const error = url.searchParams.get('auth_error') ?? undefined
    const needSync = url.searchParams.get('auth_sync') === '1'
    if (token || error || url.searchParams.has('auth_sync')) {
      url.searchParams.delete('auth_token')
      url.searchParams.delete('auth_error')
      url.searchParams.delete('auth_sync')
      window.history.replaceState({}, '', url.toString())
    }
    return { token, error, needSync }
  } catch {
    return { needSync: false }
  }
}
