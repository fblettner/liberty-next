// The jobs widget's plain logic: which jobs it shows and in what order. No React here.
import type { JobSummary } from '../Nomaflow/types'

export interface JobsWidgetSpec { jobs?: string[]; tags?: string[]; max_rows?: number }

/** The jobs a widget lists: the ids it names, in that order; else the jobs with a schedule (their
 *  own or a preset's), narrowed to ``tags`` when given — those that fire next first, the paused
 *  ones after (by id). */
export function pickJobs(all: JobSummary[], spec: JobsWidgetSpec): JobSummary[] {
  const max = spec.max_rows ?? 12
  if (spec.jobs && spec.jobs.length) {
    const byId = new Map(all.map((j) => [j.id, j]))
    return spec.jobs.map((id) => byId.get(id)).filter((j): j is JobSummary => !!j).slice(0, max)
  }
  const tags = spec.tags ?? []
  const scheduled = all.filter((j) => (j.schedule || (j.preset_schedules?.length ?? 0) > 0) && (!tags.length || j.tags.some((t) => tags.includes(t))))
  return scheduled.sort((a, b) => {
    const na = a.next_run, nb = b.next_run
    if (na && nb) return na < nb ? -1 : na > nb ? 1 : a.id.localeCompare(b.id)
    if (na) return -1
    if (nb) return 1
    return a.id.localeCompare(b.id)
  }).slice(0, max)
}
