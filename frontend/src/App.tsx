import { useCallback, useEffect, useRef, useState } from 'react'
import type { FC } from 'react'
import type { FeedItem, FeedMeta, FeedMode, SessionUser } from '@/types/api'
import { dislikeRepo, getFeed, getMe, logout, syncGithub } from '@/lib/api'
import { flush, setEventUser, trackClick } from '@/lib/events'
import {
  clearToken,
  consumeAuthFromUrl,
  getCachedUser,
  getToken,
  isGuest,
  setCachedUser,
  setGuest,
  setToken,
} from '@/lib/session'
import FeedList from '@/components/FeedList'
import SidePanel from '@/components/SidePanel'
import TopBar from '@/components/TopBar'
import SearchPanel from '@/components/SearchPanel'
import DetailSheet from '@/components/DetailSheet'
import ProfilePanel from '@/components/ProfilePanel'
import LoginGate from '@/components/LoginGate'
import ToastHost, { toast } from '@/components/Toast'

/** 游客模式对应的后端用户（数据库里的 demo 用户） */
const GUEST_USER: SessionUser = {
  user_id: 1,
  username: 'demo',
  github_login: null,
  display_name: '演示用户',
  avatar_url: null,
}
/** 每次从队列取多少条 */
const PAGE_SIZE = 10
/** 前端本地保留的最大卡片数（避免长时间刷内存无限增长） */
const MAX_ITEMS = 60

