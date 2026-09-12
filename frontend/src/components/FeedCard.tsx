import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { FC } from 'react'
import type { FeedItem } from '@/types/api'
import {
  formatStars,
  hashHue,
  isForgotten,
  langColor,
  licenseBadge,
  timeAgo,
} from '@/lib/format'
import { trackLike, trackSwipe } from '@/lib/events'
import { likeRepo, unlikeRepo } from '@/lib/api'

const REASON_ICONS: Record<string, string> = {
  遗珠: '💎',
  探索: '🧭',
  兴趣: '🎯',
  相似: '🔗',
  冷启动: '🌱',
  热门: '🔥',
  搜索: '🔍',
}

/** 从后端给的 reason 文案里挑一个图标，挑不到就不显示 */
function reasonIcon(reason: string): string | null {
  for (const [key, icon] of Object.entries(REASON_ICONS)) {
    if (reason.includes(key)) return icon
  }
  return null
}

interface Props {
  item: FeedItem
  /** 在 Feed 里的序号，用于行为上报的 rank_position */
  index: number
  batchId?: string
  userId: number
  isActive: boolean
  onOpenDetail: (item: FeedItem) => void
  onDislike: (item: FeedItem) => void
  onRegisterEl?: (index: number, el: HTMLDivElement | null) => void
}

/**
 * 单个 Feed 卡片。
 *
 * ⭐ 浏览时长与滚动深度的埋点在这里完成 —— 它是推荐系统的核心输入。
 *
 * 埋点时机：
 *   卡片成为 active（滑到视口中心）→ 记 impression + 开始计时
 *   卡片失去 active                  → 结算 dwell_ms / scroll_depth，
 *                                      判定 deep_read 还是 skip
 */
