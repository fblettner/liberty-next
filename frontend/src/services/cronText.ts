// A plain-language reading of the common cron shapes, for the schedule views ("every day at
// 03:00" beside `0 3 * * *`). Returns an i18n key + its values, or null for any shape it does
// not cover (lists, ranges other than Mon–Fri, steps on hours…) — the caller then shows the
// raw expression alone. No React here.

export interface CronText { key: string; values: Record<string, string | number> }

const pad = (n: number) => String(n).padStart(2, '0')
const num = (s: string, lo: number, hi: number): number | null => {
  if (!/^\d+$/.test(s)) return null
  const n = Number(s)
  return n >= lo && n <= hi ? n : null
}

/** The weekday name of cron day *d* (0 or 7 = Sunday) in *locale*. */
export function weekdayName(d: number, locale: string): string {
  // 2026-11-01 is a Sunday: add d days.
  return new Date(Date.UTC(2026, 10, 1 + (d % 7))).toLocaleDateString(locale, { weekday: 'long', timeZone: 'UTC' })
}

export function describeCron(expr: string | null | undefined, locale = 'en'): CronText | null {
  const f = (expr ?? '').trim().split(/\s+/)
  if (f.length !== 5) return null
  const [mi, h, dom, mon, dow] = f
  if (mon !== '*') return null
  const step = /^\*\/(\d+)$/.exec(mi)
  if (step && h === '*' && dom === '*' && dow === '*') return { key: 'cron.everyMinutes', values: { n: Number(step[1]) } }
  if (mi === '*' && h === '*' && dom === '*' && dow === '*') return { key: 'cron.everyMinute', values: {} }
  const m = num(mi, 0, 59)
  if (m == null) return null
  if (h === '*' && dom === '*' && dow === '*') return { key: 'cron.hourly', values: { m: pad(m) } }
  const hh = num(h, 0, 23)
  if (hh == null) return null
  const time = `${pad(hh)}:${pad(m)}`
  if (dom === '*' && dow === '*') return { key: 'cron.daily', values: { time } }
  if (dom === '*' && (dow === '1-5' || dow === 'MON-FRI')) return { key: 'cron.weekdays', values: { time } }
  const d = num(dow, 0, 7)
  if (dom === '*' && d != null) return { key: 'cron.weekly', values: { day: weekdayName(d, locale), time } }
  const day = num(dom, 1, 31)
  if (day != null && dow === '*') return { key: 'cron.monthly', values: { day, time } }
  return null
}
