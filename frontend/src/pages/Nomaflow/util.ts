// Shared bits for the nomaflow feature-area pages (JobsList + Schedule).
import type { RunState } from './types'

/** Run-state → Tag tone — the colour of a last-run / step-state badge. */
export const STATE_TONE: Record<RunState, 'green' | 'red' | 'orange' | 'blue' | 'neutral'> = {
  SUCCEEDED: 'green', FAILED: 'red', CANCELED: 'orange', RUNNING: 'blue', QUEUED: 'neutral',
}

/** A compact relative-time label — "2h ago" / "in 30m" / "just now". Past and
 *  future both supported (the schedule view shows future fires, the runs view past). */
export function relative(iso: string | null): string {
  if (!iso) return '—'
  const diffMs = Date.now() - new Date(iso).getTime()
  const abs = Math.abs(diffMs)
  const future = diffMs < 0
  const mins = Math.round(abs / 60000)
  if (mins < 1) return future ? 'in <1m' : 'just now'
  if (mins < 60) return future ? `in ${mins}m` : `${mins}m ago`
  const hrs = Math.round(mins / 60)
  if (hrs < 48) return future ? `in ${hrs}h` : `${hrs}h ago`
  const days = Math.round(hrs / 24)
  return future ? `in ${days}d` : `${days}d ago`
}
