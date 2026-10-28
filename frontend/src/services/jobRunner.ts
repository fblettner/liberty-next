// The user side of a nomaflow job (a ``job`` menu item): the shapes ``/api/jobs/{id}`` returns and
// the calls the job page makes. No React here. The permission is ``job:<id>``; the page runs the
// job (one file per ``file_load`` step), follows the run's log and downloads the run's files.
import { api, authHeaders } from '../api/client'
import type { RunState } from '../pages/Nomaflow/types'

export interface JobUpload { step: string; sheet: string | number | null }
export interface JobParam { name: string; label: string; step: string; default: string | null; required: boolean; choices: { value: string; label: string }[] }
export interface UserJob { id: string; description: string | null; tags: string[]; uploads: JobUpload[]; params: JobParam[]; in_flight: boolean }
export interface JobRunRow {
  id: string; job_id: string; state: RunState; trigger_kind: string; triggered_by: string | null
  started_at: string | null; finished_at: string | null; rows_affected: number | null; error_message: string | null
}
export interface JobStepRow { step_index: number; step_name: string; step_type: string; attempt: number; state: RunState; rows_affected: number | null; error_message: string | null }
export interface JobRunDetail { run: JobRunRow; steps: JobStepRow[]; logs: string }

export const ACTIVE_STATES: RunState[] = ['RUNNING', 'QUEUED']
export const isActive = (s: RunState | undefined): boolean => !!s && ACTIVE_STATES.includes(s)

const enc = encodeURIComponent

export function getJob(jobId: string): Promise<{ job: UserJob; runs: JobRunRow[] }> {
  return api.get(`/api/jobs/${enc(jobId)}`)
}

export function getRun(jobId: string, runId: string): Promise<JobRunDetail> {
  return api.get(`/api/jobs/${enc(jobId)}/runs/${enc(runId)}`)
}

/** Multipart, so it goes through fetch rather than the JSON client. */
export async function uploadFile(jobId: string, file: File): Promise<{ name: string; path: string; size: number }> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(`/api/jobs/${enc(jobId)}/upload`, { method: 'POST', headers: { ...authHeaders() }, body: form })
  if (!res.ok) {
    const body = await res.json().catch(() => ({} as Record<string, unknown>))
    throw new Error(body && typeof body === 'object' && 'detail' in body ? String((body as { detail: unknown }).detail) : `upload failed: ${res.status}`)
  }
  return res.json() as Promise<{ name: string; path: string; size: number }>
}

export function runJob(jobId: string, files: Record<string, string>, params: Record<string, string> = {}): Promise<{ run_id: string }> {
  return api.post(`/api/jobs/${enc(jobId)}/run`, { files, params })
}

/** The files a run left (today the file_load rejects report) are named ``<run id>-…``: the log
 *  names them, the page offers them as downloads. */
export function runFileUrl(jobId: string, runId: string, name: string): string {
  return `/api/jobs/${enc(jobId)}/runs/${enc(runId)}/files/${enc(name)}`
}

export async function downloadRunFile(jobId: string, runId: string, name: string): Promise<void> {
  const res = await fetch(runFileUrl(jobId, runId, name), { headers: { ...authHeaders() } })
  if (!res.ok) throw new Error(`download failed: ${res.status}`)
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob); a.download = name; a.click()
  URL.revokeObjectURL(a.href)
}

/** The run files a log mentions: every ``<run id>-<something>.<ext>`` token. */
export function filesInLog(runId: string, logs: string): string[] {
  const re = new RegExp(`${runId.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&')}-[A-Za-z0-9._-]+\\.[a-z0-9]+`, 'g')
  return [...new Set(logs.match(re) ?? [])]
}

export function durationOf(run: Pick<JobRunRow, 'started_at' | 'finished_at'>): string {
  if (!run.started_at) return '—'
  const end = run.finished_at ? new Date(run.finished_at).getTime() : Date.now()
  const s = Math.max(0, Math.round((end - new Date(run.started_at).getTime()) / 1000))
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  return m < 60 ? `${m}m ${s % 60}s` : `${Math.floor(m / 60)}h ${m % 60}m`
}
