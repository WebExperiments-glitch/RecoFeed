import { useCallback, useEffect, useRef, useState } from 'react'
import type { FC } from 'react'
import type { SearchResultItem } from '@/types/api'
import { hotKeywords, searchRepos, suggestKeywords } from '@/lib/api'
import { formatStars, isForgotten, langColor } from '@/lib/format'

interface Props {
  userId: number
  open: boolean
  onClose: () => void
  onOpenDetail: (item: SearchResultItem) => void
}

type SortKey = 'relevance' | 'stars' | 'recent' | 'forgotten'

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'relevance', label: '相关' },
  { key: 'forgotten', label: '遗珠' },
  { key: 'stars', label: '星数' },
  { key: 'recent', label: '新近' },
]

const PAGE = 12

/**
 * 搜索面板。
 *
 * 交互设计参考抖音搜索：
 *   · 打开即聚焦输入框，自动弹出热门词（冷启动指引）
 *   · 输入时 200ms 防抖拉联想词
 *   · 结果列表下拉分页（滚到底自动加载下一页）
 */
const SearchPanel: FC<Props> = ({ userId, open, onClose, onOpenDetail }) => {
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('relevance')
  const [results, setResults] = useState<SearchResultItem[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [searching, setSearching] = useState(false)
  const [suggests, setSuggests] = useState<string[]>([])
  const [hots, setHots] = useState<{ word: string; count: number }[]>([])
  const [error, setError] = useState<string | null>(null)

  const inputRef = useRef<HTMLInputElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)
  const offsetRef = useRef(0)

  // 打开时聚焦 + 拉热门词
  useEffect(() => {
    if (!open) return
    const t = window.setTimeout(() => inputRef.current?.focus(), 120)
    if (hots.length === 0) {
      hotKeywords()
        // ⚠️ 后端返回的是 {hot: [{word, count}]}，字段名是 hot 不是 items
        .then((r) => setHots((r.hot ?? []).slice(0, 14)))
        .catch(() => setHots([]))
    }
    return () => window.clearTimeout(t)
  }, [open, hots.length])

  // 联想词（防抖 200ms）
  useEffect(() => {
    const s = q.trim()
    if (s.length < 1) {
      setSuggests([])
      return
    }
    const t = window.setTimeout(() => {
      suggestKeywords(s)
        .then((r) => setSuggests(r.items.slice(0, 8)))
        .catch(() => setSuggests([]))
    }, 200)
    return () => window.clearTimeout(t)
  }, [q])

  const runSearch = useCallback(
    async (query: string, reset: boolean) => {
      const s = query.trim()
      if (!s) return
      setSearching(true)
      setError(null)
      const offset = reset ? 0 : offsetRef.current
      try {
        const res = await searchRepos({
          q: s,
          userId,
          limit: PAGE,
          offset,
          sort,
        })
        setResults((prev) =>
          reset ? res.items : [...prev, ...res.items],
        )
        setTotal(res.meta.total)
        setHasMore(res.meta.has_more)
        offsetRef.current = offset + res.items.length
      } catch (err) {
        setError(err instanceof Error ? err.message : '搜索失败')
      } finally {
        setSearching(false)
      }
    },
    [userId, sort],
  )

  // 切换排序时重搜
  useEffect(() => {
    if (!open) return
    const s = q.trim()
    if (s) void runSearch(s, true)
    // 只在排序变化时重搜，q 的变化由用户显式提交触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sort, open])

  const submit = (value?: string): void => {
    const s = (value ?? q).trim()
    if (!s) return
    if (value !== undefined) setQ(value)
    setSuggests([])
    offsetRef.current = 0
    void runSearch(s, true)
  }

  const onListScroll = (): void => {
    const el = listRef.current
    if (!el || !hasMore || searching) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 200) {
      void runSearch(q, false)
    }
  }

  if (!open) return null

  return (
    <div className="absolute inset-0 z-50 bg-ink-900/95 backdrop-blur-glass flex flex-col animate-slide-up">
      {/* ── 搜索框 ── */}
      <div className="safe-t shrink-0 px-4 pt-3 pb-2">
        <div className="flex items-center gap-2">
          <div className="glass-soft flex-1 flex items-center gap-2 rounded-full px-3.5 py-2">
            <SearchIcon />
            <input
              ref={inputRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') submit()
                if (e.key === 'Escape') onClose()
              }}
              placeholder="搜仓库、技术方向、语言…"
              className="flex-1 bg-transparent outline-none text-[14px] text-white
                         placeholder:text-white/30"
              autoComplete="off"
              spellCheck={false}
            />
            {q && (
              <button
                className="text-white/35 hover:text-white/70 text-[16px] leading-none"
                onClick={() => {
                  setQ('')
                  setResults([])
                  setSuggests([])
                }}
                aria-label="清空"
              >
                ×
              </button>
            )}
          </div>
          <button className="btn-ghost shrink-0" onClick={onClose}>
            取消
          </button>
        </div>

        {/* 排序 */}
        <div className="flex gap-1.5 mt-2.5">
          {SORTS.map((s) => (
            <button
              key={s.key}
              onClick={() => setSort(s.key)}
              className={`chip border transition-colors ${
                sort === s.key
                  ? 'bg-accent/20 border-accent/50 text-accent-glow'
                  : 'bg-white/[0.04] border-white/[0.08] text-white/45'
              }`}
            >
              {s.label}
            </button>
          ))}
          {total > 0 && (
            <span className="chip text-white/30 ml-auto">
              共 {total} 个结果
            </span>
          )}
        </div>
      </div>

      {/* ── 联想词 ── */}
      {suggests.length > 0 && (
        <div className="shrink-0 px-4 pb-2 flex flex-wrap gap-1.5">
          {suggests.map((s) => (
            <button
              key={s}
              className="chip bg-white/[0.05] text-white/60 border border-white/[0.08]
                         hover:bg-white/[0.1] hover:text-white/90 transition-colors"
              onClick={() => submit(s)}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* ── 结果 / 热门词 ── */}
      <div
        ref={listRef}
        onScroll={onListScroll}
        className="flex-1 min-h-0 overflow-y-auto px-4 pb-6"
      >
        {error && (
          <div className="text-center py-10 text-risk-danger/80 text-[13px]">
            {error}
          </div>
        )}

        {/* 未搜索时展示热门词 */}
        {results.length === 0 && !searching && !error && (
          <div className="pt-2">
            <p className="text-[12px] text-white/35 mb-2.5">大家在搜</p>
            <div className="flex flex-wrap gap-1.5">
              {hots.map((h) => (
                <button
                  key={h.word}
                  className="chip bg-white/[0.05] text-white/65 border border-white/[0.08]
                             hover:bg-white/[0.1] hover:text-white transition-colors"
                  onClick={() => submit(h.word)}
                >
                  {h.word}
                  <span className="text-white/25 ml-1.5">{h.count}</span>
                </button>
              ))}
              {hots.length === 0 && (
                <span className="text-[12px] text-white/25">
                  试试搜「rvc」「语音克隆」「量化」
                </span>
              )}
            </div>
          </div>
        )}

        {/* 结果列表 */}
        {results.map((r, i) => (
          <button
            key={`${r.repo_id}-${i}`}
            onClick={() => onOpenDetail(r)}
            className="w-full text-left glass-soft rounded-2xl p-3.5 mb-2.5
                       hover:bg-white/[0.07] transition-colors"
          >
            <div className="flex items-center gap-2">
              <span className="text-[14px] font-semibold text-white truncate">
                {r.owner}/
                <span className="text-accent-glow">{r.name}</span>
              </span>
              {isForgotten(r.forgotten_score) && (
                <span className="chip border border-gem/40 bg-gem/12 text-gem shrink-0">
                  💎
                </span>
              )}
              <span className="ml-auto text-[11px] text-white/40 shrink-0">
                ★ {formatStars(r.stars)}
              </span>
            </div>

            {r.description && (
              <p className="text-[12.5px] text-white/55 mt-1.5 line-clamp-2 leading-relaxed">
                {r.description}
              </p>
            )}

            <div className="flex items-center gap-1.5 mt-2 flex-wrap">
              {r.language && (
                <span className="chip text-white/45">
                  <span
                    className="h-1.5 w-1.5 rounded-full mr-1.5"
                    style={{ background: langColor(r.language) }}
                  />
                  {r.language}
                </span>
              )}
              {r.search_reasons.slice(0, 2).map((rs) => (
                <span
                  key={rs}
                  className="chip bg-accent/10 text-accent-glow/80 border border-accent/20"
                >
                  {rs}
                </span>
              ))}
            </div>
          </button>
        ))}

        {searching && (
          <div className="text-center py-6 text-[12px] text-white/30 animate-pulse">
            搜索中…
          </div>
        )}

        {!searching && results.length > 0 && !hasMore && (
          <div className="text-center py-6 text-[12px] text-white/22">
            没有更多了
          </div>
        )}
      </div>
    </div>
  )
}

const SearchIcon: FC = () => (
  <svg
    width="15"
    height="15"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.2"
    strokeLinecap="round"
    className="text-white/35 shrink-0"
  >
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.5-3.5" />
  </svg>
)

export default SearchPanel
