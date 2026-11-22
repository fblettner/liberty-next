import { describe, expect, it } from 'vitest'
import { applyPick, chainContextCandidates, withCapturedResults } from './actionPath'

type Row = Record<string, unknown>

describe('chainContextCandidates', () => {
  const steps: Row[] = [
    { id: 'find_user', type: 'run_query', label: 'Find the user', query: 'users_get' },
    { id: 'tell', type: 'notify', message: 'x' },
    { id: 'write', type: 'run_query', query: 'log_post' },
  ]
  it('offers what the earlier steps returned, marked as kept or not', () => {
    const offered = chainContextCandidates(steps, [{ field: null, i: 2 }]).map((c) => c.value)
    expect(offered).toEqual(['find_user.first_row', 'find_user.rows'])
  })
  it('offers nothing of the step itself or of the following ones', () => {
    expect(chainContextCandidates(steps, [{ field: null, i: 0 }])).toEqual([])
  })
  it('offers each column of the first row when the columns are known', () => {
    const offered = chainContextCandidates(steps, [{ field: null, i: 2 }], (s) => (s.id === 'find_user' ? ['EMAIL', 'NAME'] : []))
    expect(offered.map((c) => c.value)).toEqual(['find_user.first_row.EMAIL', 'find_user.first_row.NAME', 'find_user.first_row', 'find_user.rows'])
    expect(offered[0].label).toBe('EMAIL — first row of "Find the user"')
  })
  it('offers nothing for no path', () => {
    expect(chainContextCandidates(steps, [])).toEqual([])
  })
})

describe('applyPick', () => {
  it('replaces what was there by the place picked', () => {
    expect(applyPick('INPUT.from_user', 11, 11, 'find_user.rows').next).toBe('find_user.rows')
    expect(applyPick('', 0, 0, 'INPUT.to_user').next).toBe('INPUT.to_user')
    expect(applyPick('find_user.first_row', 19, 19, '#LOGIN_USER#').next).toBe('#LOGIN_USER#')
    expect(applyPick('USR_ID', 6, 6, 'USR_NAME').next).toBe('USR_NAME')
  })
  it('continues a path that waits for a column', () => {
    expect(applyPick('find_user.first_row', 19, 19, 'EMAIL')).toEqual({ next: 'find_user.first_row.EMAIL', cursor: 25 })
    expect(applyPick('loop', 4, 4, 'ID').next).toBe('loop.ID')
    expect(applyPick('find_user.first_row.', 20, 20, 'EMAIL').next).toBe('find_user.first_row.EMAIL')
  })
})

describe('withCapturedResults', () => {
  it('marks a step whose result another step reads', () => {
    const steps: Row[] = [
      { id: 'find_user', type: 'run_query', query: 'users_get' },
      { id: 'write', type: 'run_query', query: 'log_post', param_binds: [{ param: 'EMAIL', source: 'find_user.first_row.EMAIL' }] },
    ]
    const out = withCapturedResults(steps)
    expect(out[0].bind_result).toBe(true)
    expect(out[1]).toBe(steps[1])                                  // what did not change is the same object
    expect(steps[0].bind_result).toBeUndefined()                   // what was given is not changed
  })
  it('gives back the same list when nothing is read, or all is marked already', () => {
    const none: Row[] = [{ id: 'a', type: 'run_query' }, { id: 'b', type: 'run_query', param_binds: [{ param: 'X', source: 'INPUT.X' }] }]
    expect(withCapturedResults(none)).toBe(none)
    const marked: Row[] = [{ id: 'a', type: 'run_query', bind_result: true }, { id: 'b', type: 'loop', source: 'a.rows', steps: [] }]
    expect(withCapturedResults(marked)).toBe(marked)
  })
  it('reads a condition, a loop, a value set and an answer, at any depth', () => {
    const steps: Row[] = [
      { id: 'a', type: 'run_query' }, { id: 'b', type: 'call_api' }, { id: 'c', type: 'call_plugin' }, { id: 'd', type: 'run_query' },
      { id: 'if1', type: 'if', condition: { source: 'a.rows', operator: 'has_rows' }, then_steps: [
        { id: 'each', type: 'loop', source: 'b.rows', steps: [{ id: 'set', type: 'set_field', target: 'X', source: 'c.first_row.VALUE' }] },
      ], else_steps: [{ id: 'ret', type: 'return', bindings: { NAME: 'd.first_row.NAME' } }] },
    ]
    expect(withCapturedResults(steps).slice(0, 4).map((s) => s.bind_result)).toEqual([true, true, true, true])
  })
  it('marks a step inside a group, and never a kind that returns nothing', () => {
    const steps: Row[] = [
      { id: 'grp', type: 'chain', steps: [{ id: 'inner', type: 'run_query' }, { id: 'msg', type: 'notify' }] },
      { id: 'use', type: 'run_query', param_binds: [{ param: 'A', source: 'inner.first_row.A' }, { param: 'B', source: 'msg.first_row.B' }] },
    ]
    const out = withCapturedResults(steps)
    expect((out[0].steps as Row[])[0].bind_result).toBe(true)
    expect((out[0].steps as Row[])[1].bind_result).toBeUndefined()
  })
})
