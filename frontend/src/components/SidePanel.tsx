import { useEffect, useState } from 'react'
import type { FC } from 'react'
import type { FeedItem, RepoDetail } from '@/types/api'
import { explainCards, getRepo, starRepo } from '@/lib/api'
import { trackStar } from '@/lib/events'
import { formatStars, timeAgo } from '@/lib/format'

interface Props {
  /** 当前正在看的卡片（由 FeedList 的 activeIndex 上报） */
  item: FeedItem | null
  userId: number
  onOpenDetail: (item: FeedItem) => void
  onDislike: (item: FeedItem) => void
}

/**
 * 桌面端右侧信息面板 —— 「抖音网页版」式的左右分栏。
 *
 * ⭐ 为什么要它
 *   单列居中在大屏上会剩两条空白（用户原话："两边留白干嘛用的"）。
 *   宽度必须用来承载**下一层信息**：完整 README、AI 推荐理由、指标与操作。
 *   这样桌面端不用点开详情就能读完一个项目 —— 这才是桌面的价值。
 *
 * 移动端不渲染（lg:flex 才显示），完全不影响刷卡体验。
 */
const SidePanel: FC<Props> = ({ item, userId, onOpenDetail, onDislike }) => {
  const [detail, setDetail] = useState<RepoDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [explain, setExplain] = useState<string | null>(null)
  const [starred, setStarred] = useState(false)
  const repoId = item?.repo_id ?? null

  // 拉当前卡片的完整信息（含 README 全文）
  useEffect(() => {
    if (repoId == null) return
    let alive = true
    setLoading(true)
    setDetail(null)
    setExplain(null)
    getRepo(repoId, userId, true)
      .then((d) => {
        if (!alive) return
        setDetail(d)
        setStarred(Boolean(d.starred))
      })
      .catch(() => undefined)
      .finally(() => {
        if (alive) setLoading(false)
      })
    // AI 理由：命中服务端缓存（按画像指纹），几乎零成本
    explainCards(userId, [repoId])
      .then((r) => {
        if (alive) setExplain(r.explanations?.[String(repoId)] ?? null)
      })
      .catch(() => undefined)
    return () => {
      alive = false
    }
  }, [repoId, userId])

  if (!item) {
    return (
      <aside className="hidden lg:flex flex-1 min-w-0 bg-canvas border-l border-divider
                        items-center justify-center">
        <span className="text-[13px] text-ink-faint">正在加载推荐的仓库…</span>
      </aside>
    )
  }

  const lic = item.license_spdx

  return (
    <aside className="hidden lg:flex flex-1 min-w-0 bg-canvas border-l border-divider">
      <div className="flex-1 min-w-0 flex flex-col overflow-y-auto">
        {/* pt-[68px]：让开固定顶栏（52px）+ 呼吸空间，否则标题被压在顶栏下面 */}
        <div className="px-8 pt-[68px] pb-8 max-w-[720px]">
          {/* ── 身份 ── */}
          <div className="flex items-start gap-3">
            <div
              className="h-11 w-11 shrink-0 rounded-pearl flex items-center justify-center
                         text-[17px] font-semibold bg-parchment text-ink-soft"
            >
              {item.owner.slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="text-[22px] font-semibold leading-tight text-ink tracking-[-0.55px]">
                {item.name}
              </h1>
              <p className="text-[12.5px] text-ink-muted mt-1">
                {item.owner}
                {item.pushed_at_gh ? ` · 更新于 ${timeAgo(item.pushed_at_gh)}` : ''}
              </p>
            </div>
            <a
              className="chip bg-pearl border border-hairline text-ink-soft shrink-0
                         hover:text-ink transition-colors"
              href={item.url}
              target="_blank"
              rel="noreferrer"
            >
              在 GitHub 打开 ↗
            </a>
          </div>

          {/* ── AI 推荐理由 ── */}
          {explain && (
            <div className="mt-4 flex items-start gap-1.5">
              <span className="text-[11px] text-accent shrink-0 mt-[2px]">💡 AI</span>
              <span className="text-[13px] leading-[1.6] text-ink-soft">{explain}</span>
            </div>
          )}

          {/* ── 指标行 ── */}
          <div className="flex items-center gap-1.5 flex-wrap mt-4">
            <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
              ⭐ {formatStars(item.stars)}
            </span>
            {detail && (
              <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                🍴 {formatStars(detail.forks)}
              </span>
            )}
            {item.language && (
              <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                📓 {item.language}
              </span>
            )}
            <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
              🎯 {item.score.toFixed(2)}
            </span>
            {lic && (
              <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                {lic}
              </span>
            )}
          </div>

          {/* 简介不在这里重复（左侧卡片已经展示），面板主打 README 全文 */}

          {/* ── 标签 ── */}
          <div className="flex flex-wrap gap-1.5 mt-4">
            {item.tags.slice(0, 6).map((t) => (
              <span
                key={t.tag}
                className="chip bg-pearl text-ink-soft border border-hairline"
              >
                {t.tag}
              </span>
            ))}
            {item.topics.slice(0, 4).map((t) => (
              <span key={t} className="text-[11.5px] text-ink-faint self-center">
                #{t}
              </span>
            ))}
          </div>

          {/* ── README 全文（桌面端的核心价值：不用点开就能读完）── */}
          <div className="mt-6 pt-5 border-t border-divider">
            <div className="text-[11px] text-ink-faint mb-2">README</div>
            {loading && <div className="text-[12.5px] text-ink-faint">读取中…</div>}
            {!loading && detail?.readme_md && (
              <pre className="whitespace-pre-wrap break-words font-sans
                              text-[12.5px] leading-[1.75] text-ink-soft">
                {detail.readme_md}
              </pre>
            )}
            {!loading && !detail?.readme_md && (
              <div className="text-[12.5px] text-ink-faint">这个仓库没有 README</div>
            )}
          </div>

          {/* ── 操作 ── */}
          <div className="flex items-center gap-2 mt-5 pt-5 border-t border-divider">
            <button
              className={`btn-ghost ${starred ? 'text-accent' : ''}`}
              onClick={async () => {
                try {
                  await starRepo(item.repo_id, userId)
                  setStarred((v) => !v)
                  trackStar(item.repo_id)
                } catch {
                  /* 静默 */
                }
              }}
            >
              {starred ? '⭐ 已收藏' : '☆ 收藏'}
            </button>
            <button className="btn-ghost" onClick={() => onOpenDetail(item)}>
              详情面板 ›
            </button>
            <div className="flex-1" />
            <button
              className="text-[12.5px] text-ink-muted hover:text-risk-danger transition-colors"
              onClick={() => onDislike(item)}
            >
              不感兴趣
            </button>
          </div>
        </div>
      </div>
    </aside>
  )
}

export default SidePanel
