import { useCallback, useEffect, useRef, useState } from 'react'
import type { FC } from 'react'
import type { FeedItem, FeedMeta } from '@/types/api'
import { dislikeRepo, getFeed } from '@/lib/api'
import { flush, setEventUser } from '@/lib/events'
import FeedList from '@/components/FeedList'
import TopBar from '@/components/TopBar'
import SearchPanel from '@/components/SearchPanel'
import DetailSheet from '@/components/DetailSheet'
import ProfilePanel from '@/components/ProfilePanel'
import ToastHost, { toast } from '@/components/Toast'

/** 当前用户。真实项目里应来自登录态，这里先用固定值（后端默认 user_id=1）。 */
const USER_ID = 1
/** 每次从队列取多少条 */
const PAGE_SIZE = 10
/** 前端本地保留的最大卡片数（避免长时间刷内存无限增长） */
const MAX_ITEMS = 60

const App: FC = () => {
  const [items, setItems] = useState<FeedItem[]>([])
  const [meta, setMeta] = useState<FeedMeta | null>(null)
  const [loading, setLoading] = useState(true)
  const [fatal, setFatal] = useState<string | null>(null)

  const [showSearch, setShowSearch] = useState(false)
  const [showProfile, setShowProfile] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)

  const fetchingRef = useRef(false)

  // ── 拉取一页 ──
  const loadPage = useCallback(async (replace = false): Promise<void> => {
    if (fetchingRef.current) return
    fetchingRef.current = true
    setLoading(true)
    try {
      const res = await getFeed(USER_ID, PAGE_SIZE)
      if (res.items.length === 0 && replace) {
        setFatal('后端没有返回内容，请确认服务已启动且数据库已灌入种子数据')
      } else {
        setFatal(null)
      }
      setItems((prev) => {
        const next = replace ? res.items : [...prev, ...res.items]
        // 超出上限时截掉最早的（内存保护）
        return next.length > MAX_ITEMS ? next.slice(next.length - MAX_ITEMS) : next
      })
      setMeta(res.meta)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载失败'
      if (replace) setFatal(msg)
      else toast(msg, 'warn')
    } finally {
      setLoading(false)
      fetchingRef.current = false
    }
  }, [])

  useEffect(() => {
    setEventUser(USER_ID)
    void loadPage(true)
    // 页面卸载前把残留事件发出去
    return () => {
      void flush()
    }
  }, [loadPage])

  // ── 滑到底部继续加载 ──
  const onReachEnd = useCallback((): void => {
    void loadPage(false)
  }, [loadPage])

  // ── 不感兴趣 ──
  const onDislike = useCallback(async (item: FeedItem): Promise<void> => {
    // 先从界面上移走（乐观更新），再上报
    setItems((prev) => prev.filter((x) => x.repo_id !== item.repo_id))
    toast(`已减少「${item.name}」这类推荐`, 'ok')
    try {
      await dislikeRepo(item.repo_id, USER_ID, 'category')
    } catch {
      toast('操作没提交成功，稍后会重试', 'warn')
    }
  }, [])

  // ── 键盘操作（桌面端体验）──
  useEffect(() => {
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') {
        setShowSearch(false)
        setShowProfile(false)
        setDetailId(null)
        return
      }
      // "/" 快速打开搜索，与 GitHub 一致的惯例
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === '/') {
        e.preventDefault()
        setShowSearch(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  if (fatal) {
    return (
      <div className="h-full w-full flex flex-col items-center justify-center px-8 text-center">
        <div className="text-4xl mb-4">🛰️</div>
        <h1 className="text-[16px] font-semibold text-white/85">
          连不上推荐服务
        </h1>
        <p className="text-[12.5px] text-white/45 mt-2 leading-relaxed max-w-xs">
          {fatal}
        </p>
        <pre className="glass-soft rounded-xl px-4 py-3 mt-5 text-[11px] text-white/50 text-left leading-relaxed">
{`cd backend
python3 jobs/seed_data.py --reset
python3 app.py`}
        </pre>
        <button
          className="btn-primary mt-5"
          onClick={() => {
            setFatal(null)
            void loadPage(true)
          }}
        >
          重新连接
        </button>
      </div>
    )
  }

  return (
    <div className="relative h-full w-full bg-ink-900 overflow-hidden">
      {/* 氛围光 —— 让纯黑背景不那么死 */}
      <div
        aria-hidden
        className="pointer-events-none absolute -top-32 -left-24 h-72 w-72 rounded-full opacity-[0.16] blur-3xl"
        style={{ background: 'radial-gradient(circle, #3b82f6, transparent 70%)' }}
      />
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -right-20 h-72 w-72 rounded-full opacity-[0.12] blur-3xl"
        style={{ background: 'radial-gradient(circle, #f5b942, transparent 70%)' }}
      />

      <TopBar
        queueSize={meta?.queue_size ?? 0}
        source={meta?.source ?? 'queue'}
        onOpenSearch={() => setShowSearch(true)}
        onOpenProfile={() => setShowProfile(true)}
      />

      <FeedList
        items={items}
        meta={meta}
        loading={loading}
        userId={USER_ID}
        onOpenDetail={(it) => setDetailId(it.repo_id)}
        onDislike={(it) => void onDislike(it)}
        onReachEnd={onReachEnd}
      />

      <SearchPanel
        userId={USER_ID}
        open={showSearch}
        onClose={() => setShowSearch(false)}
        onOpenDetail={(it) => setDetailId(it.repo_id)}
      />

      <ProfilePanel
        userId={USER_ID}
        open={showProfile}
        onClose={() => setShowProfile(false)}
      />

      <DetailSheet
        repoId={detailId}
        userId={USER_ID}
        onClose={() => setDetailId(null)}
      />

      <ToastHost />
    </div>
  )
}

export default App
