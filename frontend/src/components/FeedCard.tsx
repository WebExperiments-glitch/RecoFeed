import { memo, useEffect, useMemo, useRef, useState } from 'react'
import type { FC } from 'react'
import type { CardTranslation, FeedItem } from '@/types/api'
import {
  formatStars,
  hashHue,
  isForgotten,
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
  const scrollRef = useRef<HTMLDivElement | null>(null)
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
      className={`snap-card relative h-full w-full shrink-0 ${
        index % 2 === 0 ? 'bg-canvas' : 'bg-parchment'
      }`}
    >
      {/* ⚠️ overflow-hidden 必不可少。
          卡片内部有 truncate / 长描述 / 标签换行，
          任何一处在窄屏下算出超宽都会把整页撑出横向滚动条，
          表现为"右侧内容被裁掉"。 */}
      <div className="h-full w-full px-4 pt-14 pb-4 flex flex-col overflow-hidden">
        {/* ── 内容区：整块垂直居中 + 限宽 ──
             布局纪律（踩了三轮坑）：
               ❌ 只把正文区居中，「身份+理由」留在顶部 → 理由胶囊被孤立，两处空洞拆开内容
               ❌ 内容顶对齐 + 指标/操作钉在底部 → 内容与页脚之间出现"太平洋留白"
                  （尤其桌面矮宽窗口，1080×600 下一个空洞四五百像素）
               ✅ 现在：**所有内容（身份→理由→描述→摘要→标签→信息行→操作行）
                  作为一个整体**居中；间距统一 14px；内容限宽 560px 居中。
                  用 m-auto 而不是 justify-center：内容超高时 auto margin 归零，
                  顶部不会被裁（justify-center + overflow 会吃掉第一行）。 */}
        <div
          ref={scrollRef}
          onScroll={onScroll}
          className="flex-1 min-h-0 overflow-y-auto pr-1"
        >
          <div className="min-h-full flex flex-col">
            <div className="m-auto w-full max-w-[560px] flex flex-col gap-3.5">
              {/* ↑ max-w-[560px]：桌面端收窄内容区居中。
                  1080px 宽的行长会让眼睛频繁换行、阅读体验崩掉；
                  560px ≈ 中文 40 字/行。移动端 390px 不受影响。 */}
              {/* ── 仓库身份 ── */}
              <div className="flex items-start gap-3">
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
                    className="text-[20px] font-semibold leading-tight text-ink tracking-[-0.55px] truncate"
                    title={zhName ? item.name : undefined}
                  >
                    {zhName || item.name}
                  </h2>
                  <p className="text-[12px] text-ink-muted mt-1 truncate">
                    {zhName ? `${item.name} · ` : ''}
                    {item.owner}
                    {item.pushed_at_gh ? ` · 更新于 ${timeAgo(item.pushed_at_gh)}` : ''}
                  </p>
                </div>
                {/* 遗珠标记 —— 本项目的品牌符号（琥珀语义色，非交互） */}
                {gem && (
                  <span
                    className="chip border border-gem/25 bg-gem/[0.08] text-gem animate-gem-pulse shrink-0"
                    title="低曝光但口碑很好，可能被埋没了"
                  >
                    💎 遗珠
                  </span>
                )}
              </div>

              {/* ── 推荐理由（Action Blue 唯一交互色之外的说明性胶囊）── */}
              <div>
                <span className="chip bg-accent/[0.07] text-accent border border-accent/15">
                  {icon ? `${icon} ` : ''}
                  {item.reason}
                </span>
              </div>

        {/* ── 💡 一句话推荐理由（LLM 生成）──
             这是"小模型排序 + 大模型解释"分工里大模型的那一半：
             排序由本地 MLP 做（毫秒级、零成本），解释需要语言能力 → 交给 LLM。 */}
        {explanation && (
          <div className="flex items-start gap-1.5">
            <span className="text-[11px] text-accent shrink-0 mt-[1.5px]">💡 AI</span>
            <span className="text-[12.5px] leading-[1.55] text-ink-soft">
              {explanation}
            </span>
          </div>
        )}

            {item.description && (
              <div>
                {/* ⚠️ 只有"真译文"才单独占一行；否则原文只显示一次。
                    后端也已加校验（模型回吐原文时返回空），这里是前端兜底。 */}
                {zhDescReal ? (
                  <>
                    <p className="text-[15px] leading-[1.55] text-ink">
                      {zhDescReal}
                    </p>
                    <p className="text-[11.5px] leading-relaxed text-ink-faint mt-1">
                      {item.description}
                    </p>
                  </>
                ) : (
                  <>
                    <p className="text-[15px] leading-[1.55] text-ink-soft">
                      {item.description}
                    </p>
                    {!descAlreadyChinese && (
                      <span className="text-[10.5px] text-ink-faint mt-1 inline-block animate-pulse">
                        中文翻译生成中…
                      </span>
                    )}
                  </>
                )}
              </div>
            )}

            {/* README 摘要 —— 让卡片有正常的信息密度（否则满屏只有两行字，
                版面会显得空，读起来像"字隔得很远"）。
                弱化成正文的补充材料：更小字号、更浅颜色、发丝线隔开。 */}
            {item.readme_excerpt && (
              <div className="border-t border-divider pt-3">
                <div className="text-[10px] text-ink-faint mb-1.5">README 摘要</div>
                <p className="text-[12.5px] leading-[1.65] text-ink-muted line-clamp-[7]">
                  {item.readme_excerpt}
                </p>
              </div>
            )}

            {/* 标签 */}
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

            {/* topics —— 与算法标签去重后最多 4 个
                （之前 detection/face/mtcnn 在标签行和话题行各出现一次）*/}
            {cleanTopics.length > 0 && (
              <div className="flex flex-wrap gap-x-2.5 gap-y-1">
                {cleanTopics.map((t) => (
                  <span key={t} className="text-[11px] text-ink-faint">
                    #{t}
                  </span>
                ))}
              </div>
            )}

            {/* ── 紧凑信息行：⭐星数 · 语言 · 🎯匹配度 · 许可证 ──
                 之前是三个大矩形"地砖"占满一整行，还压不住版面；
                 改一行小胶囊，把空间留给内容。 */}
            <div className="flex items-center gap-1.5 flex-wrap">
              <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                ⭐ {formatStars(item.stars)}
              </span>
              {lang && (
                <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                  📓 {lang}
                </span>
              )}
              <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                🎯 {item.score.toFixed(2)}
              </span>
              {lic && (
                <span className={`chip h-6 border ${lic.cls}`}>{lic.text}</span>
              )}
              {item.license_risk === 'caution' && (
                <span className="text-[10.5px] text-risk-caution">商用需留意</span>
              )}
              {item.license_risk === 'danger' && (
                <span className="text-[10.5px] text-risk-danger">协议受限</span>
              )}
            </div>

            {/* ── 操作行：紧凑 pill + 圆形按钮，同一行，不占整宽 ──
                 刷卡的主线动作是"上下滑"，全宽蓝条会把视线硬拽下去；
                 缩成一行、次级操作靠右，主动权还给用户。 */}
            <div className="flex items-center gap-2 pt-3 border-t border-divider">
              <button
                className="btn-primary h-9 px-4 text-[13px]"
                onClick={() => onOpenDetail(item)}
              >
                查看详情 ›
              </button>
              <div className="flex-1" />
              <button
                className={`h-9 w-9 shrink-0 rounded-full flex items-center justify-center
                           border transition-colors active:scale-95 ${
                             liked
                               ? 'bg-accent/[0.08] border-accent/30'
                               : 'bg-pearl border-hairline hover:bg-parchment'
                           }`}
                onClick={() => void toggleLike()}
                title={liked ? '取消点赞' : '点赞'}
                aria-label={liked ? '取消点赞' : '点赞'}
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
          </div>
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

export default memo(FeedCard)
