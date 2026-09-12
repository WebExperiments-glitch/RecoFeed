import { useCallback, useEffect, useState } from 'react'
import type { FC } from 'react'
import type { ProfileItem, QueueStats, UserProfile } from '@/types/api'
import { getQueueStats, getUserProfile, rebuildUserProfile } from '@/lib/api'

interface Props {
  userId: number
  open: boolean
  onClose: () => void
}

/**
 * 我的画像面板。
 *
 * 这是本项目的第二个透明度设计：
 *   把「推荐系统以为你喜欢什么」直接摊开给用户看。
 *   用户能看到画像里有没有跑偏的词、缓存池还剩多少、
 *   最近一次补货是朝哪个方向抓的。
 */
const ProfilePanel: FC<Props> = ({ userId, open, onClose }) => {
  const [profile, setProfile] = useState<UserProfile | null>(null)
  const [queue, setQueue] = useState<QueueStats | null>(null)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState<string | null>(null)

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
    <div className="absolute inset-0 z-50 bg-ink-900/97 backdrop-blur-glass flex flex-col animate-slide-up">
      <div className="safe-t shrink-0 flex items-center px-4 pt-4 pb-2">
        <h1 className="text-[17px] font-semibold text-white">我的兴趣画像</h1>
        <button className="btn-ghost ml-auto" onClick={onClose}>
          关闭
        </button>
      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 pb-8">
        {err && (
          <div className="glass-soft rounded-xl px-4 py-3 mt-2 text-[12.5px] text-risk-caution">
            {err}
          </div>
        )}

        {loading && !profile && (
          <div className="mt-4 space-y-3">
            <div className="skeleton h-24 w-full rounded-2xl" />
            <div className="skeleton h-4 w-40" />
            <div className="skeleton h-3 w-full" />
            <div className="skeleton h-3 w-[80%]" />
          </div>
        )}

        {/* ── 冷启动提示 ── */}
        {profile?.cold_start && topics.length === 0 && (
          <div className="glass-soft rounded-2xl p-5 mt-3 text-center">
            <div className="text-3xl mb-2.5">🌱</div>
            <p className="text-[13.5px] text-white/75">画像还在建立中</p>
            <p className="text-[12px] text-white/40 mt-1.5 leading-relaxed">
              已记录 {profile.events} 条行为、{profile.seen} 个仓库曝光。
              <br />
              多刷几条、遇到感兴趣的停一会儿，兴趣方向就会浮现出来。
            </p>
          </div>
        )}

        {/* ── 缓存池健康度 ── */}
        {queue && (
          <div className="glass-soft rounded-2xl p-4 mt-3">
            <div className="flex items-center justify-between">
              <span className="text-[13px] text-white/70">待刷缓存池</span>
              <span className="text-[15px] font-semibold text-white">
                {queue.size}
                <span className="text-[11px] text-white/35 ml-1">
                  / {queue.target_size}
                </span>
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-white/[0.07] overflow-hidden mt-2.5">
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
            <p className="text-[11px] text-white/35 mt-2 leading-relaxed">
              {queue.needs_refill
                ? `低于低水位 ${queue.low_watermark} 条，正在按画像定向补货`
                : '水位健康，刷到的都是按你兴趣挑出来的'}
            </p>

            {queue.recent_refills[0]?.strategy?.queries &&
              queue.recent_refills[0].strategy.queries.length > 0 && (
                <div className="mt-3 pt-3 border-t border-white/[0.06]">
                  <p className="text-[11px] text-white/35 mb-1.5">
                    最近一次补货按这些方向抓
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {queue.recent_refills[0].strategy.queries
                      .slice(0, 6)
                      .map((qq) => (
                        <span
                          key={qq}
                          className="chip bg-accent/10 text-accent-glow/85 border border-accent/20"
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
                            className="chip bg-gem/10 text-gem border border-gem/25"
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

        {/* ── 兴趣标签 ── */}
        <div className="mt-6">
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-[12px] font-semibold text-white/40 tracking-wide">
              兴趣标签（{topics.length}）
            </h2>
            <button
              className="chip bg-white/[0.05] text-white/55 border border-white/[0.08]
                         hover:bg-white/[0.1] transition-colors disabled:opacity-40"
              onClick={() => void rebuild()}
              disabled={busy}
            >
              {busy ? '重建中…' : '重建画像'}
            </button>
          </div>

          {topics.length === 0 && !loading && (
            <p className="text-[12.5px] text-white/28 py-5 text-center">
              还没有形成兴趣标签
            </p>
          )}

          <div className="space-y-2">
            {topics.map((t, i) => (
              <div key={t.tag} className="flex items-center gap-3">
                <span className="text-[11px] text-white/25 w-5 shrink-0 text-right">
                  {i + 1}
                </span>
                <span className="text-[12.5px] text-white/72 w-28 shrink-0 truncate">
                  {t.tag}
                </span>
                <div className="flex-1 h-1.5 rounded-full bg-white/[0.06] overflow-hidden">
                  <div
                    className="h-full rounded-full bg-gradient-to-r from-accent to-accent-glow"
                    style={{ width: `${(t.weight / maxW) * 100}%` }}
                  />
                </div>
                <span className="text-[10.5px] text-white/30 w-9 text-right shrink-0">
                  {t.weight.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>

        {/* ── 语言偏好 ── */}
        {langs.length > 0 && (
          <div className="mt-6">
            <h2 className="text-[12px] font-semibold text-white/40 mb-3 tracking-wide">
              语言偏好
            </h2>
            <div className="flex flex-wrap gap-1.5">
              {langs.map((l) => {
                const name = typeof l === 'string' ? l : l.lang
                const n = typeof l === 'string' ? undefined : l.n
                return (
                  <span
                    key={name}
                    className="chip bg-white/[0.05] text-white/60 border border-white/[0.08]"
                  >
                    {name}
                    {n !== undefined && (
                      <span className="text-white/25 ml-1.5">{n}</span>
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
            <div className="glass-soft rounded-xl px-3 py-2.5">
              <div className="text-[15px] font-semibold text-white/88">
                {profile.events}
              </div>
              <div className="text-[10px] text-white/35 mt-1">行为记录</div>
            </div>
            <div className="glass-soft rounded-xl px-3 py-2.5">
              <div className="text-[15px] font-semibold text-white/88">
                {profile.seen}
              </div>
              <div className="text-[10px] text-white/35 mt-1">已曝光仓库</div>
            </div>
          </div>
        )}

        <p className="text-[10.5px] text-white/22 mt-6 leading-relaxed">
          画像由你的浏览行为构建：停留越久、读得越深，对应的技术标签权重越高。
          划过不等于「不感兴趣」，只做短期冷却。
        </p>
      </div>
    </div>
  )
}

export default ProfilePanel
