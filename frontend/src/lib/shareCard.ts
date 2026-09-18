import type { FeedItem } from '@/types/api'

/**
 * 生成「发现卡片」分享图（PNG）。
 *
 * 为什么做
 *   评测原话："既然挖到了 TalkWithMe 或 VieNeuTTS 这样的宝藏，我作为一个用户，
 *   最大的冲动是分享到技术群。但工具缺少一键生成精美分享图的功能。"
 *   推荐系统没有社交裂变就很难破圈 —— 所以给一个能直接发群里/X 的图。
 *
 * 实现取舍
 *   手写 Canvas 2D，不引入 html2canvas 之类的依赖：可控（精确控制排版/字号/间距）、
 *   体积极小、也不受 DOM 结构变化影响。代价是每段文字要自己算换行。
 */

const W = 1200
const H = 1500
const PAD = 88

const FONT_STACK =
  'system-ui, -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif'

const INK = '#1d1d1f'
const INK_SOFT = '#424245'
const INK_MUTED = '#6e6e73'
const ACCENT = '#0066cc'
const HAIRLINE = '#e5e5ea'
const PARCHMENT = '#f5f5f7'

interface Ctx {
  ctx: CanvasRenderingContext2D
  y: number
  x: number
}

/** 逐行绘制（需要自己控制 y，所以这里包一层） */
function drawParagraph(
  c: Ctx,
  text: string,
  opts: { size: number; color: string; weight?: string; lineHeight: number;
          maxLines?: number; gapAfter?: number },
): void {
  const { ctx } = c
  ctx.font = `${opts.weight ?? '400'} ${opts.size}px ${FONT_STACK}`
  ctx.fillStyle = opts.color
  ctx.textBaseline = 'top'
  const maxW = W - PAD * 2
  const words = text.split(/(\s+)/)
  let line = ''
  let lines = 0
  const linesOut: string[] = []
  for (const w of words) {
    const test = line + w
    if (ctx.measureText(test).width > maxW && line) {
      linesOut.push(line.trimEnd())
      lines += 1
      if (opts.maxLines && lines >= opts.maxLines) break
      line = w.trimStart()
    } else {
      line = test
    }
  }
  if (line.trim() && (!opts.maxLines || lines < opts.maxLines)) linesOut.push(line.trimEnd())

  for (const l of linesOut) {
    ctx.fillText(l, PAD, c.y)
    c.y += opts.lineHeight
  }
  c.y += opts.gapAfter ?? 0
}

function chip(c: Ctx, text: string): void {
  const { ctx } = c
  ctx.font = `500 26px ${FONT_STACK}`
  const w = ctx.measureText(text).width + 40
  if (c.x + w > W - PAD) {          // 换行
    c.x = PAD
    c.y += 58
  }
  const h = 46
  const r = h / 2
  ctx.beginPath()
  ctx.roundRect(c.x, c.y, w, h, r)
  ctx.fillStyle = PARCHMENT
  ctx.fill()
  ctx.strokeStyle = HAIRLINE
  ctx.lineWidth = 2
  ctx.stroke()
  ctx.fillStyle = INK_SOFT
  ctx.textBaseline = 'middle'
  ctx.fillText(text, c.x + 20, c.y + h / 2 + 1)
  ctx.textBaseline = 'top'
  c.x += w + 14
}

export interface ShareCardInput {
  item: FeedItem
  /** AI 速读（可选，有就用它做卖点） */
  oneLiner?: string
  highlights?: string[]
  stack?: string[]
}

