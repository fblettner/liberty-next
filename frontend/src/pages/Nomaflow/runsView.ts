// The Job Runs page's plain logic: durations as text, the runs grouped by calendar day, the
// per-fire parameters as short labels. No React here.
import type { RunState } from './types'

export interface RunRow {
  id: string
  job_id: string
  state: RunState
  trigger_kind: string
  triggered_by: string | null
  scheduled_at: string | null
  started_at: string | null
  finished_at: string | null
  rows_affected: number | null
  error_message: string | null
  params?: Record<string, unknown>
  progress?: { done: number; total: number }
}

/** 42 → "42 s", 125 → "2 min 5 s", 3725 → "1 h 2 min". */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—'
  const s = Math.round(seconds)
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m < 60) return s % 60 ? `${m} min ${s % 60} s` : `${m} min`
  const h = Math.floor(m / 60)
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`
}

/** Seconds from start to finish — to *now* while the run is still going. */
export function runSeconds(r: Pick<RunRow, 'started_at' | 'finished_at'>, now = Date.now()): number | null {
  if (!r.started_at) return null
  const end = r.finished_at ? new Date(r.finished_at).getTime() : now
  return Math.max(0, (end - new Date(r.started_at).getTime()) / 1000)
}

const when = (r: RunRow) => r.started_at ?? r.scheduled_at

/** The runs (already newest first) cut into calendar days: ``key`` = local YYYY-MM-DD,
 *  ``offset`` = days before *now* (0 today, 1 yesterday) for the caller's labels. */
export function groupByDay(runs: RunRow[], now = new Date()): { key: string; date: Date; offset: number; runs: RunRow[] }[] {
  const out: { key: string; date: Date; offset: number; runs: RunRow[] }[] = []
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
  for (const r of runs) {
    const iso = when(r)
    const d = iso ? new Date(iso) : now
    const key = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    const last = out[out.length - 1]
    if (last && last.key === key) last.runs.push(r)
    else out.push({ key, date: d, offset: Math.round((midnight(now) - midnight(d)) / 86_400_000), runs: [r] })
  }
  return out
}

/** The per-fire parameters worth showing on a row: ``apps_id 3``, ``lang fr`` (lists joined). */
export function paramLabels(params: Record<string, unknown> | undefined): string[] {
  return Object.entries(params ?? {})
    .filter(([, v]) => v !== null && v !== '' && typeof v !== 'object' || Array.isArray(v))
    .map(([k, v]) => `${k} ${Array.isArray(v) ? v.join(', ') : String(v)}`)
}
