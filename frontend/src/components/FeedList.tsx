import { useCallback, useEffect, useRef, useState } from 'react'
import type { FC } from 'react'
import type { CardTranslation, FeedItem, FeedMeta } from '@/types/api'
import FeedCard from '@/components/FeedCard'
import CardSkeleton from '@/components/CardSkeleton'
import { trackImpression } from '@/lib/events'
import { translateBatch } from '@/lib/api'
import { explainCards } from '@/lib/api'

interface Props {
  items: FeedItem[]
  meta: FeedMeta | null
  loading: boolean
  userId: number
  onOpenDetail: (item: FeedItem) => void
  onDislike: (item: FeedItem) => void
  /** 滑到接近底部时触发加载下一页 */
  onReachEnd: () => void
  /** 当前正在看的卡片发生变化（桌面端右侧信息面板用） */
  onActiveChange?: (item: FeedItem | null) => void
}

/**
 * Feed 主容器：竖向吸附滚动。
 *
 * 关键实现点：
 *   ① scroll-snap 做「一次一张」的整屏吸附（CSS，不用 JS 计算，性能好）
 *   ② IntersectionObserver 判定哪张卡是 active —— 它同时驱动
 *      埋点计时和卡片高亮，比监听 scroll 事件便宜得多
 *   ③ 接近底部时预加载，避免用户滑到底看到「加载中」
 */