/** 生成分享图 Blob（调用方负责下载/复制）。失败返回 null。 */
export async function makeShareCard(input: ShareCardInput): Promise<Blob | null> {
  const { item } = input
  const canvas = document.createElement('canvas')
  canvas.width = W
  canvas.height = H
  const ctx = canvas.getContext('2d')
  if (!ctx) return null

  // 背景
  ctx.fillStyle = '#ffffff'
  ctx.fillRect(0, 0, W, H)

  const c: Ctx = { ctx, y: PAD, x: PAD }

  // 顶部品牌行
  ctx.fillStyle = INK
  ctx.font = `600 34px ${FONT_STACK}`
  ctx.textBaseline = 'top'
  ctx.fillText('RecoFeed', PAD, c.y)
  ctx.font = `400 26px ${FONT_STACK}`
  ctx.fillStyle = INK_MUTED
  ctx.fillText('遗珠推荐 · 用刷视频的方式发现 GitHub', PAD + 210, c.y + 8)
  c.y += 72

  // 双线
  ctx.strokeStyle = HAIRLINE
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.moveTo(PAD, c.y)
  ctx.lineTo(W - PAD, c.y)
  ctx.stroke()
  c.y += 56

  // 遗珠徽标
  const isGem = (item.forgotten_score ?? 0) >= 0.6
  if (isGem) {
    ctx.font = `600 26px ${FONT_STACK}`
    const label = '💎 遗珠'
    const w = ctx.measureText(label).width + 44
    ctx.beginPath()
    ctx.roundRect(PAD, c.y, w, 52, 26)
    ctx.fillStyle = '#fff4e5'
    ctx.fill()
    ctx.fillStyle = '#b06a00'
    ctx.textBaseline = 'middle'
    ctx.fillText(label, PAD + 22, c.y + 27)
    ctx.textBaseline = 'top'
    c.y += 78
  }

  // 仓库名（大字）
  drawParagraph(c, item.name, { size: 64, weight: '700', color: INK, lineHeight: 78, maxLines: 2, gapAfter: 4 })
  drawParagraph(c, `${item.owner} · ${item.language ?? '未知语言'}`, {
    size: 27, color: INK_MUTED, lineHeight: 38, maxLines: 1, gapAfter: 24,
  })

  // 指标行（用文字拼，避免再开一块画布）
  ctx.font = `600 34px ${FONT_STACK}`
  ctx.fillStyle = INK
  const stats = `★ ${formatNum(item.stars)}    ⑂ ${formatNum(item.stats?.forks ?? 0)}    匹配度 ${(item.personal_score ?? item.score).toFixed(2)}`
  ctx.fillText(stats, PAD, c.y)
  c.y += 62

  // 分隔线
  ctx.strokeStyle = HAIRLINE
  ctx.beginPath()
  ctx.moveTo(PAD, c.y)
  ctx.lineTo(W - PAD, c.y)
  ctx.stroke()
  c.y += 44

  // 一句话（优先 AI 速读）
  const lead = input.oneLiner?.trim() || item.description || ''
  if (lead) {
    drawParagraph(c, lead, { size: 36, weight: '500', color: INK, lineHeight: 52, maxLines: 4, gapAfter: 22 })
  }

  // 技术栈
  if (input.stack?.length) {
    c.x = PAD
    for (const s of input.stack.slice(0, 6)) chip(c, s)
    c.y += 74
  }

  // 亮点（最多 3 条）
  const highs = (input.highlights ?? []).slice(0, 3)
  if (highs.length) {
    for (const h of highs) {
      ctx.font = `600 30px ${FONT_STACK}`
      ctx.fillStyle = ACCENT
      ctx.fillText('·', PAD, c.y)
      drawParagraph(c, h, { size: 30, color: INK_SOFT, lineHeight: 42, maxLines: 2, gapAfter: 10 })
    }
  }

  // 底部：仓库地址
  ctx.strokeStyle = HAIRLINE
  ctx.beginPath()
  ctx.moveTo(PAD, H - 148)
  ctx.lineTo(W - PAD, H - 148)
  ctx.stroke()
  ctx.font = `500 27px ${FONT_STACK}`
  ctx.fillStyle = INK_MUTED
  ctx.textBaseline = 'top'
  ctx.fillText(`github.com/${item.full_name}`, PAD, H - 116)
  ctx.font = `400 24px ${FONT_STACK}`
  ctx.fillStyle = '#9a9aa0'
  ctx.fillText('RecoFeed · 用抖音信息流范式挖被埋没的开源项目', PAD, H - 74)

  return await new Promise<Blob | null>((resolve) => {
    canvas.toBlob((b) => resolve(b), 'image/png', 0.95)
  })
}

function formatNum(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(n >= 10000 ? 1 : 2)}k`
  return String(n)
}

/** 触发下载（同时尝试写入剪贴板，方便直接粘贴到聊天窗） */
export async function downloadShareCard(item: FeedItem, extra: {
  oneLiner?: string; highlights?: string[]; stack?: string[]
}): Promise<'clipboard' | 'download' | 'failed'> {
  const blob = await makeShareCard({ item, ...extra })
  if (!blob) return 'failed'

  // 优先复制到剪贴板（聊天窗里 Ctrl+V 最顺手）
  try {
    const ClipboardItemCtor = (window as unknown as { ClipboardItem?: typeof ClipboardItem }).ClipboardItem
    if (ClipboardItemCtor && navigator.clipboard?.write) {
      await navigator.clipboard.write([new ClipboardItemCtor({ 'image/png': blob })])
      return 'clipboard'
    }
  } catch {
    /* 剪贴板被拒（非安全上下文/权限）→ 退回下载 */
  }

  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `recofeed-${item.owner}-${item.name}.png`.replace(/[^\w.\-]/g, '_')
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
  return 'download'
}
