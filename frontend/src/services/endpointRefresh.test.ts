import { describe, expect, it } from 'vitest'
import { mergeGenerated } from './endpointRefresh'

const current = {
  name: 'my_report',                      // renamed by the operator
  label: 'Month end',
  method: 'POST',
  path: '/report/execute',
  body: '{\n  "reportName": "R1",\n  "poValues": [{"id": 1, "value": "{{cMode}}"}]\n}',
  writable: true,
  response_field: 'jobNumber',
  params: [{ name: 'cMode', label: 'My label', default: '2', help: 'old help' }],
  source: { generator: 'report', candidate: 'R1|V1', options: {} },
}

describe('mergeGenerated', () => {
  it('says nothing changes when the server describes the same endpoint', () => {
    const fresh = { ...current, name: 'report_R1_V1', label: 'Submit R1', body: '{"reportName":"R1","poValues":[{"id":1,"value":"{{cMode}}"}]}', params: [{ name: 'cMode', label: '1. Mode', default: '1' }] }
    const r = mergeGenerated(current, fresh)
    expect(r.changes).toEqual([])
    // the operator's name, label, response setting and what they wrote on the parameter are kept
    expect(r.next.name).toBe('my_report')
    expect(r.next.label).toBe('Month end')
    expect(r.next.response_field).toBe('jobNumber')
    expect(r.next.params).toEqual([{ name: 'cMode', label: 'My label', default: '2', help: 'old help' }])
  })

  it('takes what the server decides: parameters, body, address', () => {
    const fresh = {
      name: 'x', method: 'POST', path: '/v3/report/execute', writable: true,
      body: '{"reportName": "R1", "poValues": [{"id": 1, "value": "{{cMode}}"}, {"id": 2, "value": "{{cDate}}"}]}',
      params: [{ name: 'cMode', label: '1. Mode', choices: ['1', '2'] }, { name: 'cDate', label: '2. Date', help: 'A date' }],
    }
    const r = mergeGenerated(current, fresh)
    expect(r.changes).toEqual(['+ cDate', 'path: /report/execute → /v3/report/execute', 'body', 'allowed values: cMode'])
    expect(r.next.path).toBe('/v3/report/execute')
    expect(r.next.body).toBe(fresh.body)
    expect(r.next.params).toEqual([
      { name: 'cMode', label: 'My label', default: '2', help: 'old help', choices: ['1', '2'] },
      { name: 'cDate', label: '2. Date', help: 'A date' },
    ])
  })

  it('drops a parameter the object no longer has, and follows the changes-data flag', () => {
    const r = mergeGenerated(current, { name: 'x', method: 'POST', path: '/report/execute', body: '{"reportName": "R1"}', params: [] })
    expect(r.changes).toEqual(['− cMode', 'body', 'changes data: no'])
    expect(r.next.params).toBeUndefined()
    expect(r.next.writable).toBeUndefined()
  })

  it('compares a body that is not JSON (a raw placeholder) without its white space', () => {
    const a = { name: 'o', method: 'POST', path: '/o', body: '{\n  "rows": {{ROWS}}\n}', params: [{ name: 'ROWS' }] }
    expect(mergeGenerated(a, { ...a, body: '{"rows": {{ROWS}}}' }).changes).toEqual([])
  })
})
