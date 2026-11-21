import { describe, expect, it } from 'vitest'
import { connectorFigures, tableSlots } from './connectorFigures'

describe('what a connector says', () => {
  it('counts what it holds', () => {
    expect(connectorFigures({ type: 'sql', pool: 'main', tables: [{}, {}], queries: [{}], sequences: [], lookups: [{}, {}, {}] })).toEqual({
      api: false, pool: 'main', tables: 2, queries: 1, sequences: 0, lookups: 3, endpoints: 0 })
    expect(connectorFigures({ type: 'api', endpoints: [{}, {}] })).toMatchObject({ api: true, endpoints: 2, tables: 0 })
    expect(connectorFigures(undefined)).toMatchObject({ api: false, pool: '', tables: 0 })
  })
  it("says which of a table's statements are set, writable, or left blank", () => {
    const slots = tableSlots({ name: 't', get: { sql: 'SELECT 1' }, put: { sql: 'UPDATE t SET a = 1', writable: true }, post: { sql: '  ' }, delete: { sql: { oracle: 'DELETE FROM t' } } })
    expect(slots).toEqual([
      { crud: 'get', set: true, writable: false, blank: false },
      { crud: 'put', set: true, writable: true, blank: false },
      { crud: 'post', set: true, writable: false, blank: true },
      { crud: 'delete', set: true, writable: false, blank: false },
    ])
    expect(tableSlots({ name: 't' }).every((s) => !s.set && !s.blank)).toBe(true)
  })
})
