// Shared bits for the nomaflow feature-area pages (JobsList + Schedule).
import { apiDownload, apiUpload } from '../../api/client'
import type { RunState } from './types'

/** Run-state → Tag tone — the colour of a last-run / step-state badge. */
export const STATE_TONE: Record<RunState, 'green' | 'red' | 'orange' | 'blue' | 'neutral'> = {
  SUCCEEDED: 'green', FAILED: 'red', CANCELED: 'orange', MISSED: 'red', RUNNING: 'blue', QUEUED: 'neutral',
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


/** Send a file to the job's work area (`POST /admin/jobs/{id}/upload`) and get back the path a
 *  `file_load` step reads on this fire. Multipart, so it goes through fetch, not the JSON client. */
export function uploadJobFile(jobId: string, file: File): Promise<{ name: string; path: string; size: number }> {
  return apiUpload(`/admin/jobs/${encodeURIComponent(jobId)}/upload`, file)
}

/** Download a file a step left for a run (the file_load rejects report) through the authenticated API. */
/** The file uploaded for a run's file_load *step*, saved as *name* (admin route). */
export function downloadRunInput(runId: string, step: string, name: string): Promise<void> {
  return apiDownload(`/admin/jobs/runs/${encodeURIComponent(runId)}/inputs/${encodeURIComponent(step)}`, name)
}

export function downloadRunFile(runId: string, name: string): Promise<void> {
  return apiDownload(`/admin/jobs/runs/${encodeURIComponent(runId)}/files/${encodeURIComponent(name)}`, name)
}
