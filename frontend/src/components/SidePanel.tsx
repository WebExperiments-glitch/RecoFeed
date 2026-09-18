import { useEffect, useState } from 'react'
import type { FC } from 'react'
import type { FeedItem, RepoBrief, RepoDetail } from '@/types/api'
import { explainCards, getBrief, getRepo, starRepo } from '@/lib/api'
import { trackStar } from '@/lib/events'
import { formatStars, timeAgo } from '@/lib/format'
import { downloadShareCard } from '@/lib/shareCard'
import ReadmeView from '@/components/ReadmeView'
import BriefView from '@/components/BriefView'

interface Props {
  /** 是否滑出 */
  open: boolean
  /** 当前正在看的卡片（由 FeedList 的 activeIndex 上报） */
  item: FeedItem | null
  userId: number
  onClose: () => void
  onOpenDetail: (item: FeedItem) => void
  onDislike: (item: FeedItem) => void
}

/**
 * 侧滑抽屉：承载当前卡片的完整信息（README 全文 / AI 理由 / 指标 / 操作）。
 *
 * ⭐ 为什么是抽屉而不是常驻分栏
 *   常驻右栏会挤掉信息流的中心感（用户原话："这像个啥的抖音"）。
 *   抽屉按需滑出 —— 只想刷的时候屏幕是干净的一列，想看细节再划出来。
 *   移动端同样可用（<sm 占满整屏）。
 */
