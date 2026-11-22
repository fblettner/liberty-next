// What is checked in a job before it is sent: the cheap things the server would refuse anyway —
// no name, a name already taken, a step without what it acts on — found at once and placed on the
// step, instead of a refused save. Mirrors liberty/jobs/schema.py (`Step._check_required_fields`,
// `Job._validate_id`, `JobsFile._unique_job_ids`). No React here.

/** A step as the editor holds it (only what the checks read). */
export interface CheckedStep {
  type: string
  name: string
  [field: string]: unknown
}
export interface CheckedJob {
  id: string
  steps: CheckedStep[]
  presets?: { name?: string; schedule?: string | null }[]
}

export type JobIssueCode =
  | 'idRequired'        // the job has no id
  | 'idCharacters'      // the id has characters a URL cannot carry
  | 'idTaken'           // another job has this id
  | 'stepName'          // a step has no name
  | 'stepDuplicate'     // two steps have the same name
  | 'stepMissing'       // a step lacks what it acts on (``fields``)
  | 'stepUnknownJob'    // a call_job step names a job that does not exist
  | 'stepWindow'        // a report step has one of its two fixed dates only
  | 'presetName'        // a preset has no name
  | 'presetDuplicate'   // two scheduled presets have the same name

export interface JobIssue {
  code: JobIssueCode
  /** The step it is on (its place in the job); absent for the job itself. */
  step?: number
  /** The preset it is on (its place in the job). */
  preset?: number
  /** The fields a step lacks, as the model names them. */
  fields?: string[]
  /** The name in question (a duplicated step name, an unknown job). */
  name?: string
}

// What each kind of step cannot run without.
const REQUIRED: Record<string, string[]> = {
  sql_copy: ['source', 'target'],
  sql_query: ['connector', 'query'],
  python: ['callable'],
  http: ['url'],
  call_job: ['target_job_id'],
  report: ['report'],
  notify: ['recipients', 'subject'],
  file_export: ['connector', 'query'],
  file_load: ['target'],
}

const blank = (v: unknown): boolean =>
  v == null || v === '' || (Array.isArray(v) && v.length === 0)
  || (typeof v === 'object' && !Array.isArray(v) && Object.keys(v as object).length === 0)

/** The fields a step of this kind lacks. An LDAP step reads its directory from a pool or from an
 *  address typed in the step. */
export function missingFields(step: CheckedStep): string[] {
  let required = REQUIRED[step.type] ?? []
  if (step.type === 'ldap_sync') {
    required = blank(step.pool)
      ? ['server', 'bind_dn', 'search_base', 'target_connector', 'target_query']
      : ['target_connector', 'target_query']
  }
  return required.filter((f) => blank(step[f]))
}

/** Everything to correct in *job* before it is saved. *otherIds* are the ids of the other jobs. */
export function checkJob(job: CheckedJob, otherIds: string[]): JobIssue[] {
  const issues: JobIssue[] = []
  const id = job.id.trim()
  if (!id) issues.push({ code: 'idRequired' })
  else if (!/^[\p{L}\p{N}_-]+$/u.test(id)) issues.push({ code: 'idCharacters' })
  else if (otherIds.includes(id)) issues.push({ code: 'idTaken', name: id })
  const seen = new Map<string, number>()
  job.steps.forEach((step, index) => {
    const name = (step.name ?? '').trim()
    if (!name) issues.push({ code: 'stepName', step: index })
    else if (seen.has(name)) issues.push({ code: 'stepDuplicate', step: index, name })
    else seen.set(name, index)
    const missing = missingFields(step)
    if (missing.length) issues.push({ code: 'stepMissing', step: index, fields: missing })
    const target = typeof step.target_job_id === 'string' ? step.target_job_id.trim() : ''
    if (step.type === 'call_job' && target && target !== id && !otherIds.includes(target)) {
      issues.push({ code: 'stepUnknownJob', step: index, name: target })
    }
    if (step.type === 'report' && blank(step.window_from) !== blank(step.window_to)) issues.push({ code: 'stepWindow', step: index })
  })
  // a preset is picked and scheduled by its name: it needs one, and a scheduled one's is its own
  const scheduled = new Set<string>()
  ;(job.presets ?? []).forEach((preset, index) => {
    const name = (preset.name ?? '').trim()
    if (!name) { issues.push({ code: 'presetName', preset: index }); return }
    if (!(preset.schedule ?? '').trim()) return
    if (scheduled.has(name)) issues.push({ code: 'presetDuplicate', preset: index, name })
    scheduled.add(name)
  })
  return issues
}
