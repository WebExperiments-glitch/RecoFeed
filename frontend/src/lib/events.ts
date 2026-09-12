/**
 * 行为事件上报。
 *
 * ⭐ 这是推荐系统能「越用越准」的唯一输入源 —— 前端埋点质量直接决定画像质量。
 *
 * 关键设计：
 *   ① 批量合并：滑动很快会产生大量事件，逐个 POST 会把后端打满。
 *      这里攒 300ms 或满 12 条就发一次。
 *   ② 深读判定在前端做：抖音的「完播率」对应这里的 scroll_depth + dwell_ms。
 *      用户滑走时才判定，避免刚曝光就报 deep_read。
 *   ③ 页面隐藏时立即 flush：否则用户切走会丢最后一批。
 */
import type { EventPayload, EventType } from '@/types/api'

const BASE = '/api'
const FLUSH_INTERVAL_MS = 300
const FLUSH_BATCH_SIZE = 12

let buffer: EventPayload[] = []
let timer: number | null = null
let userId = 1

export function setEventUser(uid: number): void {
  userId = uid
}

function schedule(): void {
  if (timer !== null) return
  timer = window.setTimeout(() => {
    timer = null
    void flush()
  }, FLUSH_INTERVAL_MS)
}

export function track(payload: EventPayload): void {
  buffer.push(payload)
  if (buffer.length >= FLUSH_BATCH_SIZE) {
    if (timer !== null) {
      window.clearTimeout(timer)
      timer = null
    }
    void flush()
  } else {
    schedule()
  }
}

export async function flush(): Promise<void> {
  if (buffer.length === 0) return
  const batch = buffer
  buffer = []
  try {
    await fetch(`${BASE}/events`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // keepalive 让浏览器在页面卸载时也能把请求发出去
      keepalive: true,
      body: JSON.stringify({ user_id: userId, events: batch }),
    })
  } catch {
    // 上报失败不重试、不报错 —— 埋点不能影响用户滑动。
    // 丢几条事件对画像的影响远小于弹一个错误提示。
  }
}

// 页面隐藏 / 卸载时把残余事件发出去
if (typeof window !== 'undefined') {
  window.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') void flush()
  })
  window.addEventListener('pagehide', () => void flush())
}

// ------------------------------------------------------------------ 语义化封装

/** 卡片进入视口并停留超过阈值 → 记一次曝光 */
export function trackImpression(
  repoId: number,
  rankPosition: number,
  sourceChannel?: string,
  batchId?: string,
): void {
  track({
    repo_id: repoId,
    event_type: 'impression',
    source_channel: sourceChannel,
    batch_id: batchId,
    rank_position: rankPosition,
  })
}

/**
 * 卡片离开视口 → 结算这次浏览。
 *
 * 深读判定（对齐后端 READ_WPM 的口径）：
 *   停留 ≥ MIN_DEEP_MS 且 滚动深度 ≥ MIN_DEEP_DEPTH → deep_read
 *   否则 → skip
 *
 * ⚠️ 用「划过」而不是「什么都没发生」来记录，
 *    因为后端的补货逻辑需要区分"没看过"和"看了不感兴趣"。
 *    但 skip 只做 7 天冷却，不会永久排除（见 feed/refill.py）。
 */
export const MIN_DEEP_MS = 4000
export const MIN_DEEP_DEPTH = 0.35

export function trackSwipe(
  repoId: number,
  dwellMs: number,
  scrollDepth: number,
  rankPosition: number,
  sourceChannel?: string,
  batchId?: string,
): void {
  const isDeep =
    dwellMs >= MIN_DEEP_MS && scrollDepth >= MIN_DEEP_DEPTH
  const eventType: EventType = isDeep ? 'deep_read' : 'skip'
  track({
    repo_id: repoId,
    event_type: eventType,
    dwell_ms: dwellMs,
    scroll_depth: Number(scrollDepth.toFixed(3)),
    source_channel: sourceChannel,
    batch_id: batchId,
    rank_position: rankPosition,
  })
}

export function trackLike(repoId: number, liked: boolean): void {
  track({ repo_id: repoId, event_type: liked ? 'like' : 'skip' })
}

export function trackStar(repoId: number): void {
  track({ repo_id: repoId, event_type: 'star_click' })
}