const SidePanel: FC<Props> = ({
  open,
  item,
  userId,
  onClose,
  onOpenDetail,
  onDislike,
}) => {
  const [detail, setDetail] = useState<RepoDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [explain, setExplain] = useState<string | null>(null)
  const [starred, setStarred] = useState(false)
  // AI 速读（README → 结构化简介）：按需生成 + 服务端缓存
  const [brief, setBrief] = useState<RepoBrief | null>(null)
  const [briefLoading, setBriefLoading] = useState(false)
  const [briefErr, setBriefErr] = useState<string | null>(null)
  const [shareMsg, setShareMsg] = useState<string | null>(null)
  const repoId = item?.repo_id ?? null

  // 展开时才拉数据（含 README 全文），避免无谓请求
  useEffect(() => {
    if (!open || repoId == null) return
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
  }, [open, repoId, userId])

  // 卡片上若已带缓存的速读，直接显示（feed 只下发已生成的，不会触发 LLM）
  useEffect(() => {
    setBrief(item?.brief ?? null)
    setBriefErr(null)
  }, [item?.repo_id, item?.brief])

  // Esc 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent): void => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const generateBrief = async (): Promise<void> => {
    if (!item || briefLoading) return
    setBriefLoading(true)
    setBriefErr(null)
    try {
      const r = await getBrief(userId, [item.repo_id], true)
      const got = r.briefs?.[String(item.repo_id)]
      if (got?.brief) setBrief(got.brief)
      else setBriefErr(got?.reason || '生成失败，稍后再试')
    } catch (e) {
      setBriefErr(e instanceof Error ? e.message : '生成失败')
    } finally {
      setBriefLoading(false)
    }
  }

  const share = async (): Promise<void> => {
    if (!item) return
    setShareMsg('生成中…')
    try {
      const r = await downloadShareCard(item, {
        oneLiner: brief?.one_liner,
        highlights: brief?.highlights,
        stack: brief?.stack,
      })
      setShareMsg(
        r === 'clipboard' ? '✓ 已复制到剪贴板，去粘贴吧'
          : r === 'download' ? '✓ 已下载分享图' : '生成失败，请重试',
      )
    } catch {
      setShareMsg('生成失败，请重试')
    }
    setTimeout(() => setShareMsg(null), 3200)
  }

  return (
    <>
      {/* 遮罩（点击关闭） */}
      <div
        className={`fixed inset-0 z-40 bg-black/25 transition-opacity duration-200 ${
          open ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={onClose}
        aria-hidden
      />

      <aside
        className={`fixed inset-y-0 right-0 z-50 w-full sm:w-[520px] bg-canvas
                    border-l border-divider flex flex-col
                    transition-transform duration-300 ease-out ${
                      open ? 'translate-x-0' : 'translate-x-full'
                    }`}
        aria-hidden={!open}
      >
        {/* 抽屉头 */}
        <header className="h-[52px] shrink-0 flex items-center gap-2 px-5 border-b border-divider">
          <span className="text-[13px] text-ink-soft truncate">
            {item?.full_name ?? '项目详情'}
          </span>
          <div className="flex-1" />
          {item && (
            <a
              className="chip bg-pearl border border-hairline text-ink-soft shrink-0
                         hover:text-ink transition-colors"
              href={item.url}
              target="_blank"
              rel="noreferrer"
            >
              在 GitHub 打开 ↗
            </a>
          )}
          <button
            className="h-9 w-9 shrink-0 rounded-full flex items-center justify-center
                       bg-pearl border border-hairline text-ink-soft
                       hover:text-ink transition-colors active:scale-95"
            onClick={onClose}
            aria-label="关闭"
          >
            ✕
          </button>
        </header>

        {/* 抽屉体 */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {!item ? (
            <div className="p-6 text-[13px] text-ink-faint">没有选中的卡片</div>
          ) : (
            <div className="px-6 py-5">
              {/* 身份 */}
              <h1 className="text-[21px] font-semibold leading-tight text-ink tracking-[-0.5px]">
                {item.name}
              </h1>
              <p className="text-[12.5px] text-ink-muted mt-1">
                {item.owner}
                {item.pushed_at_gh ? ` · 更新于 ${timeAgo(item.pushed_at_gh)}` : ''}
              </p>

              {/* AI 理由 */}
              {explain && (
                <div className="mt-4 flex items-start gap-1.5">
                  <span className="text-[11px] text-accent shrink-0 mt-[2px]">💡 AI</span>
                  <span className="text-[13px] leading-[1.6] text-ink-soft">
                    {explain}
                  </span>
                </div>
              )}

              {/* 指标 */}
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
                  🎯 匹配度 {(item.personal_score ?? item.score).toFixed(2)}
                </span>
                {item.license_spdx && (
                  <span className="chip h-6 bg-pearl text-ink-muted border border-hairline">
                    {item.license_spdx}
                  </span>
                )}
              </div>

              {/* 标签 */}
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

              {/* AI 速读：先让用户"一眼看懂这个项目"，再决定要不要读全文 */}
              <BriefView
                brief={brief}
                loading={briefLoading}
                error={briefErr}
                onGenerate={() => void generateBrief()}
              />

              {/* README 全文 —— 按 markdown 正常排版（用户要求"完整的 MD 文件可以给用户看"）。
                  卡片只能回答"要不要点进来"，判断项目深度必须能看到完整 README。 */}
              <div className="mt-6 pt-5 border-t border-divider">
                <div className="flex items-center gap-2 mb-3">
                  <span className="text-[11px] text-ink-faint">README 全文</span>
                  {detail?.readme_md && (
                    <span className="text-[10.5px] text-ink-faint">
                      {detail.readme_md.length.toLocaleString()} 字
                    </span>
                  )}
                  <div className="flex-1" />
                  <a
                    className="text-[11px] text-ink-muted hover:text-accent transition-colors"
                    href={`${item.url}#readme`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    在 GitHub 看原文 ↗
                  </a>
                </div>
                {loading && <div className="text-[12.5px] text-ink-faint">读取中…</div>}
                {!loading && detail?.readme_md && <ReadmeView markdown={detail.readme_md} />}
                {!loading && !detail?.readme_md && (
                  <div className="text-[12.5px] text-ink-faint">这个仓库没有 README</div>
                )}
              </div>

              {/* 操作 */}
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
                <button className="btn-ghost" onClick={() => void share()}>
                  分享卡片 ↗
                </button>
                <button className="btn-ghost" onClick={() => onOpenDetail(item)}>
                  详情面板 ›
                </button>
                <div className="flex-1" />
                {shareMsg && (
                  <span className="text-[11.5px] text-accent">{shareMsg}</span>
                )}
                <button
                  className="text-[12.5px] text-ink-muted hover:text-risk-danger transition-colors"
                  onClick={() => {
                    onDislike(item)
                    onClose()
                  }}
                >
                  不感兴趣
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>
    </>
  )
}

export default SidePanel
