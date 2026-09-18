import { useCallback, useEffect, useState } from 'react'
import type { FC } from 'react'
import type { WeeklyReport as Report } from '@/types/api'
import type { WeeklyStats } from '@/types/api'
import { getWeeklyReport } from '@/lib/api'
import { shareReportCard } from '@/lib/shareCard'

interface Props {
  open: boolean
  userId: number
  onClose: () => void
}

/**
 * AI 周报：把这周的行为数据变成一份「发现总结 + 学习路线」。
 *
 * 为什么做（用户提的 P0）：竖滑卡片刷久了会累，也容易"滑过就忘"。
 * 周报是**回顾层** —— 既兑现"被发现的价值"，也是留存钩子。
 * 所有事实（曝光/深读/收藏/项目清单）由后端 SQL 算好，LLM 只负责写成话，不编项目。
 */
const WeeklyReport: FC<Props> = ({ open, userId, onClose }) => {
  const [report, setReport] = useState<Report | null>(null)
  const [stats, setStats] = useState<WeeklyStats | null>(null)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [shareMsg, setShareMsg] = useState<string | null>(null)

  const load = useCallback(
    async (force = false): Promise<void> => {
      setLoading(true)
      setErr(null)
      try {
        const r = await getWeeklyReport(userId, 7, force)
        if (r.ok && r.report) {
          setReport(r.report)
          setStats(r.stats ?? null)
        } else {
          setErr(r.reason || '生成失败')
        }
      } catch (e) {
        setErr(e instanceof Error ? e.message : '生成失败')
      } finally {
        setLoading(false)
      }
    },
    [userId],
  )

  useEffect(() => {
    if (open && !report) void load(false)
  }, [open, report, load])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const share = async (): Promise<void> => {
    if (!report) return
    setShareMsg('生成中…')
    const r = await shareReportCard(report, stats)
    setShareMsg(
      r === 'clipboard' ? '✓ 已复制到剪贴板' : r === 'download' ? '✓ 已下载报告图' : '生成失败',
    )
    setTimeout(() => setShareMsg(null), 3200)
  }

  return (
    <div className="absolute inset-0 z-50 flex items-end sm:items-center justify-center">
      <button
        className="absolute inset-0 bg-black/40 backdrop-blur-[2px]"
        onClick={onClose}
        aria-label="关闭"
      />
      <div className="relative w-full sm:max-w-[560px] max-h-[88%] bg-canvas
                      rounded-t-[18px] sm:rounded-[18px] flex flex-col overflow-hidden">
        {/* 头 */}
        <div className="h-[52px] shrink-0 px-5 flex items-center gap-2 border-b border-divider">
          <span className="text-[15px] font-semibold text-ink tracking-[-0.3px]">
            ✦ 本周发现报告
          </span>
          {report && (
            <span className="text-[10.5px] text-ink-faint">AI 生成 · 近 7 天</span>
          )}
          <div className="flex-1" />
          <button
            className="text-[11.5px] text-ink-muted hover:text-ink transition-colors"
            onClick={() => void load(true)}
            disabled={loading}
          >
            {loading ? '生成中…' : '重新生成'}
          </button>
          <button
            className="h-8 w-8 rounded-full flex items-center justify-center bg-pearl
                       border border-hairline text-ink-soft"
            onClick={onClose}
            aria-label="关闭"
          >
            ✕
          </button>
        </div>

        {/* 体 */}
        <div className="flex-1 min-h-0 overflow-y-auto px-5 py-4">
          {loading && !report && (
            <div className="py-10 text-center">
              <div className="text-[13px] text-ink-muted animate-pulse">
                AI 正在读你这周的行为数据…
              </div>
            </div>
          )}

          {err && !report && (
            <div className="py-8 text-center text-[13px] text-ink-muted">{err}</div>
          )}

          {report && (
            <>
              {/* 统计行 */}
              {stats && (
                <div className="grid grid-cols-4 gap-2">
                  {[
                    ['刷过', stats.impressions],
                    ['深读', stats.deep_reads],
                    ['点开', stats.clicks],
                    ['收藏', stats.likes],
                  ].map(([label, v]) => (
                    <div
                      key={String(label)}
                      className="rounded-pearl bg-parchment border border-hairline px-2.5 py-2"
                    >
                      <div className="text-[17px] font-semibold text-ink leading-none">{v}</div>
                      <div className="text-[9.5px] text-ink-faint mt-1.5 leading-none">
                        {label}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 标题 */}
              <h2 className="text-[19px] font-semibold text-ink leading-[1.45] tracking-[-0.4px] mt-4">
                {report.headline}
              </h2>
              {report.stats_comment && (
                <p className="text-[12.5px] text-ink-muted leading-[1.7] mt-2">
                  {report.stats_comment}
                </p>
              )}

              {/* 本周宝藏 */}
              {report.highlights.length > 0 && (
                <>
                  <div className="text-[11px] text-ink-faint mt-5 mb-2">本周宝藏</div>
                  <div className="space-y-2.5">
                    {report.highlights.map((h) => (
                      <div
                        key={h.repo}
                        className="rounded-pearl border border-hairline px-3.5 py-3"
                      >
                        <div className="text-[13.5px] font-medium text-ink truncate">
                          {h.repo}
                        </div>
                        <p className="text-[12px] text-ink-muted leading-[1.65] mt-1">
                          {h.why}
                        </p>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {/* 学习路线 */}
              {report.learning_path.length > 0 && (
                <>
                  <div className="text-[11px] text-ink-faint mt-5 mb-2">给你的学习路线</div>
                  <ol className="space-y-2">
                    {report.learning_path.map((x, i) => (
                      <li key={i} className="flex gap-2.5 text-[12.5px] leading-[1.7] text-ink-soft">
                        <span className="shrink-0 w-4 h-4 mt-[3px] rounded-full bg-accent/[0.1]
                                         text-accent text-[10px] flex items-center justify-center">
                          {i + 1}
                        </span>
                        <span>{x}</span>
                      </li>
                    ))}
                  </ol>
                </>
              )}

              {report.next_step && (
                <div className="mt-5 pt-4 border-t border-divider">
                  <p className="text-[12.5px] text-ink-soft leading-relaxed">
                    <span className="text-ink-faint">下周看点：</span>
                    {report.next_step}
                  </p>
                </div>
              )}
            </>
          )}
        </div>

        {/* 底 */}
        {report && (
          <div className="shrink-0 px-5 py-3 border-t border-divider flex items-center gap-2">
            <button className="btn-primary h-9 px-4 text-[13px]" onClick={() => void share()}>
              分享报告图 ↗
            </button>
            <div className="flex-1" />
            {shareMsg && <span className="text-[11.5px] text-accent">{shareMsg}</span>}
            <span className="text-[10.5px] text-ink-faint">AI 周报 · 数据不出本机</span>
          </div>
        )}
      </div>
    </div>
  )
}

export default WeeklyReport
