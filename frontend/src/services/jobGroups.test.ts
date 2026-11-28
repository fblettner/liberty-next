import { describe, expect, it } from 'vitest'
import { byCategory, categoryChoices, categoryOf, jobName, NO_CATEGORY } from './jobGroups'

describe('jobGroups', () => {
  const jobs = [
    { id: 'z-init', label: 'Create the tables', category: 'maintenance' },
    { id: 'a-report', label: 'Monthly report', category: 'report' },
    { id: 'b-collect', label: 'Collect everything', category: 'Daily' },
    { id: 'c-other' },
    { id: 'd-own', category: 'Closing' },
    { id: 'e-report', label: 'Annual report', category: ' report ' },
  ]

  it('names a job by its label, else its id', () => {
    expect(jobName(jobs[0])).toBe('Create the tables')
    expect(jobName(jobs[3])).toBe('c-other')
    expect(jobName({ id: 'x', label: '  ' })).toBe('x')
  })

  it("reads the framework's categories whatever their case, and keeps an installation's own as written", () => {
    expect(categoryOf(jobs[2])).toBe('daily')
    expect(categoryOf(jobs[4])).toBe('Closing')
    expect(categoryOf(jobs[3])).toBe(NO_CATEGORY)
  })

  it("orders the groups: the framework's, then the installation's own, then the jobs without one", () => {
    const groups = byCategory(jobs)
    expect(groups.map((g) => g.category)).toEqual(['daily', 'report', 'maintenance', 'Closing', NO_CATEGORY])
    expect(groups[1].jobs.map((j) => j.id)).toEqual(['e-report', 'a-report'])        // by name inside a group
  })

  it("offers the framework's categories and those already in use", () => {
    expect(categoryChoices(jobs).slice(-2)).toEqual(['demo', 'Closing'])
  })
})