const FeedList: FC<Props> = ({
  items,
  meta,
  loading,
  userId,
  onOpenDetail,
  onDislike,
  onReachEnd,
  onActiveChange,
}) => {
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const cardEls = useRef<Map<number, HTMLDivElement>>(new Map())
  const [activeIndex, setActiveIndex] = useState(0)
  const impressedRef = useRef<Set<number>>(new Set())
  const endedRef = useRef(false)

  // ── 卡片中文翻译（repo_id → {zh_name, zh_description}）──
  // feed 到手后批量拉取：整页合并 1 次 LLM 调用；
  // 后端在 feed 返回时已在后台预热，这里大概率命中缓存秒回。
  //
  // ⚠️ 重试机制（踩坑）：批量翻译偶发失败（限流/模型抽风/质量门禁丢弃
  //    脏输出）时，后端返回里对应的 id 是 null 或整个请求报错。
  //    之前失败就静默放弃，卡片会一直停在英文 —— 用户看到的就是这种卡。
  //    现在缺失/失败都走 8~10s 退避重试，最多 3 次；彻底失败才放行
  //    requestedRef，等下次 items 变化再补。
  const [zhMap, setZhMap] = useState<Record<number, CardTranslation>>({})
  const [explainMap, setExplainMap] = useState<Record<number, string>>({})
  const requestedRef = useRef<Set<number>>(new Set())
  const explainRequestedRef = useRef<Set<number>>(new Set())
  const mountedRef = useRef(true)
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
    }
  }, [])

  /**
   * 💡 LLM 推荐理由：一页 10 张合并成 1 次调用（后端按画像指纹缓存）。
   *
   * 为什么放在这里而不是 App：与翻译预热同构 —— 都是"渲染后再补齐"的可选增强，
   * 失败就静默（LLM 限流/额度耗尽时绝不影响浏览）。
   * 延迟 2.5s 发起，避免和首屏渲染抢资源。
   */
  useEffect(() => {
    const ids = items
      .slice(0, 10)
      .map((it) => it.repo_id)
      .filter((id) => !explainRequestedRef.current.has(id))
    if (ids.length === 0) return
    ids.forEach((id) => explainRequestedRef.current.add(id))

    const timer = window.setTimeout(() => {
      explainCards(userId, ids)
        .then((res) => {
          if (!mountedRef.current || !res.explanations) return
          const patch: Record<number, string> = {}
          for (const [k, v] of Object.entries(res.explanations)) {
            patch[Number(k)] = v
          }
          if (Object.keys(patch).length > 0) {
            setExplainMap((prev) => ({ ...prev, ...patch }))
          }
        })
        .catch(() => {
          /* 静默：LLM 不可用不影响浏览 */
        })
    }, 2500)
    return () => window.clearTimeout(timer)
  }, [items, userId])

  const runZh = useCallback((ids: number[], attempt: number): void => {
    translateBatch(ids)
      .then((res) => {
        if (!mountedRef.current) return
        const patch: Record<number, CardTranslation> = {}
        const missing: number[] = []
        for (const id of ids) {
          const v = res.translations[String(id)]
          if (v) patch[id] = v
          else missing.push(id)
        }
        if (Object.keys(patch).length > 0) {
          setZhMap((prev) => ({ ...prev, ...patch }))
        }
        if (missing.length > 0) {
          if (attempt < 3) {
            // 缓存未热 / 门禁丢弃：等后端重试窗口过一会儿再拉
            window.setTimeout(() => runZh(missing, attempt + 1), 8000)
          } else {
            missing.forEach((id) => requestedRef.current.delete(id))
          }
        }
      })
      .catch(() => {
        if (!mountedRef.current) return
        if (attempt < 3) {
          // 限流 / 网络失败：稍长退避
          window.setTimeout(() => runZh(ids, attempt + 1), 10000)
        } else {
          ids.forEach((id) => requestedRef.current.delete(id))
        }
      })
  }, [])

  useEffect(() => {
    const need = items
      .map((it) => it.repo_id)
      .filter((id) => !requestedRef.current.has(id))
    if (need.length === 0) return
    need.forEach((id) => requestedRef.current.add(id))
    runZh(need, 1)
  }, [items, runZh])

  const registerEl = useCallback((index: number, el: HTMLDivElement | null) => {
    if (el) cardEls.current.set(index, el)
    else cardEls.current.delete(index)
  }, [])

  // ── 用 IntersectionObserver 判定 active 卡片 ──
  useEffect(() => {
    const root = scrollRef.current
    if (!root || items.length === 0) return

    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (!e.isIntersecting) continue
          // 阈值 0.6：卡片大部分进入视口才算「正在看」
          if (e.intersectionRatio < 0.6) continue
          const idx = Number((e.target as HTMLElement).dataset.index)
          if (!Number.isNaN(idx)) setActiveIndex(idx)
        }
      },
      { root, threshold: [0.6], rootMargin: '0px' },
    )

    cardEls.current.forEach((el) => io.observe(el))
    return () => io.disconnect()
  }, [items])

  // ── 曝光埋点：卡片首次成为 active 时报一次 ──
  useEffect(() => {
    const item = items[activeIndex]
    onActiveChange?.(item ?? null)
    if (!item) return
    if (impressedRef.current.has(item.repo_id)) return
    impressedRef.current.add(item.repo_id)
    trackImpression(
      item.repo_id,
      activeIndex,
      item.channels[0],
      meta?.refill?.batch_id,
    )
  }, [activeIndex, items, meta, onActiveChange])

  // ── 接近底部 → 预加载 ──
  useEffect(() => {
    if (items.length === 0) return
    const remain = items.length - 1 - activeIndex
    if (remain <= 2 && !endedRef.current) {
      endedRef.current = true
      onReachEnd()
      // 加载完成后解锁，允许下一次触发
      window.setTimeout(() => {
        endedRef.current = false
      }, 800)
    }
  }, [activeIndex, items.length, onReachEnd])

  // 列表被清空（如搜索/刷新）时重置埋点状态。
  //
  // ⚠️ 依赖写成 isEmpty 而不是 items.length：
  //    用长度做依赖会在每次加载新页时都触发重置（长度变了），
  //    导致 impressed 集合被清空、曝光重复上报。
  //    真正需要重置的只有"从有内容变成没内容"这一次状态跃迁。
  const isEmpty = items.length === 0
  useEffect(() => {
    if (!isEmpty) return
    impressedRef.current.clear()
    endedRef.current = false
    setActiveIndex(0)
    scrollRef.current?.scrollTo({ top: 0 })
  }, [isEmpty])

  if (loading && items.length === 0) {
    return (
      <div className="h-full w-full">
        <CardSkeleton />
      </div>
    )
  }

  return (
    <div
      ref={scrollRef}
      // ⚠️ 布局要点（踩过坑）：
      //    overflow-y-auto 会产生滚动条，在 w-full 的卡片上会挤压宽度，
      //    导致内容横向溢出被裁切。用 w-screen + max-w-full 锁定宽度，
      //    再用 overscroll-contain 阻止滚动穿透到 body。
      className="snap-feed h-full w-full max-w-full overflow-y-auto overflow-x-hidden
                 overscroll-contain pt-[52px]"
      style={{ width: '100%', maxWidth: '100vw' }}
    >
      {items.map((item, i) => (
        <FeedCard
          key={`${item.repo_id}-${i}`}
          item={item}
          index={i}
          batchId={meta?.refill?.batch_id}
          userId={userId}
          isActive={i === activeIndex}
          zh={zhMap[item.repo_id]}
          explanation={explainMap[item.repo_id]}
          onOpenDetail={onOpenDetail}
          onDislike={onDislike}
          onRegisterEl={registerEl}
        />
      ))}

      {/* 列表末尾的加载指示 */}
      <div className="snap-card h-[72px] w-full flex items-center justify-center">
        {loading ? (
          <span className="text-[12px] text-ink-faint animate-pulse">
            正在补充推荐…
          </span>
        ) : meta?.needs_refill ? (
          <span className="text-[12px] text-ink-faint">
            队列见底，正在按你的兴趣补货
          </span>
        ) : (
          <span className="text-[12px] text-ink-faint/70">已经到底了</span>
        )}
      </div>
    </div>
  )
}

export default FeedList
