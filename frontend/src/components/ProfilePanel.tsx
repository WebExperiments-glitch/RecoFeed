import { useCallback, useEffect, useState } from 'react'
import type { FC } from 'react'
import type {
  CustomKeyword,
  MeResponse,
  ProfileItem,
  QueueStats,
  SessionUser,
  UserProfile,
} from '@/types/api'
import {
  addCustomKeyword,
  crawlByKeywords,
  deleteCustomKeyword,
  getCustomKeywords,
  getMe,
  getQueueStats,
  getUserProfile,
  githubLoginUrl,
  rebuildUserProfile,
} from '@/lib/api'
import { getToken } from '@/lib/session'
import { formatStars, timeAgo } from '@/lib/format'
import { toast } from '@/components/Toast'

interface Props {
  userId: number
  user: SessionUser | null
  open: boolean
  onClose: () => void
  /** 同步 GitHub 实证数据（自建仓库 + 点星仓库） */
  onSync: () => void
  onLogout: () => void
}

/**
 * 我的画像面板 —— 浅色满屏浮层，白色工具卡 + 发丝线。
 *
 * 这是本项目的第二个透明度设计：
 *   把「推荐系统以为你喜欢什么」直接摊开给用户看。
 *   用户能看到画像里有没有跑偏的词、缓存池还剩多少、
 *   最近一次补货是朝哪个方向抓的。
 */
