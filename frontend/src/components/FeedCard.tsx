import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { FC } from 'react'
import type { CardTranslation, FeedItem } from '@/types/api'
import {
  formatStars,
  hashHue,
  isForgotten,
  licenseBadge,
  timeAgo,
  formatSize,
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
  /** 批量翻译好的中文（标题短译 + 描述译文），无则回退英文原文 */
  zh?: CardTranslation
  /** 💡 LLM 生成的一句话推荐理由（比"根据你的浏览兴趣挑选"具体得多） */
  explanation?: string
  onOpenDetail: (item: FeedItem) => void
  onDislike: (item: FeedItem) => void
  onRegisterEl?: (index: number, el: HTMLDivElement | null) => void
}

/**
 * 单个 Feed 卡片 —— Apple 满幅瓦片（product-tile）。
 *
 * 瓦片节奏：偶数白瓷 / 奇数羊皮纸交替，颜色差本身就是分隔线，
 * 不加边框、不加阴影。字重纪律：标题 600、正文 400，无 500。
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
  zh,
  explanation,
  onOpenDetail,
  onDislike,
  onRegisterEl,
}) => {
  const rootRef = useRef<HTMLDivElement | null>(null)
  const enterAtRef = useRef<number>(0)
  const maxDepthRef = useRef<number>(0)
  const [liked, setLiked] = useState(false)

  const zhName = zh?.zh_name?.trim()
  const zhDesc = zh?.zh_description?.trim()
  const lang = item.language
  const lic = licenseBadge(item.license_spdx, item.license_risk)
  const hue = hashHue(item.owner)
  const gem = isForgotten(item.forgotten_score)
  const icon = reasonIcon(item.reason)

  const topTags = useMemo(
    () => item.tags.filter((t) => t.tag.length > 1).slice(0, 6),
    [item.tags],
  )

  /** 真正的译文：与原文相同（模型回吐原文）就当没有译文，避免同一句话渲染两遍 */
  const descEn = (item.description ?? '').trim()
  /** 原文本身已经是中文 → 不需要翻译，也不该显示"翻译生成中" */
  const descAlreadyChinese = useMemo(() => {
    if (!descEn) return false
    const cjk = (descEn.match(/[\u4e00-\u9fff]/g) ?? []).length
    return cjk >= 4 && cjk >= descEn.length * 0.15
  }, [descEn])
  const zhDescReal = useMemo(() => {
    const z = (zhDesc ?? '').trim()
    if (!z) return ''
    const norm = (s: string) => s.toLowerCase().replace(/\s+/g, '')
    if (norm(z) === norm(descEn)) return ''
    const cjk = (z.match(/[\u4e00-\u9fff]/g) ?? []).length
    return cjk >= 4 ? z : ''
  }, [zhDesc, descEn])

  /** 话题去重：与算法标签重复（含互为子串，如 detection/detect）的 topics 全部丢掉 */
  const cleanTopics = useMemo(() => {
    const tagLows = topTags.map((t) => t.tag.toLowerCase())
    return item.topics
      .filter((tp) => {
        const tl = tp.toLowerCase()
        if (tagLows.includes(tl)) return false
        return !tagLows.some(
          (x) =>
            x.length >= 4 && tl.length >= 4 && (x.includes(tl) || tl.includes(x)),
        )
      })
      .slice(0, 4)
  }, [item.topics, topTags])

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
      // ⭐ 卡片现在由内容撑开（不再有卡片内滚动），
      //    所以"读完比例"改用「卡片有多少落在视口内」来估算 ——
      //    100% 落在视口 = 完整看过。
      const el = rootRef.current
      maxDepthRef.current = el
        ? Math.max(
            0.15,
            Math.min(
              1,
              (Math.min(window.innerHeight, el.offsetHeight) -
                Math.max(0, -el.getBoundingClientRect().top)) /
                Math.max(1, el.offsetHeight),
            ),
          )
        : 0.5
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
      className={`snap-card relative h-full w-full shrink-0 ${
        index % 2 === 0 ? 'bg-canvas' : 'bg-parchment'
      }`}
    >
      {/* ⭐ 一个仓库 = 一页（满屏），但**内容用满宽度**：
          桌面把卡片内部拆成两列（左：身份/理由/简介/标签；右：README 填满高度），
          移动端两列折叠成一列。纵向填满（一页一仓库）、横向也不留空白。 */}
      <div className="h-full w-full px-6 pt-16 pb-5 flex flex-col gap-4 overflow-hidden">
        {/* ── 头部：仓库身份 + 指标（桌面右上）── */}
        <div className="flex items-start gap-4">
          <div
            className="h-11 w-11 shrink-0 rounded-pearl flex items-center justify-center
                       text-[17px] font-semibold"
            style={{
              background: `hsl(${hue} 52% 91%)`,
              color: `hsl(${hue} 45% 30%)`,
            }}
          >
            {item.owner.slice(0, 1).toUpperCase()}
          </div>
          <div className="min-w-0 flex-1">
            <h2
              className="text-[22px] font-semibold leading-tight text-ink tracking-[-0.6px] truncate"
              title={zhName ? item.name : undefined}
            >
              {zhName || item.name}
            </h2>
            <p className="text-[12.5px] text-ink-muted mt-1 truncate">
              {zhName ? `${item.name} · ` : ''}
              {item.owner}
              {item.pushed_at_gh ? ` · 更新于 ${timeAgo(item.pushed_at_gh)}` : ''}
            </p>
          </div>

          {gem && (
            <span
              className="chip border border-gem/25 bg-gem/[0.08] text-gem animate-gem-pulse shrink-0"
              title="低曝光但口碑很好，可能被埋没了"
            >
              💎 遗珠
            </span>
          )}

          <div className="hidden lg:flex items-center gap-1.5 flex-wrap shrink-0 pt-1">
            <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
              ⭐ {formatStars(item.stars)}
            </span>
            {lang && (
              <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                📓 {lang}
              </span>
            )}
            <span
              className="chip h-6 bg-pearl text-ink-muted border border-hairline"
              title={
                item.personal_score != null
                  ? `个人模型匹配度 ${item.personal_score.toFixed(2)}｜质量分 ${item.score.toFixed(2)}`
                  : `质量分 ${item.score.toFixed(2)}`
              }
            >
              🎯 {(item.personal_score ?? item.score).toFixed(2)}
            </span>
            {lic && <span className={`chip h-6 border ${lic.cls}`}>{lic.text}</span>}
          </div>
        </div>

        {/* ── 主体：桌面两列 / 移动单列 ── */}
        <div className="flex-1 min-h-0 flex flex-col lg:flex-row gap-5">
          <div className="flex-1 min-w-0 flex flex-col gap-3 overflow-y-auto pr-1">
            <div>
              <span className="chip bg-accent/[0.07] text-accent border border-accent/15">
                {icon ? `${icon} ` : ''}
                {item.reason}
              </span>
            </div>

            {explanation && (
              <div className="flex items-start gap-1.5">
                <span className="text-[11px] text-accent shrink-0 mt-[2px]">💡 AI</span>
                <span className="text-[13px] leading-[1.6] text-ink-soft">
                  {explanation}
                </span>
              </div>
            )}

            {item.description && (
              <div>
                {zhDescReal ? (
                  <>
                    <p className="text-[16px] leading-[1.65] text-ink">{zhDescReal}</p>
                    <p className="text-[12px] leading-relaxed text-ink-faint mt-1.5">
                      {item.description}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-[16px] leading-[1.65] text-ink-soft">
                      {item.description}
                    </p>
                    {!descAlreadyChinese && (
                      <span className="text-[10.5px] text-ink-faint mt-1.5 inline-block animate-pulse">
                        中文翻译生成中…
                      </span>
                    )}
                  </>
                )}
              </div>
            )}

            {topTags.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {topTags.map((t) => (
                  <span
                    key={t.tag}
                    className="chip bg-pearl text-ink-soft border border-hairline"
                    title={`权重 ${t.weight.toFixed(2)}`}
                  >
                    {t.tag}
                  </span>
                ))}
              </div>
            )}

            {cleanTopics.length > 0 && (
              <div className="flex flex-wrap gap-x-2.5 gap-y-1">
                {cleanTopics.map((t) => (
                  <span key={t} className="text-[11.5px] text-ink-faint">
                    #{t}
                  </span>
                ))}
              </div>
            )}

            {/* README 摘要 —— 放在散文列（左），与简介/标签同属"读"的内容；
                右侧专留数据（概览+画像），两列高度更均衡，不再留大片空白。
                完整 README（markdown 渲染）在抽屉里。 */}
            {item.readme_excerpt && (
              <div className="border-t border-divider pt-3">
                <div className="flex items-center gap-2 mb-2">
                  <span className="text-[11px] text-ink-faint">README 摘要</span>
                  <div className="flex-1" />
                  <button
                    className="text-[11px] text-ink-muted hover:text-accent transition-colors"
                    onClick={() => onOpenDetail(item)}
                  >
                    读全文 ↗
                  </button>
                </div>
                <p className="text-[12.5px] leading-[1.75] text-ink-muted whitespace-pre-wrap">
                  {item.readme_excerpt}
                </p>
              </div>
            )}

            <div className="flex lg:hidden items-center gap-1.5 flex-wrap">
              <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                ⭐ {formatStars(item.stars)}
              </span>
              {lang && (
                <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                  📓 {lang}
                </span>
              )}
              <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                🎯 {(item.personal_score ?? item.score).toFixed(2)}
              </span>
              {lic && <span className={`chip h-6 border ${lic.cls}`}>{lic.text}</span>}
            </div>
          </div>

          {/* ── 右列（桌面）：结构化数据面板 ──
               ⚠️ 不依赖 README：扩库进来的仓库大多是"描述兜底"的伪 README，
                 靠它填版面会塌陷（实测过：右列空白 → 中间又出现空洞）。
                 这里用已在库里的结构化数据：概览 + 推荐系统打分明细。 */}
          <div className="hidden lg:flex w-[38%] shrink-0 flex-col gap-4 min-h-0
                          border-l border-divider pl-6 overflow-y-auto">
            <div>
              <div className="text-[11px] text-ink-faint mb-2">仓库概览</div>
              <div className="grid grid-cols-3 gap-2">
                <StatBox label="Star" value={formatStars(item.stars)} />
                <StatBox label="Fork" value={formatStars(item.stats?.forks ?? 0)} />
                <StatBox label="Issue" value={String(item.stats?.open_issues ?? 0)} />
                <StatBox
                  label="体积"
                  // ⚠️ 不再 Math.max(1, …)：缺数据时凭空显示"1 MB"是撒谎，交给 formatSize 显示 —
                  value={item.stats ? formatSize(item.stats.size_kb) : '—'}
                />
                <StatBox
                  label="创建"
                  value={item.stats?.created_at ? timeAgo(item.stats.created_at) : '—'}
                />
                <StatBox
                  label="推送"
                  value={item.stats?.pushed_at ? timeAgo(item.stats.pushed_at) : '—'}
                />
              </div>
            </div>

            {item.stats && (
              <div>
                <div className="text-[11px] text-ink-faint mb-2">仓库画像（客观指标）</div>
                <ScoreBar label="质量分" v={item.stats.quality} />
                <ScoreBar label="活跃度" v={item.stats.velocity} />
                <ScoreBar label="新鲜度" v={item.stats.freshness} />
                <ScoreBar label="遗珠分" v={item.stats.forgotten} tone="gem" />
                {/* ⚠️ 口径说明：用户实测反馈过"左边 🎯1.00、右边质量分 76 很诡异"——
                    两个数根本不是一回事，必须写清楚，否则看起来像自相矛盾 */}
                <p className="text-[10.5px] text-ink-faint mt-2 leading-relaxed">
                  左上角的 🎯 是
                  <span className="text-ink-soft">你的个人模型</span>
                  给的匹配度（模型判断你会不会喜欢）；这四项是
                  <span className="text-ink-soft">仓库自身</span>
                  的客观指标。口径不同，不必互相对齐。
                </p>
              </div>
            )}

          </div>
        </div>

        {/* ── 底部：操作 ──
             用户要求三个按钮**紧挨着放最右边**：查看详情在左，收藏/不感兴趣在右。
             （之前收藏在最左、详情在最右，隔了一整屏，视线要来回跳） */}
        <div className="flex items-center justify-end gap-2 pt-3 border-t border-divider">
          <button
            className="btn-primary h-9 px-5 text-[13px]"
            onClick={() => onOpenDetail(item)}
          >
            查看详情 ›
          </button>
          <button
            className={`h-9 w-9 shrink-0 rounded-full flex items-center justify-center
                       border transition-colors active:scale-95 ${
                         liked
                           ? 'bg-accent/[0.08] border-accent/30'
                           : 'bg-pearl border-hairline hover:bg-parchment'
                       }`}
            onClick={() => void toggleLike()}
            title={liked ? '取消收藏' : '收藏'}
            aria-label={liked ? '取消收藏' : '收藏'}
          >
            {liked ? '💙' : '🤍'}
          </button>
          <button
            className="h-9 w-9 shrink-0 rounded-full flex items-center justify-center
                       bg-pearl border border-hairline text-ink-muted
                       hover:text-risk-danger transition-colors active:scale-95"
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


/** 右列概览小方块 */
const StatBox: FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="rounded-pearl bg-parchment border border-hairline px-2.5 py-2 min-w-0">
    <div className="text-[13px] font-semibold text-ink leading-none truncate">
      {value}
    </div>
    <div className="text-[9.5px] text-ink-faint mt-1.5 leading-none">{label}</div>
  </div>
)

/** 右列打分条（0~1） */
const ScoreBar: FC<{ label: string; v: number; tone?: 'accent' | 'gem' }> = ({
  label,
  v,
  tone = 'accent',
}) => (
  <div className="flex items-center gap-2 mb-1.5">
    <span className="text-[11px] text-ink-muted w-14 shrink-0">{label}</span>
    <span className="flex-1 h-1.5 rounded-full bg-parchment overflow-hidden">
      <span
        className={`block h-full rounded-full ${tone === 'gem' ? 'bg-gem' : 'bg-accent'}`}
        style={{ width: `${Math.round(Math.max(0, Math.min(1, v)) * 100)}%` }}
      />
    </span>
    <span className="text-[11px] text-ink-faint w-7 text-right shrink-0">
      {Math.round(v * 100)}
    </span>
  </div>
)

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

export default memo(FeedCard)