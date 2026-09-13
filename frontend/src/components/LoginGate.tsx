import { useEffect, useState } from 'react'
import type { FC } from 'react'
import { getAuthStatus, githubLoginUrl, loginWithPat } from '@/lib/api'
import type { AuthStatus, SessionUser } from '@/types/api'
import { setCachedUser, setGuest, setToken } from '@/lib/session'

interface Props {
  onLoggedIn: (user: SessionUser) => void
  /** 令牌登录成功后是否需要立刻同步 GitHub 数据 */
  onNeedSync: () => void
}

/**
 * 登录页 —— GitHub OAuth 注册登录。
 *
 * ⭐ 为什么值得登录（页面上要讲清楚，否则用户不会点）：
 *   登录后我们会读取你的「点星仓库」和「你自己做的仓库」——
 *   前者是你明确觉得有意思的，后者是你亲手在写的。
 *   这两类证据比"刷到卡片停了一会儿"强得多，推荐会又准又猛。
 */
const LoginGate: FC<Props> = ({ onLoggedIn, onNeedSync }) => {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [pat, setPat] = useState('')
  const [showPat, setShowPat] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    getAuthStatus()
      .then((s) => {
        setStatus(s)
        if (!s.configured) setShowPat(true)
      })
      .catch(() => setStatus(null))
  }, [])

  const doLogin = (): void => {
    window.location.href = githubLoginUrl()
  }

  const doPatLogin = async (): Promise<void> => {
    const t = pat.trim()
    if (!t || busy) return
    setBusy(true)
    setErr(null)
    try {
      const res = await loginWithPat(t)
      setToken(res.token)
      setCachedUser(res.user)
      onLoggedIn(res.user)
      onNeedSync()
    } catch (e) {
      setErr(e instanceof Error ? e.message : '登录失败')
    } finally {
      setBusy(false)
    }
  }

  const guest = (): void => {
    setGuest(true)
    setCachedUser(null)
    onLoggedIn({
      user_id: 1,
      username: 'demo',
      github_login: null,
      display_name: '演示用户',
      avatar_url: null,
    })
  }

  return (
    <div className="h-full w-full bg-parchment flex flex-col items-center justify-center px-7 text-center safe-t safe-b">
      <div className="text-[28px] font-semibold text-ink tracking-[-0.6px] leading-none">
        RecoFeed
      </div>
      <div className="text-[12px] text-ink-faint mt-1.5">遗珠推荐 · 刷到被埋没的好项目</div>

      <h1 className="text-[19px] font-semibold text-ink mt-9 tracking-[-0.3px]">
        用 GitHub 登录
      </h1>
      <p className="text-[13px] text-ink-muted mt-2.5 leading-relaxed max-w-[300px]">
        登录后我们会读取你的<span className="text-ink">点星仓库</span>和
        <span className="text-ink">你自己做的仓库</span>——
        你亲手写的东西最能说明你的兴趣，推荐会更准、更猛。
      </p>

      <div className="w-full max-w-[300px] mt-7 space-y-2.5">
        {status?.configured && (
          <button className="btn-primary w-full h-11" onClick={doLogin}>
            <GithubMark />
            使用 GitHub 登录
          </button>
        )}

        {status?.configured && !showPat && (
          <button
            className="w-full text-[12px] text-ink-muted py-1"
            onClick={() => setShowPat(true)}
          >
            或用个人访问令牌登录
          </button>
        )}

        {showPat && (
          <div className="text-left">
            <div className="flex items-center gap-2">
              <input
                value={pat}
                onChange={(e) => setPat(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void doPatLogin()
                }}
                type="password"
                placeholder="粘贴 GitHub 令牌 (ghp_… / github_pat_…)"
                className="flex-1 min-w-0 bg-canvas border border-hairline rounded-full px-3.5 h-10
                           text-[12.5px] text-ink placeholder:text-ink-faint outline-none"
                autoComplete="off"
                spellCheck={false}
              />
              <button
                className="btn-ghost shrink-0"
                onClick={() => void doPatLogin()}
                disabled={busy || !pat.trim()}
              >
                {busy ? '验证中…' : '登录'}
              </button>
            </div>
            <p className="text-[10.5px] text-ink-faint mt-2 leading-relaxed">
              {status?.configured
                ? '不想走 OAuth 授权也行：'
                : '还没配置 OAuth 应用，用令牌一样能登录：'}
              在 GitHub → Settings → Developer settings → Personal access tokens
              生成一个带 <span className="text-ink-soft">read:user</span> +{' '}
              <span className="text-ink-soft">public_repo</span> 权限的令牌。
            </p>
          </div>
        )}

        {err && (
          <div className="text-[11.5px] text-risk-danger leading-relaxed">{err}</div>
        )}

        <button
          className="w-full text-[12.5px] text-ink-muted py-2 active:opacity-60"
          onClick={guest}
        >
          先随便逛逛（游客模式）
        </button>
      </div>

      <p className="text-[10px] text-ink-faint mt-8 leading-relaxed max-w-[290px]">
        我们只读公开数据（你的仓库列表与点星列表），不会修改你的任何内容。
        令牌仅存在本机数据库。
      </p>
    </div>
  )
}

const GithubMark: FC = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.4 7.4 0 0 1 2-.27c.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8Z" />
  </svg>
)

export default LoginGate
