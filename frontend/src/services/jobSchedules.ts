// "Schedule a job" — pure logic, no React. A job runs on its schedules (`job.schedules`): each has a name, when it
// runs, whether it is active, and what it runs with — one of the job's presets or the job as it is, its own values
// of the job's shared parameters, its environment. One job, as many schedules as needed, no copy. The Schedule page
// and the job editor set, change, rename and remove one through these helpers.
import type { JobConfig, JobScheduleConfig } from '../pages/Nomaflow/types'

/** A parameter's value as text, for a field; a list as its values separated by commas. */
export const paramText = (value: unknown): string => (value == null ? '' : Array.isArray(value) ? value.join(', ') : String(value))

/** What was typed for a parameter, as the job's own value is typed: a number stays a number, yes / no stays one. */
export function paramValue(typed: string, like: unknown): unknown {
  const said = typed.trim()
  if (typeof like === 'number' && said !== '' && Number.isFinite(Number(said))) return Number(said)
  if (typeof like === 'boolean' && /^(true|false)$/i.test(said)) return said.toLowerCase() === 'true'
  return said
}

const same = (a: unknown, b: unknown): boolean => paramText(a) === paramText(b)

/** The values a schedule runs with before its own: the job's shared parameters, then its preset's over them. */
export function baseParams(job: JobConfig, preset: string | null | undefined): Record<string, unknown> {
  const p = preset ? (job.presets ?? []).find((x) => x.name === preset) : undefined
  return { ...(job.params ?? {}), ...(p?.params ?? {}) }
}

/** The schedule *name* of *jobId* as the file holds it — null when there is none. */
export function scheduleOf(jobs: readonly JobConfig[], jobId: string, name: string | null): JobScheduleConfig | null {
  if (!name) return null
  return (jobs.find((j) => j.id === jobId)?.schedules ?? []).find((s) => s.name === name) ?? null
}

/** Why *values* cannot be saved as the schedule *was* of *jobId* (null = a new one) — a translation key, or null. */
export function scheduleProblem(jobs: readonly JobConfig[], jobId: string, was: string | null, values: JobScheduleConfig): string | null {
  const job = jobs.find((j) => j.id === jobId)
  if (!job) return 'nomaflow.scheduleJob.needJob'
  const name = values.name.trim()
  if (!name) return 'nomaflow.scheduleJob.needName'
  if (name !== was && (job.schedules ?? []).some((s) => s.name === name)) return 'nomaflow.scheduleJob.nameTaken'
  if (!(values.schedule ?? '').trim()) return 'nomaflow.scheduleJob.needWhen'
  return null
}

/** *jobs* with the schedule *was* of *jobId* replaced by *values* (added when *was* is null or not there). Its
 *  parameters keep only what differs from what it runs with otherwise (the job's, its preset's): a value left as
 *  those, or empty, follows them. */
export function withSchedule(jobs: readonly JobConfig[], jobId: string, was: string | null, values: JobScheduleConfig): JobConfig[] {
  return jobs.map((job) => {
    if (job.id !== jobId) return job
    const preset = (values.preset ?? '').trim() || null
    const under = baseParams(job, preset)
    const params: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(values.params ?? {})) {
      if (paramText(value) !== '' && !same(value, under[key])) params[key] = value
    }
    const entry: JobScheduleConfig = {
      name: values.name.trim(), schedule: (values.schedule ?? '').trim(), enabled: values.enabled ?? true,
      ...(preset ? { preset } : {}), ...((values.pool ?? '').trim() ? { pool: values.pool!.trim() } : {}),
      ...((values.timezone ?? '').trim() ? { timezone: values.timezone!.trim() } : {}),
      ...(Object.keys(params).length ? { params } : {}),
    }
    const list = job.schedules ?? []
    const at = was == null ? -1 : list.findIndex((s) => s.name === was)
    return { ...job, schedules: at < 0 ? [...list, entry] : list.map((s, i) => (i === at ? entry : s)) }
  })
}

/** *jobs* without the schedule *name* of *jobId*: the job stays, run by hand when it has no other. */
export function withoutSchedule(jobs: readonly JobConfig[], jobId: string, name: string): JobConfig[] {
  return jobs.map((job) => (job.id === jobId ? { ...job, schedules: (job.schedules ?? []).filter((s) => s.name !== name) } : job))
}

/** The schedule's name a new one is offered: the job's name for its first, else the job's name and a number. */
export function newScheduleName(job: Pick<JobConfig, 'id' | 'label' | 'schedules'>): string {
  const base = (job.label ?? '').trim() || job.id
  const taken = new Set((job.schedules ?? []).map((s) => s.name))
  if (!taken.has(base)) return base
  let n = 2
  while (taken.has(`${base} ${n}`)) n += 1
  return `${base} ${n}`
}
