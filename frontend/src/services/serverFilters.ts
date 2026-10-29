// The data screen's advanced filters (no React): the value of each filter field, the binds a run
// sends for them, which columns filter by a period, and the periods a screen opens on.
//
// A field is ``{op, val}`` — sent as ``<col>`` + ``<col>_op`` (the server's filter wrap) — or, on
// a date column, a period ``{op: 'period', val: from, to, preset}`` sent as ``<col>_from`` /
// ``<col>_to`` (the server bounds the column with ``>=`` / ``<=``, a date-only end on a date-time
// column covering its whole day — the same binds a dashboard's period sends).
import type { Column } from '../types/connectors'
import { rangeForPreset, type DatePreset } from './period'

export interface ServerFilter {
  op: string
  val: string
  /** A period's end (``op = 'period'``; ``val`` is its start). */
  to?: string
  /** The preset the period was picked from (``custom`` for calendar dates). */
  preset?: DatePreset
}

const DATE_FORMATS = new Set(['date', 'datetime', 'timestamp'])

/** What tells a date column: its format, else its database type. */
type Typed = { format?: string | null; type?: string | null }

/** A date column — its filter is a period. */
export function isDateColumn(c: Typed): boolean {
  const f = (c.format ?? '').toLowerCase()
  const t = (c.type ?? '').toLowerCase()
  return DATE_FORMATS.has(f) || /\bdate\b|timestamp/.test(t)
}

/** The binds a run sends for the filters: a period as ``<col>_from`` / ``<col>_to`` (either may be
 *  open), any other field as ``<col>`` + ``<col>_op``; empty fields send nothing. */
export function filterBinds(filters: Record<string, ServerFilter>): Record<string, string> {
  const sent: Record<string, string> = {}
  for (const [name, f] of Object.entries(filters)) {
    if (f.op === 'period') {
      if (f.val) sent[`${name}_from`] = f.val
      if (f.to) sent[`${name}_to`] = f.to
    } else if (f.val !== '') {
      sent[name] = f.val
      sent[`${name}_op`] = f.op
    }
  }
  return sent
}

/** The field a drill / link value seeds: on a date column, a date-only value is that one day as a
 *  period (``col = '2026-10-29'`` never matches a date-time column); anything else an exact match. */
export function seededFilter(c: Typed | undefined, value: string): ServerFilter {
  if (c && isDateColumn(c) && /^\d{4}-\d{2}-\d{2}$/.test(value.trim())) {
    const day = value.trim()
    return { op: 'period', val: day, to: day, preset: 'custom' }
  }
  return { op: 'equals', val: value }
}

/** A field that filters (a period with either bound, or a value). */
export const isActiveFilter = (f: ServerFilter | undefined): boolean => !!f && (f.val !== '' || !!f.to)

/** The period each date filter opens on (its column's ``filter_period``), as of *today*. */
export function defaultPeriods(cols: Column[], today: Date = new Date()): Record<string, ServerFilter> {
  const out: Record<string, ServerFilter> = {}
  for (const c of cols) {
    if (!c.filter || !c.filter_period || c.filter_period === 'all' || !isDateColumn(c)) continue
    const r = rangeForPreset(c.filter_period, today)
    out[c.name] = { op: 'period', val: r.dateFrom, to: r.dateTo, preset: c.filter_period }
  }
  return out
}
