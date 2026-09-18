import type { FC } from 'react'
import type { RepoBrief } from '@/types/api'

interface Props {
  /** 已生成的速读（null 表示还没生成） */
  brief: RepoBrief | null
  loading: boolean
  error: string | null
  onGenerate: () => void
}

/**
 * AI 项目速读 —— README 太长，先给用户一份「一眼看懂」。
 *
 * 用户要求："AI 可以把这个项目的技术栈、MD 说了什么，弄一个给用户看一下，
 *            先了解一下这个项目"。
 * 结构固定：一句话定位 → 技术栈 → 核心亮点（优先取自 FAQ/Features/Quick Start）
 *          → 适合谁 → 成熟度。亮点要求有信息量，不写"支持多种功能"这种空话。
 *
 * 按需生成（点按钮才跑 LLM）＋服务端缓存（README 变了才重新生成）。
 */
const BriefView: FC<Props> = ({ brief, loading, error, onGenerate }) => {
  if (loading) {
    return (
      <div className="rounded-pearl border border-hairline bg-parchment px-4 py-3.5">
        <div className="text-[11px] text-ink-faint mb-2">AI 速读</div>
        <div className="text-[13px] text-ink-faint animate-pulse">
          正在读 README 并提炼要点…（约 10 秒）
        </div>
      </div>
    )
  }

  if (!brief) {
    return (
      <div className="rounded-pearl border border-hairline bg-parchment px-4 py-3.5">
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-ink-faint">AI 速读</span>
          <div className="flex-1" />
          <button className="btn-ghost !h-7 !px-3 text-[11.5px]" onClick={onGenerate}>
            生成 AI 速读
          </button>
        </div>
        <p className="text-[11.5px] text-ink-faint mt-2 leading-relaxed">
          {error || '让 AI 读完这份 README，给你一句话定位、技术栈与核心亮点 —— 10 秒判断值不值得深入。'}
        </p>
      </div>
    )
  }

  return (
    <div className="rounded-pearl border border-hairline bg-parchment px-4 py-3.5">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-[11px] text-accent">✦ AI 速读</span>
        <div className="flex-1" />
        <button
          className="text-[10.5px] text-ink-faint hover:text-ink-muted transition-colors"
          onClick={onGenerate}
          title="README 更新后可重新生成"
        >
          重新生成
        </button>
      </div>

      {/* 一句话定位 */}
      <p className="text-[14.5px] leading-[1.5] text-ink font-medium">{brief.one_liner}</p>

      {/* 技术栈 */}
      {brief.stack.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2.5">
          {brief.stack.map((s) => (
            <span
              key={s}
              className="chip h-6 bg-canvas text-ink-soft border border-hairline"
            >
              {s}
            </span>
          ))}
        </div>
      )}

      {/* 核心亮点 */}
      {brief.highlights.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {brief.highlights.map((h, i) => (
            <li key={i} className="flex gap-2 text-[12.5px] leading-[1.65] text-ink-soft">
              <span className="text-accent shrink-0 select-none">·</span>
              <span>{h}</span>
            </li>
          ))}
        </ul>
      )}

      {/* 适合谁 + 成熟度 */}
      {(brief.audience || brief.maturity) && (
        <div className="mt-3 pt-2.5 border-t border-hairline space-y-1">
          {brief.audience && (
            <p className="text-[11.5px] text-ink-muted leading-relaxed">
              <span className="text-ink-faint">适合</span> {brief.audience}
            </p>
          )}
          {brief.maturity && (
            <p className="text-[11.5px] text-ink-muted leading-relaxed">
              <span className="text-ink-faint">成熟度</span> {brief.maturity}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

export default BriefView
