import { describe, expect, it } from 'vitest'
import { baseParams, newScheduleName, paramValue, scheduleOf, scheduleProblem, withSchedule, withoutSchedule } from './jobSchedules'
import type { JobConfig } from '../pages/Nomaflow/types'

const jobs: JobConfig[] = [
  {
    id: 'collect', label: 'Collect everything', steps: [], params: { apps_id: 1, source_connector: 'jdedwards' },
    presets: [{ name: 'Usage only', params: { mode: 'usage' }, step_enabled: { A: false } }],
    schedules: [{ name: 'Production nightly', schedule: '0 2 * * *', enabled: true, preset: 'Usage only', pool: 'jde_prd', params: { apps_id: 2 } }],
  },
  { id: 'report', steps: [] },
]

describe('jobSchedules', () => {
  it('reads a schedule by its name', () => {
    expect(scheduleOf(jobs, 'collect', 'Production nightly')?.pool).toBe('jde_prd')
    expect(scheduleOf(jobs, 'collect', 'Nope')).toBeNull()
    expect(scheduleOf(jobs, 'collect', null)).toBeNull()
  })

  it('runs with the job\'s parameters, then its preset\'s', () => {
    expect(baseParams(jobs[0], 'Usage only')).toEqual({ apps_id: 1, source_connector: 'jdedwards', mode: 'usage' })
    expect(baseParams(jobs[0], null)).toEqual({ apps_id: 1, source_connector: 'jdedwards' })
  })

  it('adds a schedule that keeps only the values that differ', () => {
    const next = withSchedule(jobs, 'report', null, { name: ' Monthly ', schedule: '0 6 1 * *', enabled: true, params: {} })
    expect(next[1].schedules).toEqual([{ name: 'Monthly', schedule: '0 6 1 * *', enabled: true }])
    const two = withSchedule(jobs, 'collect', null, { name: 'Test weekly', schedule: '0 4 * * 1', enabled: false, pool: 'jde_test',
      params: { apps_id: '1,3', source_connector: 'jdedwards' } })
    expect(two[0].schedules?.[1]).toEqual({ name: 'Test weekly', schedule: '0 4 * * 1', enabled: false, pool: 'jde_test', params: { apps_id: '1,3' } })
    expect(two[0].schedules?.[0]).toEqual(jobs[0].schedules?.[0])
    expect(two[1]).toBe(jobs[1])
  })

  it('changes and renames a schedule in its place', () => {
    const next = withSchedule(jobs, 'collect', 'Production nightly',
      { name: 'Production', schedule: '30 2 * * *', enabled: true, preset: 'Usage only', pool: 'jde_prd', params: { apps_id: 1, mode: 'usage' } })
    expect(next[0].schedules).toEqual([{ name: 'Production', schedule: '30 2 * * *', enabled: true, preset: 'Usage only', pool: 'jde_prd' }])
  })

  it('says what is missing', () => {
    const ok = { name: 'x', schedule: '0 1 * * *', enabled: true }
    expect(scheduleProblem(jobs, 'nope', null, ok)).toBe('nomaflow.scheduleJob.needJob')
    expect(scheduleProblem(jobs, 'collect', null, { ...ok, name: ' ' })).toBe('nomaflow.scheduleJob.needName')
    expect(scheduleProblem(jobs, 'collect', null, { ...ok, name: 'Production nightly' })).toBe('nomaflow.scheduleJob.nameTaken')
    expect(scheduleProblem(jobs, 'collect', 'Production nightly', { ...ok, name: 'Production nightly' })).toBeNull()      // its own name
    expect(scheduleProblem(jobs, 'collect', null, { ...ok, schedule: '' })).toBe('nomaflow.scheduleJob.needWhen')
  })

  it('removes a schedule and keeps the job', () => {
    expect(withoutSchedule(jobs, 'collect', 'Production nightly')[0].schedules).toEqual([])
  })

  it('offers a name for a new schedule', () => {
    expect(newScheduleName(jobs[1])).toBe('report')
    expect(newScheduleName({ id: 'c', label: 'Collect', schedules: [{ name: 'Collect', schedule: '' }, { name: 'Collect 2', schedule: '' }] })).toBe('Collect 3')
  })

  it('types a value as the job\'s own', () => {
    expect(paramValue(' 3 ', 1)).toBe(3)
    expect(paramValue('1,3', 1)).toBe('1,3')
    expect(paramValue('all', 1)).toBe('all')
  })
})
