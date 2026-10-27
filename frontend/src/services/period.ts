// Periods (no React): the presets a date range filter offers, the range each one covers today,
// the period to compare with (nomaubl's rule), and the label a range shows. ISO dates
// (yyyy-mm-dd) throughout; an empty range means "all dates".

export type DatePreset =
  | 'all' | 'today' | 'yesterday' | 'last7' | 'last30'
  | 'thisMonth' | 'lastMonth' | 'thisQuarter' | 'thisYear' | 'lastYear' | 'last12' | 'custom'

export const DATE_PRESETS: DatePreset[] = ['today', 'yesterday', 'last7', 'last30', 'thisMonth', 'lastMonth', 'thisQuarter', 'thisYear', 'lastYear', 'last12', 'all']

export interface DateRange {
  dateFrom: string
  dateTo: string
}

export const EMPTY_RANGE: DateRange = { dateFrom: '', dateTo: '' }

export function toIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
export function fromIso(s: string): Date {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, (m || 1) - 1, d || 1)
}
const addDays = (d: Date, n: number): Date => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
const monthEnd = (d: Date): Date => new Date(d.getFullYear(), d.getMonth() + 1, 0)
const isMonthEnd = (d: Date): boolean => d.getDate() === monthEnd(d).getDate()
/** Same day *n* months earlier, clamped to that month's length (31 Mar − 1 month = 28 Feb). */
function minusMonths(d: Date, n: number): Date {
  const y = d.getFullYear(), m = d.getMonth() - n
  const last = new Date(y, m + 1, 0).getDate()
  return new Date(y, m, Math.min(d.getDate(), last))
}
const minusYears = (d: Date, n: number): Date => minusMonths(d, 12 * n)
const daysBetween = (a: Date, b: Date): number => Math.round((b.getTime() - a.getTime()) / 86_400_000)

/** The range a preset covers, as of *today* (injectable for tests). */
export function rangeForPreset(preset: DatePreset, today: Date = new Date()): DateRange {
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  switch (preset) {
    case 'today': return { dateFrom: toIso(t), dateTo: toIso(t) }
    // yesterday AND today: a night batch straddles midnight and the default view shows the whole batch
    case 'yesterday': return { dateFrom: toIso(addDays(t, -1)), dateTo: toIso(t) }
    case 'last7': return { dateFrom: toIso(addDays(t, -6)), dateTo: toIso(t) }
    case 'last30': return { dateFrom: toIso(addDays(t, -29)), dateTo: toIso(t) }
    case 'thisMonth': return { dateFrom: toIso(new Date(t.getFullYear(), t.getMonth(), 1)), dateTo: toIso(t) }
    case 'lastMonth': return { dateFrom: toIso(new Date(t.getFullYear(), t.getMonth() - 1, 1)), dateTo: toIso(new Date(t.getFullYear(), t.getMonth(), 0)) }
    case 'thisQuarter': return { dateFrom: toIso(new Date(t.getFullYear(), Math.floor(t.getMonth() / 3) * 3, 1)), dateTo: toIso(t) }
    case 'thisYear': return { dateFrom: toIso(new Date(t.getFullYear(), 0, 1)), dateTo: toIso(t) }
    case 'lastYear': return { dateFrom: toIso(new Date(t.getFullYear() - 1, 0, 1)), dateTo: toIso(new Date(t.getFullYear() - 1, 11, 31)) }
    case 'last12': return { dateFrom: toIso(addDays(minusYears(t, 1), 1)), dateTo: toIso(t) }
    default: return EMPTY_RANGE
  }
}

/** The period to compare with (nomaubl's rule). From 1 January: the same dates a year earlier.
 *  From the 1st of a month: shifted back by the months covered — the previous month (to date)
 *  for a month, the previous quarter for a quarter; a period ending on a month's last day
 *  compares with whole months. Anything else: the same length just before. An empty range
 *  (all dates) has no previous period. */
export function previousPeriod(range: DateRange): DateRange {
  if (!range.dateFrom || !range.dateTo) return EMPTY_RANGE
  const from = fromIso(range.dateFrom), to = fromIso(range.dateTo)
  const toEnd = isMonthEnd(to)
  if (from.getMonth() === 0 && from.getDate() === 1 && to.getFullYear() === from.getFullYear()) {
    const pt = minusYears(to, 1)
    return { dateFrom: toIso(minusYears(from, 1)), dateTo: toIso(toEnd ? monthEnd(pt) : pt) }
  }
  if (from.getDate() === 1) {
    const next = addDays(to, 1)
    let months = (next.getFullYear() - from.getFullYear()) * 12 + (next.getMonth() - from.getMonth())
    if (!toEnd || months === 0) months++
    const pf = minusMonths(from, months)
    let pt = minusMonths(to, months)
    if (toEnd) pt = monthEnd(pt)
    return { dateFrom: toIso(pf), dateTo: toIso(pt) }
  }
  const pt = addDays(from, -1)
  return { dateFrom: toIso(addDays(pt, -daysBetween(from, to))), dateTo: toIso(pt) }
}

/** "1 – 30 Sept 2026", "28 Aug – 3 Sept 2026", "25 Oct 2026"; '' for an empty range. */
export function formatRange(r: DateRange, lang: string): string {
  if (!r.dateFrom && !r.dateTo) return ''
  const a = fromIso(r.dateFrom || r.dateTo), b = fromIso(r.dateTo || r.dateFrom)
  const full = new Intl.DateTimeFormat(lang, { day: 'numeric', month: 'short', year: 'numeric' })
  if (a.getTime() === b.getTime()) return full.format(a)
  const sameYear = a.getFullYear() === b.getFullYear()
  const sameMonth = sameYear && a.getMonth() === b.getMonth()
  const head = sameMonth
    ? String(a.getDate())
    : new Intl.DateTimeFormat(lang, sameYear ? { day: 'numeric', month: 'short' } : { day: 'numeric', month: 'short', year: 'numeric' }).format(a)
  return `${head} – ${full.format(b)}`
}

export const isPreset = (v: unknown): v is DatePreset => typeof v === 'string' && (DATE_PRESETS as string[]).includes(v)
