// A date-time value as the record form edits it (no React): split into a day (yyyy-mm-dd) and a
// time (hh:mm:ss), joined back as ``yyyy-mm-dd hh:mm:ss`` — what the server reads for a
// ``datetime`` / ``timestamp`` column. A value carrying its zone (a PostgreSQL ``timestamptz``) is
// shown in the browser's local time, like the grid.
import { localDateTime } from './cells'

/** ``{date, time}`` of a raw date-time; empty parts when *raw* is empty or not a date-time. */
export function splitDateTime(raw: unknown): { date: string; time: string } {
  if (raw == null || raw === '') return { date: '', time: '' }
  const s = String(raw).trim()
  const local = localDateTime(s) ?? s
  const m = /^(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2})(?::(\d{2}))?)?/.exec(local)
  if (!m) return { date: '', time: '' }
  return { date: m[1], time: m[2] ? `${m[2]}:${m[3]}:${m[4] ?? '00'}` : '' }
}

/** A time typed hh:mm or hh:mm:ss (00-23 / 00-59), normalised to hh:mm:ss; null when it is not one. */
export function normaliseTime(v: string): string | null {
  const m = /^(\d{1,2}):(\d{2})(?::(\d{2}))?$/.exec(v.trim())
  if (!m) return null
  const [h, mi, se] = [Number(m[1]), Number(m[2]), Number(m[3] ?? '0')]
  if (h > 23 || mi > 59 || se > 59) return null
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(h)}:${p(mi)}:${p(se)}`
}

/** ``yyyy-mm-dd hh:mm:ss`` (the time 00:00:00 when none); '' without a day. */
export function joinDateTime(date: string, time: string): string {
  if (!date) return ''
  return `${date} ${normaliseTime(time) ?? '00:00:00'}`
}
