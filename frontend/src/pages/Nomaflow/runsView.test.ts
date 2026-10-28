import { describe, expect, it } from 'vitest'
import { formatDuration, groupByDay, paramLabels, runSeconds, type RunRow } from './runsView'

const run = (id: string, started: string | null, extra: Partial<RunRow> = {}): RunRow => ({
  id, job_id: 'j', state: 'SUCCEEDED', trigger_kind: 'manual', triggered_by: null, scheduled_at: null,
  started_at: started, finished_at: null, rows_affected: null, error_message: null, ...extra,
})

describe('runsView', () => {
  it('writes durations', () => {
    expect(formatDuration(42)).toBe('42 s')
    expect(formatDuration(120)).toBe('2 min')
    expect(formatDuration(125)).toBe('2 min 5 s')
    expect(formatDuration(3725)).toBe('1 h 2 min')
    expect(formatDuration(null)).toBe('—')
  })
  it('measures a finished run and a live one', () => {
    expect(runSeconds({ started_at: '2026-10-28T10:00:00Z', finished_at: '2026-10-28T10:01:30Z' })).toBe(90)
    expect(runSeconds({ started_at: '2026-10-28T10:00:00Z', finished_at: null }, Date.parse('2026-10-28T10:00:10Z'))).toBe(10)
    expect(runSeconds({ started_at: null, finished_at: null })).toBeNull()
  })
  it('groups by local day, newest first, with the offset from today', () => {
    const now = new Date(2026, 9, 28, 15, 0)
    const g = groupByDay([
      run('a', new Date(2026, 9, 28, 9).toISOString()),
      run('b', new Date(2026, 9, 28, 1).toISOString()),
      run('c', new Date(2026, 9, 27, 23).toISOString()),
      run('d', null, { scheduled_at: new Date(2026, 9, 20, 6).toISOString(), state: 'MISSED' }),
    ], now)
    expect(g.map((x) => [x.key, x.offset, x.runs.map((r) => r.id).join('')])).toEqual([
      ['2026-10-28', 0, 'ab'], ['2026-10-27', 1, 'c'], ['2026-10-20', 8, 'd'],
    ])
  })
  it('labels the parameters', () => {
    expect(paramLabels({ apps_id: 3, lang: 'fr', list: [1, 2], skip: null, obj: { a: 1 } })).toEqual(['apps_id 3', 'lang fr', 'list 1, 2'])
    expect(paramLabels(undefined)).toEqual([])
  })
})
