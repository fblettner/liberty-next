// A job's schedule as a person says it — every 15 minutes, every 2 hours, on Mondays and
// Thursdays at 06:30, the 1st of the month at 02:00 — and the cron expression the scheduler
// takes. The editor shows the plan; the cron is what is saved. A cron the plans do not cover
// (ranges, lists of hours, months…) is kept as it is, typed. No React here.

export type SchedulePlan =
  | { kind: 'manual' }
  /** Every *every* minutes. */
  | { kind: 'minutes'; every: number }
  /** Every *every* hours, at *minute* past the hour. */
  | { kind: 'hours'; every: number; minute: number }
  /** At *time* on the *days* (0 = Sunday … 6 = Saturday); no day = every day. */
  | { kind: 'days'; days: number[]; time: string }
  /** At *time* on the *day* of each month. */
  | { kind: 'month'; day: number; time: string }
  /** Anything else: the cron expression itself. */
  | { kind: 'custom'; cron: string }

export type PlanKind = SchedulePlan['kind']
export const PLAN_KINDS: PlanKind[] = ['manual', 'minutes', 'hours', 'days', 'month', 'custom']

const pad = (n: number) => String(n).padStart(2, '0')
const int = (s: string, lo: number, hi: number): number | null => {
  if (!/^\d+$/.test(s)) return null
  const n = Number(s)
  return n >= lo && n <= hi ? n : null
}
const clamp = (n: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, Math.trunc(Number.isFinite(n) ? n : lo)))

/** ``HH:MM`` → [hour, minute]; anything else → 02:00. */
function hm(time: string): [number, number] {
  const m = /^(\d{1,2}):(\d{2})$/.exec(time.trim())
  return m ? [clamp(Number(m[1]), 0, 23), clamp(Number(m[2]), 0, 59)] : [2, 0]
}

/** The cron expression of *plan*; null for a job that is only started by hand. */
export function planToCron(plan: SchedulePlan): string | null {
  switch (plan.kind) {
    case 'manual': return null
    case 'minutes': { const n = clamp(plan.every, 1, 59); return n === 1 ? '* * * * *' : `*/${n} * * * *` }
    case 'hours': { const n = clamp(plan.every, 1, 23); return `${clamp(plan.minute, 0, 59)} ${n === 1 ? '*' : `*/${n}`} * * *` }
    case 'days': {
      const [h, m] = hm(plan.time)
      const days = [...new Set(plan.days.map((d) => clamp(d, 0, 7) % 7))].sort((a, b) => a - b)
      return `${m} ${h} * * ${days.length === 0 || days.length === 7 ? '*' : days.join(',')}`
    }
    case 'month': { const [h, m] = hm(plan.time); return `${m} ${h} ${clamp(plan.day, 1, 31)} * *` }
    case 'custom': return plan.cron.trim() || null
  }
}

/** The plan a cron expression says; ``custom`` when no plan says it. */
export function cronToPlan(cron: string | null | undefined): SchedulePlan {
  const text = (cron ?? '').trim()
  if (!text) return { kind: 'manual' }
  const f = text.split(/\s+/)
  if (f.length !== 5) return { kind: 'custom', cron: text }
  const [mi, h, dom, mon, dow] = f
  if (mon !== '*') return { kind: 'custom', cron: text }
  const everyMin = /^\*\/(\d+)$/.exec(mi)
  if (h === '*' && dom === '*' && dow === '*') {
    if (mi === '*') return { kind: 'minutes', every: 1 }
    if (everyMin && int(everyMin[1], 1, 59) != null) return { kind: 'minutes', every: Number(everyMin[1]) }
  }
  const minute = int(mi, 0, 59)
  if (minute == null) return { kind: 'custom', cron: text }
  const everyHour = /^\*\/(\d+)$/.exec(h)
  if (dom === '*' && dow === '*') {
    if (h === '*') return { kind: 'hours', every: 1, minute }
    if (everyHour && int(everyHour[1], 1, 23) != null) return { kind: 'hours', every: Number(everyHour[1]), minute }
  }
  const hour = int(h, 0, 23)
  if (hour == null) return { kind: 'custom', cron: text }
  const time = `${pad(hour)}:${pad(minute)}`
  if (dom === '*') {
    if (dow === '*') return { kind: 'days', days: [], time }
    if (dow === '1-5') return { kind: 'days', days: [1, 2, 3, 4, 5], time }
    const days = dow.split(',').map((d) => int(d, 0, 7))
    if (days.every((d) => d != null)) return { kind: 'days', days: [...new Set(days.map((d) => (d as number) % 7))].sort((a, b) => a - b), time }
    return { kind: 'custom', cron: text }
  }
  const day = int(dom, 1, 31)
  if (day != null && dow === '*') return { kind: 'month', day, time }
  return { kind: 'custom', cron: text }
}

/** The plan a kind starts from when it is picked — keeping the time of the plan it replaces. */
export function startPlan(kind: PlanKind, from: SchedulePlan): SchedulePlan {
  const time = from.kind === 'days' || from.kind === 'month' ? from.time : '02:00'
  switch (kind) {
    case 'manual': return { kind }
    case 'minutes': return { kind, every: 15 }
    case 'hours': return { kind, every: 1, minute: 0 }
    case 'days': return { kind, days: [], time }
    case 'month': return { kind, day: 1, time }
    case 'custom': return { kind, cron: planToCron(from) ?? '' }
  }
}
