import { useEffect, useState } from 'react'
import type { FC } from 'react'
import type { RepoDetail, TranslateResponse } from '@/types/api'
import { getRepo, starRepo, translateRepo } from '@/lib/api'
import {
  formatStars,
  isForgotten,
  langColor,
  licenseBadge,
  pct,
  poolBadge,
  stripMarkdown,
  timeAgo,
} from '@/lib/format'
import { trackStar } from '@/lib/events'

interface Props {
  repoId: number | null
  userId: number
  onClose: () => void
}

/**
 * 仓库详情抽屉（底部上滑）—— 白色瓷面 + 发丝线分区。
 *
 * 展示的是「后端已经算好的数据」—— 包括流量池等级、各项转化率。
 * 这是本项目的透明度设计：让用户看到这个仓库在推荐系统里的处境。
 */
const DetailSheet: FC<Props> = ({ repoId, userId, onClose }) => {
  const [data, setData] = useState<RepoDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [starred, setStarred] = useState(false)

  // ---- 翻译状态（打开详情后自动请求，缓存命中瞬时返回）
  const [tr, setTr] = useState<TranslateResponse | null>(null)
  const [trLoading, setTrLoading] = useState(false)
  const [trErr, setTrErr] = useState<string | null>(null)
  const [showOriginal, setShowOriginal] = useState(false)

  useEffect(() => {
    if (repoId === null) {
      setData(null)
      setTr(null)
      setTrErr(null)
      setTrLoading(false)
      setShowOriginal(false)
      return
    }
    let alive = true
    setLoading(true)
    setErr(null)
    getRepo(repoId, userId, true)
      .then((d) => {
        if (!alive) return
        setData(d)
        setStarred(d.starred)
      })
      .catch((e: unknown) => {
        if (!alive) return
        setErr(e instanceof Error ? e.message : '加载失败')
      })
      .finally(() => {
        if (alive) setLoading(false)
      })

    // 自动翻译（后台进行，原文照常显示；失败静默降级为小字提示）
    setTr(null)
    setTrErr(null)
    setTrLoading(true)
    setShowOriginal(false)
    translateRepo(repoId)
      .then((t) => {
        if (!alive) return
        setTr(t)
      })
      .catch((e: unknown) => {
        if (!alive) return
        const msg = e instanceof Error ? e.message : '翻译失败'
        setTrErr(msg)
      })
      .finally(() => {
        if (alive) setTrLoading(false)
      })

    return () => {
      alive = false
    }
  }, [repoId, userId])

  const doStar = async (): Promise<void> => {
    if (!data || starred) return
    setStarred(true)
    trackStar(data.id)
    try {
      await starRepo(data.id, userId)
    } catch {
      setStarred(false)
    }
  }

  if (repoId === null) return null

  const lic = data ? licenseBadge(data.license_spdx, data.license_risk) : null
  const pool = poolBadge(data?.current_pool ?? 0)
  const readme = data?.readme_md ? stripMarkdown(data.readme_md) : ''

  return (
    <div className="absolute inset-0 z-40 flex flex-col justify-end">
      {/* 遮罩 */}
      <button
        className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
        onClick={onClose}
        aria-label="关闭"
      />

      <div className="relative bg-canvas border-t border-hairline rounded-t-card max-h-[88%] flex flex-col animate-slide-up">
        {/* 把手 */}
        <div className="shrink-0 pt-2.5 pb-1 flex justify-center">
          <div className="h-1 w-10 rounded-full bg-divider" />
        </div>

        <div className="flex-1 min-h-0 overflow-y-auto px-5 pb-6 safe-b">
          {loading && (
            <div className="py-10 space-y-3">
              <div className="skeleton h-5 w-48" />
              <div className="skeleton h-3 w-full" />
              <div className="skeleton h-3 w-[80%]" />
            </div>
          )}

          {err && (
            <div className="py-10 text-center text-[13px] text-risk-danger">
              {err}
            </div>
          )}

          {data && !loading && (
            <>
              {/* 标题 */}
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="text-[19px] font-semibold text-ink tracking-[-0.24px] leading-tight break-all">
                    {data.name}
                  </h2>
                  <p className="text-[12.5px] text-ink-muted mt-1">
                    {data.owner}
                  </p>
                </div>
                {isForgotten(data.forgotten_score) && (
                  <span className="chip border border-gem/25 bg-gem/[0.08] text-gem shrink-0">
                    💎 遗珠
                  </span>
                )}
              </div>

              {data.description && (
                <div className="mt-3">
                  {tr?.zh_description && !showOriginal ? (
                    <>
                      <p className="text-[13.5px] text-ink leading-[1.47]">
                        {tr.zh_description}
                      </p>
                      <p className="text-[12px] text-ink-faint mt-1.5 leading-relaxed">
                        {data.description}
                      </p>
                    </>
                  ) : (
                    <p className="text-[13.5px] text-ink-soft leading-[1.47]">
                      {data.description}
                    </p>
                  )}
                </div>
              )}

              {/* 操作按钮：蓝胶囊主 CTA + 次动作 */}
              <div className="flex gap-2 mt-4">
                <a
                  className="btn-primary flex-1"
                  href={data.url}
                  target="_blank"
                  rel="noreferrer noopener"
                >
                  打开 GitHub
                </a>
                <button
                  className={starred ? 'btn-gem' : 'btn-ghost'}
                  onClick={() => void doStar()}
                  disabled={starred}
                >
                  {starred ? '★ 已收藏' : '☆ 收藏'}
                </button>
              </div>

              {/* 基础指标 */}
              <Section title="仓库概况">
                <div className="grid grid-cols-3 gap-2">
                  <Stat label="Star" value={formatStars(data.stars)} />
                  <Stat label="Fork" value={formatStars(data.forks)} />
                  <Stat
                    label="语言"
                    value={data.language ?? '—'}
                    dot={data.language ? langColor(data.language) : undefined}
                  />
                  <Stat label="Issue" value={String(data.open_issues)} />
                  <Stat
                    label="体积"
                    value={
                      data.size_kb ? `${(data.size_kb / 1024).toFixed(1)} MB` : '—'
                    }
                  />
                  <Stat label="创建" value={timeAgo(data.created_at_gh)} />
                </div>
                <div className="flex gap-2 mt-2.5 flex-wrap">
                  <span className={`chip border ${pool.cls}`}>{pool.name}</span>
                  {lic && (
                    <span className={`chip border ${lic.cls}`}>{lic.text}</span>
                  )}
                  {!!data.has_ci && (
                    <span className="chip bg-accent/[0.08] text-accent border border-accent/20">
                      CI
                    </span>
                  )}
                  {!!data.has_tests && (
                    <span className="chip bg-risk-safe/[0.08] text-risk-safe border border-risk-safe/25">
                      测试
                    </span>
                  )}
                </div>
              </Section>

              {/* 推荐系统视角 —— 本项目特色，把算法透明度摆出来 */}
              <Section title="推荐系统怎么看你">
                <div className="space-y-3">
                  <RateBar label="质量分" v={data.quality_score} />
                  <RateBar label="活跃度" v={data.velocity_score} />
                  <RateBar label="新鲜度" v={data.freshness_score} />
                  <RateBar label="遗珠分" v={data.forgotten_score} gold />
                </div>
                <div className="grid grid-cols-3 gap-2 mt-3.5">
                  <Stat label="曝光" value={String(data.impressions ?? 0)} />
                  <Stat label="深读率" value={`${pct(data.deep_rate)}%`} />
                  <Stat label="点击率" value={`${pct(data.ctr)}%`} />
                </div>
                {(data.impressions ?? 0) < 20 && (
                  <p className="text-[11.5px] text-ink-faint mt-2.5 leading-relaxed">
                    曝光较少，推荐系统正在给它更多机会。
                  </p>
                )}
              </Section>

              {/* README（自动翻译，可切换原文） */}
              {readme && (
                <Section
                  title={
                    tr?.zh_readme && !showOriginal
                      ? 'README 摘要（中文）'
                      : 'README 摘要'
                  }
                  action={
                    tr?.zh_readme ? (
                      <button
                        className="text-[11px] text-accent border border-accent/25 bg-accent/[0.08] rounded-full px-2.5 py-0.5 active:opacity-60"
                        onClick={() => setShowOriginal((v) => !v)}
                      >
                        {showOriginal ? '看中文' : '看原文'}
                      </button>
                    ) : trLoading ? (
                      <span className="text-[11px] text-ink-faint">
                        翻译中…
                      </span>
                    ) : undefined
                  }
                >
                  <pre className="text-[12.5px] leading-relaxed text-ink-soft whitespace-pre-wrap font-sans">
                    {(tr?.zh_readme && !showOriginal
                      ? tr.zh_readme
                      : readme
                    ).slice(0, 1600)}
                    {((tr?.zh_readme && !showOriginal
                      ? tr.zh_readme
                      : readme
                    ).length ?? 0) > 1600
                      ? '\n\n…'
                      : ''}
                  </pre>
                  {tr?.readme_truncated && !showOriginal && (
                    <p className="text-[11px] text-ink-faint mt-2">
                      （篇幅所限，仅翻译开头部分；完整内容请看 GitHub）
                    </p>
                  )}
                  {trErr && !tr && (
                    <p className="text-[11px] text-ink-faint mt-2">
                      中文翻译暂不可用：{trErr}
                    </p>
                  )}
                </Section>
              )}

              {/* 标签 */}
              {data.tags_json && (
                <Section title="技术标签">
                  <div className="flex flex-wrap gap-1.5">
                    {(
                      Object.entries(
                        JSON.parse(data.tags_json) as Record<string, number>,
                      ) as [string, number][]
                    )
                      .sort((a, b) => b[1] - a[1])
                      .slice(0, 16)
                      .map(([tagName, w]) => (
                        <span
                          key={tagName}
                          className="chip bg-pearl text-ink-soft border border-hairline"
                        >
                          {tagName}
                          <span className="text-ink-faint ml-1.5">
                            {w.toFixed(2)}
                          </span>
                        </span>
                      ))}
                  </div>
                </Section>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

const Section: FC<{
  title: string
  children: React.ReactNode
  action?: React.ReactNode
}> = ({ title, children, action }) => (
  <div className="mt-5">
    <div className="flex items-center justify-between mb-2.5">
      <h3 className="text-[12px] font-semibold text-ink-muted tracking-wide">
        {title}
      </h3>
      {action}
    </div>
    {children}
  </div>
)

const Stat: FC<{ label: string; value: string; dot?: string }> = ({
  label,
  value,
  dot,
}) => (
  <div className="bg-canvas border border-hairline rounded-pearl px-2.5 py-2">
    <div className="flex items-center gap-1.5">
      {dot && (
        <span
          className="h-2 w-2 rounded-full shrink-0"
          style={{ background: dot }}
        />
      )}
      <span className="text-[13px] font-semibold text-ink leading-none truncate">
        {value}
      </span>
    </div>
    <div className="text-[10px] text-ink-muted mt-1.5 leading-none">
      {label}
    </div>
  </div>
)

const RateBar: FC<{ label: string; v: number; gold?: boolean }> = ({
  label,
  v,
  gold,
}) => {
  const p = pct(v)
  return (
    <div className="flex items-center gap-3">
      <span className="text-[11.5px] text-ink-muted w-14 shrink-0">
        {label}
      </span>
      <div className="flex-1 h-1.5 rounded-full bg-divider overflow-hidden">
        <div
          className={`h-full rounded-full ${gold ? 'bg-gem' : 'bg-accent'}`}
          style={{ width: `${p}%` }}
        />
      </div>
      <span className="text-[11px] text-ink-muted w-8 text-right shrink-0">
        {p}
      </span>
    </div>
  )
}

export default DetailSheet
