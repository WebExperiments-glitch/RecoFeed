import type { FC } from 'react'
import type { FeedMode, SessionUser } from '@/types/api'

interface Props {
  queueSize: number
  source: string
  user: SessionUser | null
  /** 信息流分档：全部 / 热门（≥1000 星）/ 遗珠（<1000 星） */
  mode: FeedMode
  onChangeMode: (m: FeedMode) => void
  onOpenSearch: () => void
  onOpenProfile: () => void
}

/**
 * 顶部状态栏（磨砂羊皮纸导航，sub-nav-frosted 变体）。
 *
 * Apple 语法：悬浮感来自 backdrop blur + parchment 80%，
 * 不来自阴影；标题用负字距的「Apple tight」排版。
 *
 * 显示队列水位 —— 这是本项目的一个「透明度」设计：
 *   用户能看到还有多少条待刷、是否在补货，
 *   而不是黑盒地"刷着刷着就没了"。
 */
const TopBar: FC<Props> = ({
  queueSize,
  source,
  user,
  mode,
  onChangeMode,
  onOpenSearch,
  onOpenProfile,
}) => {
  const isRealtime = source.includes('realtime')
  return (
    <header className="absolute top-0 inset-x-0 z-30 safe-t">
      <div className="glass flex items-center gap-2 px-4 h-[52px]">
        <div className="flex items-baseline gap-1.5 select-none">
          <span className="text-[21px] font-semibold text-ink leading-none tracking-[-0.23px]">
            RecoFeed
          </span>
          <span className="text-[10px] text-ink-faint">遗珠推荐</span>
        </div>

        <div className="flex-1" />

        {/* 分档切换：热门（≥1000 星）/ 遗珠（<1000 星）
            遗珠是本产品的立身之本 —— 让被埋没的低星好项目浮上来 */}
        <div className="flex items-center rounded-full bg-pearl border border-hairline p-0.5">
          {([
            ['all', '探索'],
            ['hot', '热门'],
            ['gem', '遗珠'],
          ] as [FeedMode, string][]).map(([k, label]) => (
            <button
              key={k}
              className={`h-6 px-2.5 rounded-full text-[11.5px] transition-colors ${
                mode === k
                  ? 'bg-accent text-white'
                  : 'text-ink-muted hover:text-ink'
              }`}
              onClick={() => onChangeMode(k)}
            >
              {label}
            </button>
          ))}
        </div>

        {/* 队列水位 —— 珍珠胶囊（button-pearl-capsule 语法） */}
        <div
          className="chip bg-pearl border border-hairline text-ink-muted gap-1.5 h-7"
          title={isRealtime ? '缓存池见底，正在实时兜底推荐' : '待刷缓存池水位'}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isRealtime ? 'bg-risk-caution' : 'bg-risk-safe'
            }`}
          />
          {queueSize > 0 ? `${queueSize} 待刷` : '补货中'}
        </div>

        <button
          className="h-9 w-9 rounded-full flex items-center justify-center
                     bg-pearl border border-hairline text-ink-soft
                     hover:text-ink hover:bg-parchment transition-colors active:scale-95"
          onClick={onOpenSearch}
          aria-label="搜索"
        >
          <SearchIcon />
        </button>

        <button
          className="h-9 w-9 rounded-full flex items-center justify-center overflow-hidden
                     bg-pearl border border-hairline text-ink-soft
                     hover:text-ink hover:bg-parchment transition-colors active:scale-95"
          onClick={onOpenProfile}
          aria-label="我的画像"
          title={user?.github_login ? `@${user.github_login}` : '我的画像'}
        >
          {user?.avatar_url ? (
            <img
              src={user.avatar_url}
              alt={user.github_login ?? 'avatar'}
              className="h-full w-full object-cover"
              referrerPolicy="no-referrer"
            />
          ) : (
            <UserIcon />
          )}
        </button>
      </div>
    </header>
  )
}

const SearchIcon: FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
)

const UserIcon: FC = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21c0-4 3.6-6 8-6s8 2 8 6" />
  </svg>
)

export default TopBar