const App: FC = () => {
  const [items, setItems] = useState<FeedItem[]>([])
  const [meta, setMeta] = useState<FeedMeta | null>(null)
  const [loading, setLoading] = useState(true)
  const [fatal, setFatal] = useState<string | null>(null)

  // ── 登录态 ──
  const [user, setUser] = useState<SessionUser | null>(null)
  const [authReady, setAuthReady] = useState(false)
  const userId = user?.user_id ?? 1

  const [showSearch, setShowSearch] = useState(false)
  const [showProfile, setShowProfile] = useState(false)
  const [detailId, setDetailId] = useState<number | null>(null)
  /** 当前正在看的卡片（侧滑抽屉的数据源，由 FeedList 上报） */
  const [activeItem, setActiveItem] = useState<FeedItem | null>(null)
  /** 侧滑抽屉（项目详情）是否展开 */
  const [drawerOpen, setDrawerOpen] = useState(false)
  /** 信息流分档：全部 / 热门 / 遗珠 */
  const [feedMode, setFeedMode] = useState<FeedMode>('all')

  const fetchingRef = useRef(false)
  /** 首屏加载失败的重试计数（后端瞬时抖动不该直接甩一屏报错） */
  const bootRetryRef = useRef(0)
  /** 首屏最多自动重试几次 */
  const BOOT_RETRY_MAX = 3
  /** 追加模式拿到空页时的重试计数（队列可能在补货中，不该让用户卡死） */
  const emptyRetryRef = useRef(0)

  // ── 拉取一页 ──
  const loadPage = useCallback(async (replace = false): Promise<void> => {
    if (fetchingRef.current) return
    fetchingRef.current = true
    setLoading(true)
    try {
      const res = await getFeed(userId, PAGE_SIZE, undefined, feedMode)
      if (res.items.length === 0 && replace) {
        setFatal('后端没有返回内容，请确认服务已启动且数据库已灌入种子数据')
      } else {
        setFatal(null)
        bootRetryRef.current = 0
      }
      setItems((prev) => {
        const next = replace ? res.items : [...prev, ...res.items]
        // 超出上限时截掉最早的（内存保护）
        return next.length > MAX_ITEMS ? next.slice(next.length - MAX_ITEMS) : next
      })
      setMeta(res.meta)

      // ⭐ 追加到空页 → 自动退避重试。
      //    实测问题：队列被刷到底、补货还在冷却时，这次请求返回 0 条，
      //    前端就永久停在"已经到底了"，而其实几十秒后就有货了。
      if (!replace && res.items.length === 0) {
        if (emptyRetryRef.current < 5) {
          emptyRetryRef.current += 1
          window.setTimeout(() => {
            void loadPage(false)
          }, 3000 * emptyRetryRef.current)
        }
      } else {
        emptyRetryRef.current = 0
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载失败'
      // ⭐ 首屏失败先自动重试：实测后端偶发 500（SQLite 写锁）或正在重启时，
      //    直接甩"连不上推荐服务"太粗暴 —— 退避重试 3 次再认输。
      if (replace && bootRetryRef.current < BOOT_RETRY_MAX) {
        bootRetryRef.current += 1
        const delay = 1200 * bootRetryRef.current
        setTimeout(() => {
          fetchingRef.current = false
          void loadPage(true)
        }, delay)
      } else if (replace) {
        setFatal(msg)
      } else {
        toast(msg, 'warn')
      }
    } finally {
      setLoading(false)
      fetchingRef.current = false
    }
    // ⚠️ 依赖必须带 feedMode：否则切换分档时这个 useCallback 还是旧闭包
    //    （里面捕获的 mode 还是旧值），下面的 feed-loading effect 不会重跑 →
    //    一个请求都不发，而 setItems([]) 已经清了列表 → 永久空屏。
  }, [userId, feedMode])

  // ── 报错页自动重试：后端起来了就自动恢复，不用用户点 ──
  useEffect(() => {
    if (!fatal) return
    const t = setInterval(() => {
      if (!fetchingRef.current) void loadPage(true)
    }, 10_000)
    return () => clearInterval(t)
  }, [fatal, loadPage])

  // ── 同步 GitHub 实证数据（自建仓库 + 点星仓库）──
  const runSync = useCallback(async (): Promise<void> => {
    toast('正在读取你的 GitHub 仓库与点星…', 'info')
    try {
      const r = await syncGithub()
      toast(`已同步：自建 ${r.owned} 个 / 点星 ${r.starred} 个，画像已重建`, 'ok')
      void loadPage(true)
    } catch (e) {
      toast(e instanceof Error ? e.message : '同步失败', 'warn')
    }
  }, [loadPage])

  // ── 登录态初始化（含 OAuth 回调处理）──
  useEffect(() => {
    const { token, error, needSync } = consumeAuthFromUrl()
    if (token) {
      setToken(token)
      setGuest(false)
    }
    if (error) toast(`GitHub 登录失败：${error}`, 'warn')

    const boot = async (): Promise<void> => {
      if (getToken()) {
        try {
          const me = await getMe()
          setCachedUser(me)
          setUser(me)
          setAuthReady(true)
          if (needSync) void runSync()
          return
        } catch {
          // 会话过期 / 数据库被重置：退回登录页
          clearToken()
          setCachedUser(null)
        }
      }
      if (isGuest()) {
        setCachedUser(GUEST_USER)
        setUser(GUEST_USER)
      } else {
        const cached = getCachedUser()
        if (cached) setUser(cached) // 先乐观渲染
      }
      setAuthReady(true)
    }
    void boot()
  }, [runSync])

  // ── 拿到用户后拉 Feed ──
  useEffect(() => {
    if (!authReady || !user) return
    setEventUser(user.user_id)
    void loadPage(true)
    return () => {
      void flush()
    }
  }, [authReady, user, loadPage])

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
      await dislikeRepo(item.repo_id, userId, 'category')
    } catch {
      toast('操作没提交成功，稍后会重试', 'warn')
    }
  }, [userId])

  // ── 退出登录 ──
  const handleLogout = useCallback(async (): Promise<void> => {
    try {
      await logout()
    } catch {
      /* 网络失败也照常清本地 */
    }
    clearToken()
    setCachedUser(null)
    setGuest(false)
    setUser(null)
    setShowProfile(false)
    toast('已退出登录', 'ok')
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

  // ── 启动中：极简闪屏，避免先闪一下登录页 ──
  if (!authReady) {
    return (
      <div className="h-full w-full bg-parchment flex items-center justify-center">
        <span className="text-[13px] text-ink-faint animate-pulse">RecoFeed</span>
      </div>
    )
  }

  // ── 未登录且非游客 → 登录页 ──
  if (!user) {
    return (
      <div className="h-full w-full bg-parchment overflow-hidden">
        <LoginGate
          onLoggedIn={(u) => setUser(u)}
          onNeedSync={() => void runSync()}
        />
        <ToastHost />
      </div>
    )
  }

  if (fatal) {
    return (
      <div className="h-full w-full flex flex-col items-center justify-center px-8 text-center bg-parchment">
        <div className="text-4xl mb-4">🛰️</div>
        <h1 className="text-[17px] font-semibold text-ink tracking-[-0.22px]">
          连不上推荐服务
        </h1>
        <p className="text-[12.5px] text-ink-muted mt-2 leading-relaxed max-w-xs">
          {fatal}
        </p>
        <pre className="bg-canvas border border-hairline rounded-util px-4 py-3 mt-5 text-[11px] text-ink-soft text-left leading-relaxed font-mono">
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
    // 羊皮纸底座：卡片自己画白/羊皮纸交替瓦片，
    // 颜色差本身就是分隔线 —— 不加边框、不加阴影。
    <div className="relative h-full w-full bg-parchment overflow-hidden">
      <TopBar
        queueSize={meta?.queue_size ?? 0}
        source={meta?.source ?? 'queue'}
        user={user}
        mode={feedMode}
        onChangeMode={(m) => {
          setFeedMode(m)
          setItems([])          // 换档先清空，避免旧档的卡片混在列表里
        }}
        onOpenSearch={() => setShowSearch(true)}
        onOpenProfile={() => setShowProfile(true)}
      />

      {/* ── 居中信息流（限宽一列）+ 侧滑抽屉 ──
          卡片自己就是"一页"（满屏 + 用满宽度：卡内左右两列），
          Feed 容器不再限宽居中 —— 不再有左右空白。
          细节（README 全文）仍按需从右侧抽屉滑出。 */}
      <div className="h-full w-full">
        <div className="relative w-full h-full">
          <FeedList
            items={items}
            meta={meta}
            loading={loading}
            userId={userId}
            onOpenDetail={(it) => {
              // ⭐ click 事件 = "值得点开看"（比滑过更强的正信号），后端据此算 ctr
              trackClick(it.repo_id)
              setDrawerOpen(true)
            }}
            onDislike={(it) => void onDislike(it)}
            onReachEnd={onReachEnd}
            onActiveChange={setActiveItem}
          />
        </div>
      </div>

      <SidePanel
        open={drawerOpen}
        item={activeItem}
        userId={userId}
        onClose={() => setDrawerOpen(false)}
        onOpenDetail={(it) => {
          setDrawerOpen(false)
          setDetailId(it.repo_id)
        }}
        onDislike={(it) => void onDislike(it)}
      />

      <SearchPanel
        userId={userId}
        open={showSearch}
        onClose={() => setShowSearch(false)}
        onOpenDetail={(it) => {
          // 从搜索结果点开也算 click（正信号）
          trackClick(it.repo_id)
          setDetailId(it.repo_id)
        }}
      />

      <ProfilePanel
        userId={userId}
        user={user}
        open={showProfile}
        onClose={() => setShowProfile(false)}
        onSync={() => void runSync()}
        onLogout={() => void handleLogout()}
      />

      <DetailSheet
        repoId={detailId}
        userId={userId}
        onClose={() => setDetailId(null)}
      />

      <ToastHost />
    </div>
  )
}

export default App
