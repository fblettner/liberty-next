import { describe, expect, it } from 'vitest'
import { mergeGenerated, mergeModified, rowKeys } from './endpointRefresh'

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

describe('Modify…: the generator asked again with other choices', () => {
  const src = (options: Record<string, unknown>) => ({ generator: 'application', candidate: 'P01012', options })
  const list = [
    { name: 'other', method: 'GET', path: '/x' },
    { name: 'my_list', label: 'My list', method: 'POST', path: '/formservice', body: '{"a":1}', params: [{ name: 'AN8', label: 'Number (mine)' }], source: src({ columns: ['AN8'] }) },
    { name: 'form_P01012_add', method: 'POST', path: '/formservice', body: '{"b":1}', source: src({ columns: ['AN8'] }) },
    { name: 'form_P4210_add', method: 'POST', path: '/formservice', source: { generator: 'application', candidate: 'P4210', options: {} } },
  ]
  it('merges a single build into the endpoint being modified, whatever it was renamed to', () => {
    const fresh = { name: 'form_P01012_list', method: 'POST', path: '/formservice', body: '{"a":2}', params: [{ name: 'AN8' }, { name: 'ALPH' }], source: src({ columns: ['AN8', 'ALPH'] }) }
    const r = mergeModified(list, 1, [fresh])
    expect(r.endpoints[1].name).toBe('my_list')                                   // the operator's name
    expect(r.endpoints[1].label).toBe('My list')
    expect(r.endpoints[1].params).toEqual([{ name: 'AN8', label: 'Number (mine)' }, { name: 'ALPH' }])
    expect((r.endpoints[1].source as { options: unknown }).options).toEqual({ columns: ['AN8', 'ALPH'] })   // the next Modify… starts from here
    expect(r.lines).toEqual([{ name: 'my_list', changes: ['+ ALPH', 'body'] }])
    expect(r.endpoints[0]).toBe(list[0])
    expect(list[1].body).toBe('{"a":1}')                                          // the list given is not touched
  })
  it("applies a set to the endpoints of the same origin, adds what is new, leaves another application's alone", () => {
    const built = [
      { name: 'my_list', method: 'POST', path: '/formservice', body: '{"a":1}', params: [{ name: 'AN8' }], source: src({ row: true }) },
      { name: 'form_P01012_add', method: 'POST', path: '/formservice', body: '{"b":2}', source: src({ row: true }) },
      { name: 'form_P01012_delete', method: 'POST', path: '/formservice', writable: true, source: src({ row: true }) },
      { name: 'form_P4210_add', method: 'POST', path: '/other', source: src({ row: true }) },
    ]
    const r = mergeModified(list, 1, built)
    expect(r.lines).toEqual([
      { name: 'form_P01012_add', changes: ['body'] },
      { name: 'form_P01012_delete', changes: [], added: true },
      { name: 'form_P4210_add_2', changes: [], added: true },                      // the name is another application's: not merged into it
    ])
    expect(r.endpoints[3]).toBe(list[3])
    expect(r.endpoints.map((e) => e.name)).toEqual(['other', 'my_list', 'form_P01012_add', 'form_P4210_add', 'form_P01012_delete', 'form_P4210_add_2'])
    expect((r.endpoints[1].source as { options: unknown }).options).toEqual({ row: true })                 // unchanged content, new choices kept
  })
  it('does nothing on an endpoint that was not generated', () => {
    expect(mergeModified(list, 0, [{ name: 'x' }])).toEqual({ endpoints: list, lines: [] })
  })
})

describe('the keys a column can be read from', () => {
  it("reads the first row's keys where the rows are", () => {
    const answer = { fs_P01012_W01012B: { data: { gridData: { rowset: [{ z_AN8_19: 1, z_ALPH_20: 'x' }, { z_AN8_19: 2 }] } } } }
    expect(rowKeys(answer, 'fs_P01012_W01012B.data.gridData.rowset')).toEqual(['z_AN8_19', 'z_ALPH_20'])
    expect(rowKeys([{ id: 1, name: 'a' }], '')).toEqual(['id', 'name'])
    expect(rowKeys({ rows: [] }, 'rows')).toEqual([])
    expect(rowKeys({ rows: 'nope' }, 'rows')).toEqual([])
    expect(rowKeys({ a: 1 }, 'missing.path')).toEqual([])
  })
})

