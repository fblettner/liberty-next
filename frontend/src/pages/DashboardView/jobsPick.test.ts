import { describe, expect, it } from 'vitest'
import type { JobSummary } from '../Nomaflow/types'
import { pickJobs } from './jobsPick'

const sched = (name: string, schedule: string, enabled = true, next_run: string | null = null) =>
  ({ name, schedule, enabled, next_run, timezone: null, preset: null, pool: null })
const job = (id: string, extra: Partial<JobSummary> = {}): JobSummary => ({
  id, description: null, schedules: [], scheduled: false, tags: [], next_run: null, last_run: null, in_flight: false, ...extra,
} as unknown as JobSummary)

describe('pickJobs', () => {
  const all = [
    job('manual'),
    job('later', { schedules: [sched('later', '0 6 * * *')], scheduled: true, next_run: '2026-10-29T06:00:00Z', tags: ['nomasx1'] }),
    job('soon', { schedules: [sched('soon', '0 3 * * *')], scheduled: true, next_run: '2026-10-29T03:00:00Z' }),
    job('paused', { schedules: [sched('paused', '0 1 * * *', false)], next_run: null, tags: ['nomasx1'] }),
    job('preset', { schedules: [sched('p', '0 2 * * *', true, '2026-10-29T02:00:00Z')], scheduled: true, next_run: '2026-10-29T02:00:00Z' }),
  ]
  it('lists the scheduled jobs, soonest first, the paused ones last', () => {
    expect(pickJobs(all, {}).map((j) => j.id)).toEqual(['preset', 'soon', 'later', 'paused'])
  })
  it('narrows to tags, keeps a named order, caps the rows', () => {
    expect(pickJobs(all, { tags: ['nomasx1'] }).map((j) => j.id)).toEqual(['later', 'paused'])
    expect(pickJobs(all, { jobs: ['manual', 'ghost', 'soon'] }).map((j) => j.id)).toEqual(['manual', 'soon'])
    expect(pickJobs(all, { max_rows: 2 }).map((j) => j.id)).toEqual(['preset', 'soon'])
  })
})
