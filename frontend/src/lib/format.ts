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

/** 许可证风险 → 展示文案与配色（浅色语义 tint） */
export function licenseBadge(
  spdx: string | null,
  risk: string | null,
): { text: string; cls: string } | null {
  if (!spdx) return null
  const map: Record<string, string> = {
    safe: 'text-risk-safe border-risk-safe/35 bg-risk-safe/[0.08]',
    caution: 'text-risk-caution border-risk-caution/35 bg-risk-caution/[0.08]',
    danger: 'text-risk-danger border-risk-danger/35 bg-risk-danger/[0.08]',
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

/** 流量池等级 → 名称与配色（浅色语义） */
export function poolBadge(level: number | null): {
  name: string
  cls: string
} {
  const map: Record<number, { name: string; cls: string }> = {
    [-1]: { name: '已淘汰', cls: 'text-ink-faint border-hairline' },
    0: { name: '冷启动', cls: 'text-ink-muted border-hairline' },
    1: { name: '基础池', cls: 'text-accent border-accent/35' },
    2: { name: '中级池', cls: 'text-accent border-accent/35' },
    3: { name: '高级池', cls: 'text-gem border-gem/35' },
    4: { name: '爆款池', cls: 'text-gem-soft border-gem-soft/45' },
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


/**
 * 仓库体积的人类可读格式。
 *
 * ⚠️ 为什么单独写：原来两处各写各的 —— 详情面板用 `(kb/1024).toFixed(1)` → 缺数据时
 * 显示「0.0 MB」（看着像坏了），卡片用 `Math.max(1, round(kb/1024))` → 缺数据时显示「1 MB」
 * （凭空捏造）。缺数据就该老老实实显示 —。
 */
export function formatSize(kb?: number | null): string {
  const n = Number(kb ?? 0)
  if (!n || n <= 0) return '—'
  if (n < 1024) return `${Math.round(n)} KB`
  const mb = n / 1024
  if (mb < 1024) return `${mb < 10 ? mb.toFixed(1) : Math.round(mb)} MB`
  return `${(mb / 1024).toFixed(1)} GB`
}

/**
 * README 是否是"实质内容"。
 *
 * 爬虫抓不到真 README 时会写入「# 仓库名 + 一句话简介」这种兜底文本（实测约 79 字），
 * 对这种仓库摆一个「读完整 README」的入口是很滑稽的 —— 用户点进去只有一行字。
 */
export function isThinReadme(
  readme?: string | null,
  name?: string | null,
  description?: string | null,
): boolean {
  const t = (readme ?? '').trim()
  if (t.length < 200) return true
  const norm = (x: string): string => (x ?? '').toLowerCase().replace(/[\s\W_]+/g, '')
  const n = norm(t)
  return n === norm(name ?? '') || n === norm(description ?? '') ||
    n === norm(name ?? '') + norm(description ?? '')
}
