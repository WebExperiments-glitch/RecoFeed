import type { FC } from 'react'

interface Props {
  queueSize: number
  source: string
  onOpenSearch: () => void
  onOpenProfile: () => void
}

/**
 * 顶部状态栏（毛玻璃悬浮）。
 *
 * 显示队列水位 —— 这是本项目的一个「透明度」设计：
 *   用户能看到还有多少条待刷、是否在补货，
 *   而不是黑盒地"刷着刷着就没了"。
 */
const TopBar: FC<Props> = ({ queueSize, source, onOpenSearch, onOpenProfile }) => {
  const isRealtime = source.includes('realtime')
  return (
    <header className="absolute top-0 inset-x-0 z-30 safe-t">
      <div className="flex items-center gap-2 px-4 py-3">
        <div className="flex items-baseline gap-1.5 select-none">
          <span className="text-[17px] font-bold text-gradient">RecoFeed</span>
          <span className="text-[10px] text-white/35">遗珠推荐</span>
        </div>

        <div className="flex-1" />

        {/* 队列水位 */}
        <div
          className="glass-soft chip gap-1.5 text-white/55"
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
          className="glass-soft h-9 w-9 rounded-full flex items-center justify-center
                     text-white/70 hover:text-white transition-colors"
          onClick={onOpenSearch}
          aria-label="搜索"
        >
          <SearchIcon />
        </button>

        <button
          className="glass-soft h-9 w-9 rounded-full flex items-center justify-center
                     text-white/70 hover:text-white transition-colors"
          onClick={onOpenProfile}
          aria-label="我的画像"
        >
          <UserIcon />
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