const FeedCard: FC<Props> = ({
  item,
  index,
  batchId,
  userId,
  isActive,
  onOpenDetail,
  onDislike,
  onRegisterEl,
}) => {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const enterAtRef = useRef<number>(0)
  const maxDepthRef = useRef<number>(0)
  const [liked, setLiked] = useState(false)

  const lang = item.language
  const lic = licenseBadge(item.license_spdx, item.license_risk)
  const hue = hashHue(item.owner)
  const gem = isForgotten(item.forgotten_score)
  const icon = reasonIcon(item.reason)

  const topTags = useMemo(
    () => item.tags.filter((t) => t.tag.length > 1).slice(0, 6),
    [item.tags],
  )

  useEffect(() => {
    onRegisterEl?.(index, rootRef.current)
    return () => onRegisterEl?.(index, null)
  }, [index, onRegisterEl])

  // ── 埋点：进入 / 离开 active ──
  //
  // ⚠️ 依赖里不能直接放 item.channels（数组引用每次渲染都可能变），
  //    否则这个 effect 会反复执行，把 enterAtRef 重置掉，导致
  //    dwell_ms 永远算成 0。这里取它第一个元素（字符串，值稳定）。
  const channelKey = item.channels[0] ?? ''
  useEffect(() => {
    if (isActive) {
      enterAtRef.current = Date.now()
      maxDepthRef.current = 0
      return
    }
    // 离开时结算（enterAt 为 0 说明还没激活过，不报）
    if (enterAtRef.current > 0) {
      const dwell = Date.now() - enterAtRef.current
      // 停留太短（< 400ms）说明只是滑过去，不产生有效曝光
      if (dwell >= 400) {
        trackSwipe(
          item.repo_id,
          dwell,
          maxDepthRef.current,
          index,
          channelKey || undefined,
          batchId,
        )
      }
      enterAtRef.current = 0
    }
  }, [isActive, item.repo_id, channelKey, index, batchId])

  // ── 卡片内部滚动 → 记录最大滚动深度 ──
  const onScroll = (): void => {
    const el = scrollRef.current
    if (!el) return
    const max = el.scrollHeight - el.clientHeight
    if (max <= 4) {
      // 内容不足一屏，视为已完整浏览
      maxDepthRef.current = Math.max(maxDepthRef.current, 1)
      return
    }
    const d = el.scrollTop / max
    if (d > maxDepthRef.current) maxDepthRef.current = d
  }

  const toggleLike = async (): Promise<void> => {
    const next = !liked
    setLiked(next)
    trackLike(item.repo_id, next)
    try {
      if (next) await likeRepo(item.repo_id, userId)
      else await unlikeRepo(item.repo_id, userId)
    } catch {
      setLiked(!next) // 失败回滚 UI
    }
  }

  return (
    <section
      ref={rootRef}
      data-index={index}
      className="snap-card relative h-full w-full shrink-0"
    >
      {/* ⚠️ overflow-hidden 必不可少。
          卡片内部有 truncate / 长描述 / 标签换行，
          任何一处在窄屏下算出超宽都会把整页撑出横向滚动条，
          表现为"右侧内容被裁掉"。 */}
      <div className="h-full w-full px-4 pt-14 pb-4 flex flex-col overflow-hidden">
        {/* ── 顶部：仓库身份 ── */}
        <div className="flex items-start gap-3">
          <div
            className="h-11 w-11 shrink-0 rounded-xl flex items-center justify-center
                       text-base font-bold text-white/95 border border-white/10"
            style={{
              background: `linear-gradient(135deg, hsl(${hue} 62% 42%), hsl(${(hue + 48) % 360} 58% 30%))`,
            }}
          >
            {item.owner.slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-[17px] font-semibold leading-tight text-white truncate">
              {item.name}
            </h2>
            <p className="text-[12px] text-white/45 mt-0.5 truncate">
              {item.owner}
              {item.pushed_at_gh ? ` · 更新于 ${timeAgo(item.pushed_at_gh)}` : ''}
            </p>
          </div>
          {/* 遗珠标记 —— 本项目的品牌符号 */}
          {gem && (
            <span
              className="chip border border-gem/45 bg-gem/15 text-gem animate-gem-pulse shrink-0"
              title="低曝光但口碑很好，可能被埋没了"
            >
              💎 遗珠
            </span>
          )}
        </div>

        {/* ── 推荐理由 ── */}
        <div className="mt-3.5">
          <span className="glass-soft chip text-accent-glow/95">
            {icon ? `${icon} ` : ''}
            {item.reason}
          </span>
        </div>

        {/* ── 可滚动正文区 ── */}
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="mt-4 flex-1 min-h-0 overflow-y-auto pr-1 space-y-4"
        >
          {item.description && (
            <p className="text-[14.5px] leading-relaxed text-white/78">
              {item.description}
            </p>
          )}

          {/* 标签 */}
          {topTags.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {topTags.map((t) => (
                <span
                  key={t.tag}
                  className="chip bg-white/[0.05] text-white/55 border border-white/[0.07]"
                  title={`权重 ${t.weight.toFixed(2)}`}
                >
                  {t.tag}
                </span>
              ))}
            </div>
          )}

          {/* topics */}
          {item.topics.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {item.topics.slice(0, 8).map((t) => (
                <span key={t} className="chip text-white/35">
                  #{t}
                </span>
              ))}
            </div>
          )}

          {/* 许可证 */}
          {lic && (
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`chip border ${lic.cls}`}>{lic.text}</span>
              {item.license_risk === 'caution' && (
                <span className="text-[11px] text-risk-caution/80">
                  商用需留意条款
                </span>
              )}
              {item.license_risk === 'danger' && (
                <span className="text-[11px] text-risk-danger/80">
                  协议限制较强
                </span>
              )}
            </div>
          )}

          {/* 底部留白，避免最后一行被指标栏盖住 */}
          <div className="h-2" />
        </div>

        {/* ── 指标栏 ── */}
        <div className="mt-4 grid grid-cols-3 gap-2 min-w-0">
          <Metric label="Star" value={formatStars(item.stars)} />
          <Metric
            label="语言"
            value={lang ?? '—'}
            dot={lang ? langColor(lang) : undefined}
          />
          <Metric label="匹配度" value={item.score.toFixed(2)} />
        </div>

        {/* ── 操作栏 ── */}
        <div className="mt-3 flex items-center gap-2 safe-b">
          <button
            className="btn-primary flex-1 min-w-0"
            onClick={() => onOpenDetail(item)}
          >
            查看详情
          </button>
          <button
            className="btn-ghost w-11 shrink-0 !px-0"
            onClick={() => void toggleLike()}
            title={liked ? '取消点赞' : '点赞'}
            aria-label={liked ? '取消点赞' : '点赞'}
          >
            {liked ? '💙' : '🤍'}
          </button>
          <button
            className="btn-ghost w-11 shrink-0 !px-0 text-white/50 hover:text-risk-danger"
            onClick={() => onDislike(item)}
            title="不感兴趣"
            aria-label="不感兴趣"
          >
            <BanIcon />
          </button>
        </div>
      </div>
    </section>
  )
}

const BanIcon: FC = () => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
  >
    <circle cx="12" cy="12" r="9" />
    <path d="m5.6 5.6 12.8 12.8" />
  </svg>
)

const Metric: FC<{ label: string; value: string; dot?: string }> = ({
  label,
  value,
  dot,
}) => (
  <div className="glass-soft rounded-2xl px-2 py-2.5 text-center min-w-0">
    <div className="flex items-center justify-center gap-1.5 min-w-0">
      {dot && (
        <span
          className="h-2 w-2 rounded-full shrink-0"
          style={{ background: dot }}
        />
      )}
      <span className="text-[14px] font-semibold text-white/92 leading-none truncate">
        {value}
      </span>
    </div>
    <div className="text-[10px] text-white/40 mt-1.5 leading-none">
      {label}
    </div>
  </div>
)

export default memo(FeedCard)
