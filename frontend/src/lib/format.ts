/**
 * 展示层工具函数。
 */

/** 22800 → "22.8k"；1200000 → "1.2M" */
export function formatStars(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) {
    const k = n / 1_000
    return k >= 100 ? `${Math.round(k)}k` : `${k.toFixed(1)}k`
  }
  return String(n)
}

/** 相对时间："3 天前" */
export function timeAgo(iso: string | null): string {
  if (!iso) return '未知'
  const t = new Date(iso.replace(' ', 'T')).getTime()
  if (Number.isNaN(t)) return '未知'
  const diff = Date.now() - t
  const day = 86_400_000
  if (diff < 3_600_000) return `${Math.max(1, Math.floor(diff / 60_000))} 分钟前`
  if (diff < day) return `${Math.floor(diff / 3_600_000)} 小时前`
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`
  if (diff < 365 * day) return `${Math.floor(diff / (30 * day))} 个月前`
  return `${Math.floor(diff / (365 * day))} 年前`
}

/** GitHub 官方语言配色（覆盖常用项，未命中回退灰） */
const LANG_COLORS: Record<string, string> = {
  Python: '#3572A5',
  TypeScript: '#3178c6',
  JavaScript: '#f1e05a',
  Rust: '#dea584',
  Go: '#00ADD8',
  'C++': '#f34b7d',
  C: '#555555',
  Java: '#b07219',
  Kotlin: '#A97BFF',
  Swift: '#F05138',
  Ruby: '#701516',
  PHP: '#4F5D95',
  Shell: '#89e051',
  HTML: '#e34c26',
  CSS: '#563d7c',
  Vue: '#41b883',
  Svelte: '#ff3e00',
  Dart: '#00B4AB',
  Zig: '#ec915c',
  Lua: '#000080',
  Haskell: '#5e5086',
  Elixir: '#6e4a7e',
  Scala: '#c22d40',
  Julia: '#a270ba',
  CUDA: '#3A552E',
  Jupyter: '#DA5B0B',
  'Jupyter Notebook': '#DA5B0B',
}

export function langColor(lang: string | null | undefined): string {
  if (!lang) return '#8b8b9a'
  return LANG_COLORS[lang] ?? '#8b8b9a'
}

/** 许可证风险 → 展示文案与配色 */
export function licenseBadge(
  spdx: string | null,
  risk: string | null,
): { text: string; cls: string } | null {
  if (!spdx) return null
  const map: Record<string, string> = {
    safe: 'text-risk-safe border-risk-safe/40 bg-risk-safe/10',
    caution: 'text-risk-caution border-risk-caution/40 bg-risk-caution/10',
    danger: 'text-risk-danger border-risk-danger/40 bg-risk-danger/10',
  }
  return {
    text: spdx,
    cls: map[risk ?? 'safe'] ?? map.safe,
  }
}

/**
 * 遗珠判定：低曝光 + 高口碑。
 *
 * ⚠️ 阈值必须与后端 profile/rank 侧保持一致，
 *    否则会出现「前端标了金色遗珠标记、后端其实没给它遗珠配额」的错位。
 *    后端 forgotten_score ≥ 0.7 视为遗珠（见 docs 里的定义）。
 */
export const FORGOTTEN_THRESHOLD = 0.7

export function isForgotten(score: number | null | undefined): boolean {
  return (score ?? 0) >= FORGOTTEN_THRESHOLD
}

/** 流量池等级 → 名称与配色 */
export function poolBadge(level: number | null): {
  name: string
  cls: string
} {
  const map: Record<number, { name: string; cls: string }> = {
    [-1]: { name: '已淘汰', cls: 'text-white/40 border-white/15' },
    0: { name: '冷启动', cls: 'text-white/60 border-white/20' },
    1: { name: '基础池', cls: 'text-accent border-accent/40' },
    2: { name: '中级池', cls: 'text-accent-glow border-accent-glow/40' },
    3: { name: '高级池', cls: 'text-gem border-gem/40' },
    4: { name: '爆款池', cls: 'text-gem-soft border-gem-soft/50' },
  }
  return map[level ?? 0] ?? map[0]
}

/** 评分条：把 0~1 的分数转成 0~100 */
export function pct(v: number | null | undefined): number {
  return Math.round(Math.max(0, Math.min(1, v ?? 0)) * 100)
}

/** 极简 Markdown → 纯文本（详情页展示 README 摘要用） */
export function stripMarkdown(md: string): string {
  return md
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/^\s*[-*+]\s+/gm, '· ')
    .replace(/\*\*([^*]*)\*\*/g, '$1')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

/** 稳定 hash（给头像底色用，同一个 owner 每次颜色一致） */
export function hashHue(s: string): number {
  let h = 0
  for (let i = 0; i < s.length; i++) {
    h = (h * 31 + s.charCodeAt(i)) | 0
  }
  return Math.abs(h) % 360
}