const ProfilePanel: FC<Props> = ({
  userId,
  user,
  open,
  onClose,
  onSync,
  onLogout,
}) => {
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [queue, setQueue] = useState<QueueStats | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  /** GitHub 实证圈概况（登录后才有） */
  const [me, setMe] = useState<MeResponse | null>(null)

  // ── 自定义关键词（用户手动维护，可驱动爬虫抓取）──
  const [kws, setKws] = useState<CustomKeyword[]>([])
  const [kwInput, setKwInput] = useState('')
  const [kwBusy, setKwBusy] = useState(false)
  const [crawling, setCrawling] = useState(false)
  const [crawlInfo, setCrawlInfo] = useState<string | null>(null)

  const load = useCallback(async (): Promise<void> => {
    setErr(null)
    setLoading(true)
    try {
      // ⚠️ 两个请求各自 catch —— 一个挂了不该让整页空白。
      //    之前用 Promise.all，profile 失败会连带把 queue 也丢掉。
      const [p, q] = await Promise.allSettled([
        getUserProfile(userId),
        getQueueStats(userId),
      ])
      if (p.status === 'fulfilled') setProfile(p.value)
      else setErr(p.reason instanceof Error ? p.reason.message : '画像加载失败')
      if (q.status === 'fulfilled') setQueue(q.value)
    } finally {
      setLoading(false)
    }
  }, [userId])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  // ── 打开时拉自定义关键词 ──
  useEffect(() => {
    if (!open) return
    getCustomKeywords(userId)
      .then((r) => setKws(r.keywords))
      .catch(() => setKws([]))
  }, [open, userId])

  // ── 打开时拉 GitHub 实证圈概况（登录用户才有）──
  useEffect(() => {
    if (!open || !getToken()) {
      setMe(null)
      return
    }
    getMe()
      .then(setMe)
      .catch(() => setMe(null))
  }, [open, userId])

  const submitKeyword = async (): Promise<void> => {
    const kw = kwInput.trim()
    if (!kw || kwBusy) return
    setKwBusy(true)
    try {
      const item = await addCustomKeyword(userId, kw)
      if (item.inserted) {
        setKws((prev) => [item, ...prev])
        setKwInput('')
      } else {
        toast('这个关键词已经有了', 'warn')
      }
    } catch (e) {
      toast(e instanceof Error ? e.message : '添加失败', 'warn')
    } finally {
      setKwBusy(false)
    }
  }

  const removeKw = async (id: number): Promise<void> => {
    try {
      await deleteCustomKeyword(id, userId)
      setKws((prev) => prev.filter((k) => k.id !== id))
    } catch {
      toast('删除失败', 'warn')
    }
  }

  const runCrawl = async (): Promise<void> => {
    if (crawling) return
    setCrawling(true)
    setCrawlInfo(null)
    try {
      const r = await crawlByKeywords(userId)
      setCrawlInfo(
        `抓到 ${r.found} 个候选 · 新入库 ${r.new_repos} 个 · 入队 ${r.enqueued} 个 —— 回去刷 Feed 就能看到`,
      )
      toast(`爬虫抓回 ${r.enqueued} 条新推荐`, 'ok')
      void load() // 顺手刷新队列水位
    } catch (e) {
      const msg = e instanceof Error ? e.message : '抓取失败'
      toast(msg, 'warn')
      setCrawlInfo(`抓取失败：${msg}`)
    } finally {
      setCrawling(false)
    }
  }

  const rebuild = async (): Promise<void> => {
    setBusy(true)
    try {
      await rebuildUserProfile(userId)
      await load()
    } catch (e) {
      setErr(e instanceof Error ? e.message : '重建失败')
    } finally {
      setBusy(false)
    }
  }

  if (!open) return null

  const topics: ProfileItem[] = profile?.interests ?? []
  const langs = profile?.languages ?? []
  const maxW = topics.length > 0 ? topics[0].weight : 1

  return (
    <div className="absolute inset-0 z-50 bg-parchment flex flex-col animate-slide-up">
      <div className="safe-t shrink-0 flex items-center px-4 pt-4 pb-2">
        <h1 className="text-[17px] font-semibold text-ink tracking-[-0.37px]">
          我的兴趣画像
        </h1>
        <button className="btn-ghost ml-auto" onClick={onClose}>
          关闭
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-8">
        {err && (
          <div className="bg-canvas border border-hairline rounded-pearl px-4 py-3 mt-2 text-[12.5px] text-risk-caution">
            {err}
          </div>
        )}

        {loading && !profile && (
          <div className="mt-4 space-y-3">
            <div className="skeleton h-24 w-full rounded-card" />
            <div className="skeleton h-4 w-40" />
            <div className="skeleton h-3 w-full" />
            <div className="skeleton h-3 w-[80%]" />
          </div>
        )}

        {/* ── GitHub 实证圈（最强信号：自建仓库 > 点星仓库）── */}
        <div className="card p-4 mt-3">
          {user?.github_login ? (
            <>
              <div className="flex items-center gap-3">
                {user.avatar_url && (
                  <img
                    src={user.avatar_url}
                    alt={user.github_login}
                    className="h-11 w-11 rounded-full border border-hairline object-cover"
                    referrerPolicy="no-referrer"
                  />
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-[14px] font-semibold text-ink truncate">
                    @{user.github_login}
                  </div>
                  <div className="text-[11px] text-ink-muted mt-0.5">
                    {me
                      ? `自建 ${me.footprint.owned_count} 个 · 点星 ${me.footprint.starred_count} 个`
                      : '还没同步 GitHub 数据'}
                  </div>
                </div>
                <button
                  className="chip bg-canvas text-accent border border-hairline hover:bg-pearl transition-colors shrink-0"
                  onClick={onSync}
                >
                  🔄 同步
                </button>
              </div>

              <p className="text-[11px] text-ink-muted mt-2.5 leading-relaxed">
                你在写的仓库 = 铁证兴趣（本周更新权重 0.5，越久越低）；
                点星仓库 = 明确认可（按星数排名，榜首 0.4）。
                画像按 ×{me?.footprint.boost ?? 2} 放大并驱动定向补货。
              </p>

              {me && (me.footprint.owned.length > 0 ||
                me.footprint.starred.length > 0) && (
                <div className="mt-3 pt-3 border-t border-divider space-y-1.5">
                  {me.footprint.owned.slice(0, 4).map((it) => (
                    <div key={`o-${it.full_name}`} className="flex items-center gap-2">
                      <span className="w-9 shrink-0 text-[10px] text-gem font-medium">
                        自建
                      </span>
                      <span className="text-[12px] text-ink truncate flex-1 min-w-0">
                        {it.full_name}
                      </span>
                      <span className="text-[10.5px] text-ink-faint shrink-0">
                        {it.pushed_at ? timeAgo(it.pushed_at) : ''}
                      </span>
                      <span className="text-[11px] font-semibold text-gem shrink-0 w-9 text-right">
                        {it.weight.toFixed(2)}
                      </span>
                    </div>
                  ))}
                  {me.footprint.starred.slice(0, 4).map((it) => (
                    <div key={`s-${it.full_name}`} className="flex items-center gap-2">
                      <span className="w-9 shrink-0 text-[10px] text-accent font-medium">
                        点星
                      </span>
                      <span className="text-[12px] text-ink-soft truncate flex-1 min-w-0">
                        {it.full_name}
                      </span>
                      <span className="text-[10.5px] text-ink-faint shrink-0">
                        {it.rank ? `#${it.rank}` : ''}
                        {it.stars !== undefined ? ` ★${formatStars(it.stars)}` : ''}
                      </span>
                      <span className="text-[11px] font-semibold text-accent shrink-0 w-9 text-right">
                        {it.weight.toFixed(2)}
                      </span>
                    </div>
                  ))}
                </div>
              )}

              <button
                className="w-full text-[12px] text-ink-muted py-2.5 mt-1 active:opacity-60"
                onClick={onLogout}
              >
                退出登录
              </button>
            </>
          ) : (
            <>
              <div className="text-[13px] text-ink-soft">
                登录 GitHub，推荐会更猛
              </div>
              <p className="text-[11px] text-ink-muted mt-1.5 leading-relaxed">
                我们会读取你的仓库列表与点星列表 ——
                你亲手做的项目最能说明兴趣，登录后会大幅加推同类仓库。
              </p>
              <button
                className="btn-primary w-full mt-3"
                onClick={() => {
                  window.location.href = githubLoginUrl()
                }}
              >
                使用 GitHub 登录
              </button>
            </>
          )}
        </div>

        {/* ── 冷启动提示 ── */}
        {profile?.cold_start && topics.length === 0 && (
          <div className="card p-5 mt-3 text-center">
            <div className="text-3xl mb-2.5">🌱</div>
            <p className="text-[13.5px] text-ink-soft">画像还在建立中</p>
            <p className="text-[12px] text-ink-muted mt-1.5 leading-relaxed">
              已记录 {profile.events} 条行为、{profile.seen} 个仓库曝光。
              <br />
              多刷几条、遇到感兴趣的停一会儿，兴趣方向就会浮现出来。
            </p>
          </div>
        )}

        {/* ── 缓存池健康度 ── */}
        {queue && (
          <div className="card p-4 mt-3">
            <div className="flex items-center justify-between">
              <span className="text-[13px] text-ink-soft">待刷缓存池</span>
              <span className="text-[15px] font-semibold text-ink">
                {queue.size}
                <span className="text-[11px] text-ink-faint ml-1">
                  / {queue.target_size}
                </span>
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-divider overflow-hidden mt-2.5">
              <div
                className={`h-full rounded-full transition-all ${
                  queue.needs_refill ? 'bg-risk-caution' : 'bg-risk-safe'
                }`}
                style={{
                  width: `${Math.min(
                    100,
                    (queue.size / Math.max(1, queue.target_size)) * 100,
                  )}%`,
                }}
              />
            </div>
            <p className="text-[11px] text-ink-muted mt-2 leading-relaxed">
              {queue.needs_refill
                ? `低于低水位 ${queue.low_watermark} 条，正在按画像定向补货`
                : '水位健康，刷到的都是按你兴趣挑出来的'}
            </p>

            {queue.recent_refills[0]?.strategy?.queries &&
              queue.recent_refills[0].strategy.queries.length > 0 && (
                <div className="mt-3 pt-3 border-t border-divider">
                  <p className="text-[11px] text-ink-muted mb-1.5">
                    最近一次补货按这些方向抓
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {queue.recent_refills[0].strategy.queries
                      .slice(0, 6)
                      .map((qq) => (
                        <span
                          key={qq}
                          className="chip bg-accent/[0.08] text-accent border border-accent/20"
                        >
                          {qq}
                        </span>
                      ))}
                  </div>
                  {(queue.recent_refills[0].strategy.domains ?? []).length >
                    0 && (
                    <div className="flex flex-wrap gap-1.5 mt-1.5">
                      {(queue.recent_refills[0].strategy.domains ?? []).map(
                        (d) => (
                          <span
                            key={d}
                            className="chip bg-gem/[0.08] text-gem border border-gem/25"
                          >
                            {d}
                          </span>
                        ),
                      )}
                    </div>
                  )}
                </div>
              )}
          </div>
        )}

        {/* ── 自定义关键词 · 驱动爬虫 ── */}
        <div className="card p-4 mt-3">
          <div className="flex items-center justify-between">
            <span className="text-[13px] text-ink-soft">自定义关键词</span>
            <span className="text-[11px] text-ink-faint">{kws.length}/12</span>
          </div>
          <p className="text-[11px] text-ink-muted mt-1 leading-relaxed">
            添加你关心的技术方向，Scrapling
            爬虫会按这些词去 GitHub 抓真实仓库，直接进你的 Feed；日常补货也会优先按这些方向来。
          </p>

          <div className="flex items-center gap-2 mt-2.5">
            <input
              value={kwInput}
              onChange={(e) => setKwInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void submitKeyword()
              }}
              placeholder="如：rvc、wasm、推荐系统…"
              className="flex-1 min-w-0 bg-canvas border border-hairline rounded-full px-3.5 h-9
                         text-[13px] text-ink placeholder:text-ink-faint outline-none"
              maxLength={40}
            />
            <button
              className="btn-ghost shrink-0"
              onClick={() => void submitKeyword()}
              disabled={kwBusy || !kwInput.trim() || kws.length >= 12}
            >
              {kwBusy ? '添加中…' : '添加'}
            </button>
          </div>

          {kws.length > 0 && (
            <div className="flex flex-wrap gap-1.5 mt-2.5">
              {kws.map((k) => (
                <span
                  key={k.id}
                  className="chip bg-accent/[0.08] text-accent border border-accent/20"
                >
                  {k.keyword}
                  <button
                    className="ml-1.5 text-accent/50 hover:text-accent leading-none"
                    onClick={() => void removeKw(k.id)}
                    aria-label={`删除 ${k.keyword}`}
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          )}

          <button
            className="btn-primary w-full mt-3"
            onClick={() => void runCrawl()}
            disabled={crawling || kws.length === 0}
          >
            {crawling ? '🕷️ 爬虫抓取中…' : '🕷️ 让爬虫去抓'}
          </button>
          {crawlInfo && (
            <p className="text-[11.5px] text-ink-muted mt-2 leading-relaxed">
              {crawlInfo}
            </p>
          )}
        </div>

        {/* ── 兴趣标签 ── */}
        <div className="mt-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-[12px] font-semibold text-ink-muted tracking-wide">
              兴趣标签（{topics.length}）
            </h2>
            <button
              className="chip bg-canvas text-accent border border-hairline
                         hover:bg-pearl transition-colors disabled:opacity-40"
              onClick={() => void rebuild()}
              disabled={busy}
            >
              {busy ? '重建中…' : '重建画像'}
            </button>
          </div>

          {topics.length === 0 && !loading && (
            <p className="text-[12.5px] text-ink-faint py-5 text-center">
              还没有形成兴趣标签
            </p>
          )}

          <div className="space-y-2">
            {topics.map((t, i) => (
              <div key={t.tag} className="flex items-center gap-3">
                <span className="text-[11px] text-ink-faint w-5 shrink-0 text-right">
                  {i + 1}
                </span>
                <span className="text-[12.5px] text-ink-soft w-28 shrink-0 truncate">
                  {t.tag}
                </span>
                <div className="flex-1 h-1.5 rounded-full bg-divider overflow-hidden">
                  <div
                    className="h-full rounded-full bg-accent"
                    style={{ width: `${(t.weight / maxW) * 100}%` }}
                  />
                </div>
                <span className="text-[10.5px] text-ink-faint w-9 text-right shrink-0">
                  {t.weight.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* ── 语言偏好 ── */}
        {langs.length > 0 && (
          <div className="mt-6">
            <h2 className="text-[12px] font-semibold text-ink-muted mb-3 tracking-wide">
              语言偏好
            </h2>
            <div className="flex flex-wrap gap-1.5">
              {langs.map((l) => {
                const name = typeof l === 'string' ? l : l.lang
                const n = typeof l === 'string' ? undefined : l.n
                return (
                  <span
                    key={name}
                    className="chip bg-canvas text-ink-soft border border-hairline"
                  >
                    {name}
                    {n !== undefined && (
                      <span className="text-ink-faint ml-1.5">{n}</span>
                    )}
                  </span>
                )
              })}
            </div>
          </div>
        )}

        {/* ── 冷启动统计 ── */}
        {profile && (
          <div className="grid grid-cols-2 gap-2 mt-6">
            <div className="bg-canvas border border-hairline rounded-pearl px-3 py-2.5">
              <div className="text-[15px] font-semibold text-ink">
                {profile.events}
              </div>
              <div className="text-[10px] text-ink-muted mt-1">行为记录</div>
            </div>
            <div className="bg-canvas border border-hairline rounded-pearl px-3 py-2.5">
              <div className="text-[15px] font-semibold text-ink">
                {profile.seen}
              </div>
              <div className="text-[10px] text-ink-muted mt-1">已曝光仓库</div>
            </div>
          </div>
        )}

        <p className="text-[10.5px] text-ink-faint mt-6 leading-relaxed">
          画像由你的浏览行为构建：停留越久、读得越深，对应的技术标签权重越高。
          划过不等于「不感兴趣」，只做短期冷却。
        </p>
      </div>
    </div>
  )
}

export default ProfilePanel
