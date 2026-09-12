import { useEffect, useState } from 'react'
import type { FC } from 'react'
import type { RepoDetail } from '@/types/api'
import { getRepo, starRepo } from '@/lib/api'
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
 * 仓库详情抽屉（底部上滑）。
 *
 * 展示的是「后端已经算好的数据」—— 包括流量池等级、各项转化率。
 * 这是本项目的透明度设计：让用户看到这个仓库在推荐系统里的处境。
 */
const DetailSheet: FC<Props> = ({ repoId, userId, onClose }) => {
  const [data, setData] = useState<RepoDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [starred, setStarred] = useState(false)

  useEffect(() => {
    if (repoId === null) {
      setData(null)
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
        className="absolute inset-0 bg-black/65 backdrop-blur-[2px]"
        onClick={onClose}
        aria-label="关闭"
      />

      <div className="relative glass rounded-t-3xl max-h-[88%] flex flex-col animate-slide-up">
        {/* 把手 */}
        <div className="shrink-0 pt-2.5 pb-1 flex justify-center">
          <div className="h-1 w-10 rounded-full bg-white/20" />
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
            <div className="py-10 text-center text-[13px] text-risk-danger/80">
              {err}
            </div>
          )}

          {data && !loading && (
            <>
              {/* 标题 */}
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <h2 className="text-[19px] font-bold text-white leading-tight break-all">
                    {data.name}
                  </h2>
                  <p className="text-[12.5px] text-white/45 mt-1">
                    {data.owner}
                  </p>
                </div>
                {isForgotten(data.forgotten_score) && (
                  <span className="chip border border-gem/45 bg-gem/15 text-gem shrink-0">
                    💎 遗珠
                  </span>
                )}
              </div>

              {data.description && (
                <p className="text-[13.5px] text-white/72 mt-3 leading-relaxed">
                  {data.description}
                </p>
              )}

              {/* 操作按钮 */}
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
                    <span className="chip bg-accent/10 text-accent-glow/85 border border-accent/20">
                      CI
                    </span>
                  )}
                  {!!data.has_tests && (
                    <span className="chip bg-risk-safe/10 text-risk-safe border border-risk-safe/25">
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
                  <p className="text-[11.5px] text-white/35 mt-2.5 leading-relaxed">
                    曝光较少，推荐系统正在给它更多机会。
                  </p>
                )}
              </Section>

              {/* README */}
              {readme && (
                <Section title="README 摘要">
                  <pre className="text-[12.5px] leading-relaxed text-white/60 whitespace-pre-wrap font-sans">
                    {readme.slice(0, 1600)}
                    {readme.length > 1600 ? '\n\n…' : ''}
                  </pre>
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
                          className="chip bg-white/[0.05] text-white/55 border border-white/[0.08]"
                        >
                          {tagName}
                          <span className="text-white/25 ml-1.5">
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

const Section: FC<{ title: string; children: React.ReactNode }> = ({
  title,
  children,
}) => (
  <div className="mt-5">
    <h3 className="text-[12px] font-semibold text-white/40 mb-2.5 tracking-wide">
      {title}
    </h3>
    {children}
  </div>
)

const Stat: FC<{ label: string; value: string; dot?: string }> = ({
  label,
  value,
  dot,
}) => (
  <div className="glass-soft rounded-xl px-2.5 py-2">
    <div className="flex items-center gap-1.5">
      {dot && (
        <span
          className="h-2 w-2 rounded-full shrink-0"
          style={{ background: dot }}
        />
      )}
      <span className="text-[13px] font-semibold text-white/88 leading-none truncate">
        {value}
      </span>
    </div>
    <div className="text-[10px] text-white/35 mt-1.5 leading-none">
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
      <span className="text-[11.5px] text-white/45 w-14 shrink-0">
        {label}
      </span>
      <div className="flex-1 h-1.5 rounded-full bg-white/[0.07] overflow-hidden">
        <div
          className={`h-full rounded-full ${gold ? 'bg-gem' : 'bg-accent'}`}
          style={{ width: `${p}%` }}
        />
      </div>
      <span className="text-[11px] text-white/40 w-8 text-right shrink-0">
        {p}
      </span>
    </div>
  )
}

export default DetailSheet
