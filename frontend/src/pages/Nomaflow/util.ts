// Shared bits for the nomaflow feature-area pages (JobsList + Schedule).
import i18n from 'i18next'
import { apiDownload, apiUpload } from '../../api/client'
import type { RunState } from './types'

/** Run-state → Tag tone — the colour of a last-run / step-state badge. */
export const STATE_TONE: Record<RunState, 'green' | 'red' | 'orange' | 'blue' | 'neutral'> = {
  SUCCEEDED: 'green', FAILED: 'red', CANCELED: 'orange', MISSED: 'red', RUNNING: 'blue', QUEUED: 'neutral',
}

/** A compact relative-time label in the language of the interface — "2 hr. ago" / "in 30 min." /
 *  "il y a 2 h" / "dans 30 min". Past and future both supported (the schedule view shows future fires,
 *  the runs view past). *lang* is for tests; the interface's language otherwise. */
export function relative(iso: string | null, lang?: string): string {
  if (!iso) return '—'
  const diffMs = Date.now() - new Date(iso).getTime()
  const sign = diffMs < 0 ? 1 : -1                     // the future counts up, the past down
  const mins = Math.round(Math.abs(diffMs) / 60000)
  const say = new Intl.RelativeTimeFormat(lang || i18n.language || 'en', { numeric: 'auto', style: 'short' })
  if (mins < 1) return say.format(0, 'second')         // "now" / "maintenant"
  if (mins < 60) return say.format(sign * mins, 'minute')
  const hrs = Math.round(mins / 60)
  if (hrs < 48) return say.format(sign * hrs, 'hour')
  return say.format(sign * Math.round(hrs / 24), 'day')
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
