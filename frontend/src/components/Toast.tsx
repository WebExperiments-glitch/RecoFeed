import { useEffect, useState } from 'react'
import type { FC } from 'react'

export interface ToastMsg {
  id: number
  text: string
  tone?: 'info' | 'ok' | 'warn'
}

let seq = 0
const listeners = new Set<(m: ToastMsg) => void>()

/** 全局轻提示。用完即弃，不需要状态管理库。 */
export function toast(text: string, tone: ToastMsg['tone'] = 'info'): void {
  seq += 1
  const msg: ToastMsg = { id: seq, text, tone }
  listeners.forEach((fn) => fn(msg))
}

const TONES: Record<NonNullable<ToastMsg['tone']>, string> = {
  info: 'border-white/12 text-white/80',
  ok: 'border-risk-safe/35 text-risk-safe',
  warn: 'border-risk-caution/40 text-risk-caution',
}

const ToastHost: FC = () => {
  const [list, setList] = useState<ToastMsg[]>([])

  useEffect(() => {
    const fn = (m: ToastMsg): void => {
      setList((prev) => [...prev, m])
      window.setTimeout(() => {
        setList((prev) => prev.filter((x) => x.id !== m.id))
      }, 2200)
    }
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  }, [])

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-24 z-[60] flex flex-col items-center gap-2 px-6">
      {list.map((m) => (
        <div
          key={m.id}
          className={`glass rounded-full px-4 py-2 text-[12.5px] animate-slide-up border ${
            TONES[m.tone ?? 'info']
          }`}
        >
          {m.text}
        </div>
      ))}
    </div>
  )
}

export default ToastHost
