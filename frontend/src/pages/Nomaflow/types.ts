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
  /** Soonest fire across the job's own schedule AND any schedulable preset. */
  next_run: string | null
  /** The job-level schedule's OWN next fire (null when it has no job-level cron). */
  schedule_next_run?: string | null
  /** Schedulable presets: each fires the job on its own cron with its own params. */
  preset_schedules?: PresetSchedule[]
}

export interface PresetSchedule {
  name: string
  schedule: string
  timezone: string | null
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

export type StepType = 'sql_copy' | 'sql_query' | 'python' | 'ldap_sync' | 'http' | 'call_job'

/** One step. Typed loosely — the per-type forms (increment 4–5) narrow it; the
 *  list view only needs `type` + `name`. */
export interface StepConfig {
  type: StepType
  name: string
  /** Step-level enable switch. Defaults to true. When false the runner skips
   *  the step (CANCELED with "skipped: disabled") and continues with the next
   *  one — doesn't fail the run. Operators set this in jobs.toml for
   *  conditionally-relevant steps, OR per-fire via the Run-with-parameters
   *  modal's toggle. */
  enabled?: boolean
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
  /** Job-level kwargs merged UNDER every python step's op_kwargs (step wins on
   *  conflict, run-time override wins over both). For agent jobs (nomasx1-security,
   *  nomasx1-database) that fire N module callables sharing the same apps_id /
   *  source_connector / target_connector, set those once here instead of duplicating
   *  on every step. Defaults to `{}` — irrelevant for jobs whose steps are all
   *  sql_copy / sql_query (those executors don't consume op_kwargs). */
  params?: Record<string, unknown>
  /** Per-run logging verbosity. INFO (default) gives row counts + business
   *  progress markers. DEBUG also emits the full SQL of every run_query.
   *  The Run-with-parameters modal can override this per-fire without editing
   *  the TOML; the per-fire value wins. */
  log_level?: 'WARNING' | 'INFO' | 'DEBUG'
  /** Named snapshots of the Run-with-parameters modal state. The modal
   *  surfaces them as a "Preset" dropdown above the form; picking one
   *  layers its log_level / params / op_kwargs / step_enabled onto the
   *  job's saved defaults. Operators save current state via the modal's
   *  "Save as preset…" button. Empty array hides the dropdown. */
  presets?: JobPreset[]
}

export interface JobPreset {
  name: string
  log_level?: 'WARNING' | 'INFO' | 'DEBUG' | null
  params?: Record<string, unknown>
  op_kwargs?: Record<string, Record<string, unknown>>
  step_enabled?: Record<string, boolean>
  /** Optional cron — when set, the scheduler fires THIS preset on its own schedule,
   *  applying the preset's params/op_kwargs/log_level/step_enabled. One job → many
   *  param'd schedules without cloning. */
  schedule?: string | null
  /** IANA timezone for `schedule`; null → inherit the job's timezone. */
  timezone?: string | null
}

export interface JobsParsedResponse {
  path: string
  jobs: JobConfig[]
}

// ── run detail (GET /admin/jobs/runs/<id>) ───────────────────────────────────────

export interface RunInfo {
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
}

export interface StepRunInfo {
  step_index: number
  step_name: string
  step_type: string
  attempt: number
  state: RunState
  started_at: string | null
  finished_at: string | null
  rows_affected: number | null
  error_message: string | null
  /** Free-form extras the executor attached to the StepResult — today the
   *  only writer is CallJobExecutor, which records
   *  ``{child_run_id, child_job_id}`` so the Run Detail page can render the
   *  step row as a link to the spawned child run. Null when the step's
   *  executor didn't write extras. */
  extras?: {
    child_run_id?: string
    child_job_id?: string
  } | null
}

export interface RunDetailResponse {
  run: RunInfo
  steps: StepRunInfo[]
  /** The run's log text — live in-memory buffer while it's running, the
   *  durable nomaflow_run_logs row once finished. */
  logs: string
  /** Per-fire overrides captured at run start — what the operator picked in
   *  the Run-with-params modal (empty for scheduled fires or runs from
   *  before the RunOverrides table existed). Shape mirrors POST /admin/jobs/<id>/run:
   *  ``{log_level, params, op_kwargs: {step: {k: v}}, step_enabled: {step: bool}}``.
   *  ``parent_chain`` is added by the runner for call_job-spawned children. */
  overrides?: {
    log_level?: 'WARNING' | 'INFO' | 'DEBUG'
    params?: Record<string, unknown>
    op_kwargs?: Record<string, Record<string, unknown>>
    step_enabled?: Record<string, boolean>
    parent_chain?: Array<[string, string]>
  }
}

