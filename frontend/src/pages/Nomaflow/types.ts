// nomaflow API shapes — see liberty/web/jobs.py (GET /admin/jobs) and
// liberty/web/admin.py (GET/PUT /admin/config/jobs/parsed). The UI design is
// docs/NOMAFLOW-UI.md.

export type RunState = 'QUEUED' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELED'

/** The latest run of a job — the `last_run` badge on the Jobs list. */
export interface JobLastRun {
  run_id: string
  state: RunState
  started_at: string | null
  finished_at: string | null
  rows_affected: number | null
  error_message: string | null
}

/** One job's operational view — `GET /admin/jobs`. Catalogue fields + scheduler
 *  flags + the last-run badge + the next scheduled fire. */
export interface JobSummary {
  id: string
  description: string | null
  schedule: string | null
  timezone: string | null
  enabled: boolean
  tags: string[]
  step_count: number
  registered_with_scheduler: boolean
  in_flight: boolean
  last_run: JobLastRun | null
  next_run: string | null
}

export interface JobsListResponse {
  jobs: JobSummary[]
  active_run_ids: string[]
}

// ── full job config (the editor — increment 3+) ──────────────────────────────────

export interface JobRetry {
  attempts: number
  backoff: 'fixed' | 'exponential'
  base_seconds: number
}

export interface JobAlerts {
  on_failure: boolean
  on_long_run_minutes: number | null
  recipients: string[]
}

export type StepType = 'sql_copy' | 'sql_query' | 'python' | 'ldap_sync' | 'http'

/** One step. Typed loosely — the per-type forms (increment 4–5) narrow it; the
 *  list view only needs `type` + `name`. */
export interface StepConfig {
  type: StepType
  name: string
  [field: string]: unknown
}

/** The full job config — `GET/PUT /admin/config/jobs/parsed`. */
export interface JobConfig {
  id: string
  description?: string | null
  schedule?: string | null
  timezone?: string | null
  enabled?: boolean
  tags?: string[]
  retry?: JobRetry | null
  alerts?: JobAlerts | null
  steps: StepConfig[]
  timeout_seconds?: number
}

export interface JobsParsedResponse {
  path: string
  jobs: JobConfig[]
}
