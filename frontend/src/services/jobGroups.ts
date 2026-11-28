// The jobs list's categories — pure logic, no React. A job names its category (`category`): one of the
// framework's own, listed here in the order they are shown, or a name of the installation's own (shown as
// written, after the framework's, by name). A job without one is listed last, under "Other".

/** The framework's categories, in the order the list shows them (liberty/jobs/schema.py JOB_CATEGORIES). */
export const JOB_CATEGORIES = ['daily', 'load', 'analysis', 'report', 'monitoring', 'tool', 'maintenance', 'demo'] as const

/** The key of the group of the jobs that name no category. */
export const NO_CATEGORY = ''

/** The categories that open closed: what a user of the application does not start by hand every day. */
export const CLOSED_BY_DEFAULT: readonly string[] = ['maintenance', 'demo']

export interface Categorised { id: string; label?: string | null; category?: string | null }

/** A job's category as a key: trimmed, the framework's in lower case; '' when it names none. */
export function categoryOf(job: Categorised): string {
  const said = (job.category ?? '').trim()
  const known = JOB_CATEGORIES.find((c) => c === said.toLowerCase())
  return known ?? said
}

export const isKnownCategory = (category: string): boolean => (JOB_CATEGORIES as readonly string[]).includes(category)

/** Where a category stands: the framework's in their order, then the others by name, then "Other". */
function rank(category: string): number {
  if (category === NO_CATEGORY) return JOB_CATEGORIES.length + 1
  const at = (JOB_CATEGORIES as readonly string[]).indexOf(category)
  return at < 0 ? JOB_CATEGORIES.length : at
}

/** What a job is called in the list: its name, else its id. */
export const jobName = (job: Categorised): string => (job.label ?? '').trim() || job.id

/** *jobs* as the list shows them: one group per category, in order; inside, by name. */
export function byCategory<T extends Categorised>(jobs: readonly T[]): { category: string; jobs: T[] }[] {
  const groups = new Map<string, T[]>()
  for (const job of jobs) {
    const key = categoryOf(job)
    groups.set(key, [...(groups.get(key) ?? []), job])
  }
  return [...groups.entries()]
    .sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b))
    .map(([category, list]) => ({ category, jobs: [...list].sort((a, b) => jobName(a).localeCompare(jobName(b)) || a.id.localeCompare(b.id)) }))
}

/** The categories a job may be given in the editor: the framework's, then those the other jobs already name. */
export function categoryChoices(jobs: readonly Categorised[]): string[] {
  const own = [...new Set(jobs.map(categoryOf).filter((c) => c && !isKnownCategory(c)))].sort((a, b) => a.localeCompare(b))
  return [...JOB_CATEGORIES, ...own]
}
