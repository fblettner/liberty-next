import { describe, expect, it } from 'vitest'
import { freshStepId, insertStep, stepSummary } from './ActionFlow'

const root = [
  { id: 'read', type: 'run_query', connector: 'main', query: 'codes_get' },
  { id: 'none', type: 'if', condition: { source: 'read.rows', operator: 'no_rows' }, then_steps: [{ id: 'say', type: 'notify', message: 'nothing' }], else_steps: [] },
  { id: 'out', type: 'return', bindings: { code: 'read.first_row.ky', label: 'read.first_row.dl01' } },
]

describe('the flow of a service', () => {
  it('says what a step does in one line', () => {
    expect(root.map(stepSummary)).toEqual(['main.codes_get', 'read.rows no_rows', 'code, label'])
    expect(stepSummary({ type: 'call_api', connector: 'ais', endpoint: 'form' })).toBe('ais.form')
    expect(stepSummary({ type: 'refresh' })).toBe('')
  })
  it('inserts a step where its "+" is: in the service, in a branch', () => {
    const first = insertStep(root, [], null, 0, { id: 'new', type: 'notify' })
    expect(first.map((a) => a.id)).toEqual(['new', 'read', 'none', 'out'])
    const inFalse = insertStep(root, [{ field: null, i: 1 }], 'else_steps', 0, { id: 'other', type: 'notify', message: 'x' })
    expect((inFalse[1].else_steps as { id: string }[]).map((a) => a.id)).toEqual(['other'])
    const inTrue = insertStep(root, [{ field: null, i: 1 }], 'then_steps', 1, { id: 'then2', type: 'notify', message: 'y' })
    expect((inTrue[1].then_steps as { id: string }[]).map((a) => a.id)).toEqual(['say', 'then2'])
    expect(root[1].then_steps).toHaveLength(1)                      // nothing changed in place
  })
  it('names a new step with an id nobody has, nested ones counted', () => {
    expect(freshStepId(root, 'run_query')).toBe('query_1')
    expect(freshStepId([{ id: 'api_1', type: 'chain', steps: [{ id: 'api_2' }] }], 'call_api')).toBe('api_3')
    expect(freshStepId(root, 'if')).toBe('if_1')
  })
})
