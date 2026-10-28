import { describe, expect, it } from 'vitest'
import { durationOf, filesInLog, isActive } from './jobRunner'

describe('jobRunner helpers', () => {
  it('finds the run files a log names', () => {
    const logs = 'rejects written to abc123-load-rejects.csv and abc123-load-rejects.csv; other-run-x.csv ignored'
    expect(filesInLog('abc123', logs)).toEqual(['abc123-load-rejects.csv'])
    expect(filesInLog('abc123', '')).toEqual([])
  })
  it('formats a duration and knows the live states', () => {
    expect(durationOf({ started_at: '2026-10-28T10:00:00Z', finished_at: '2026-10-28T10:01:05Z' })).toBe('1m 5s')
    expect(durationOf({ started_at: null, finished_at: null })).toBe('—')
    expect(isActive('RUNNING') && isActive('QUEUED') && !isActive('FAILED') && !isActive(undefined)).toBe(true)
  })
})
