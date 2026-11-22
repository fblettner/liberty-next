import { describe, expect, it } from 'vitest'
import { takenNames, withList, withListQuery, type NewList } from './listFromTable'

const list: NewList = {
  query: { name: 'customers_lookup', sql: 'SELECT CODE, NAME FROM CUSTOMERS', label: 'Customers' },
  dictEntry: { query: 'customers_lookup', value: 'CODE', label: 'NAME' },
  dictId: 'customers',
}

describe('withListQuery', () => {
  it('adds the query to the list queries of the connector, and to them only', () => {
    const before = { demo: { type: 'sql', queries: [{ name: 'q' }], lookups: [{ name: 'other' }] }, x: { type: 'sql' } }
    const after = withListQuery(before, 'demo', list)
    expect(after.demo.lookups).toEqual([{ name: 'other' }, list.query])
    expect(after.demo.queries).toBe(before.demo.queries)
    expect(after.x).toBe(before.x)
    expect(before.demo.lookups).toHaveLength(1)                       // the document given is not changed
  })
  it('starts the list queries when the connector had none', () => {
    expect(withListQuery({ demo: { type: 'sql' } }, 'demo', list).demo.lookups).toEqual([list.query])
  })
  it('leaves a query of that name as it is', () => {
    const before = { demo: { lookups: [{ name: 'customers_lookup', sql: 'SELECT 1' }] } }
    expect(withListQuery(before, 'demo', list)).toBe(before)
  })
})

describe('withList', () => {
  it('adds the list under the lists of the application', () => {
    const before = { entries: {}, connectors: { demo: { entries: { A: {} }, lookups: { other: {} } } } }
    const after = withList(before, 'demo', list) as { connectors: { demo: { entries: unknown; lookups: Record<string, unknown> } } }
    expect(after.connectors.demo.lookups).toEqual({ other: {}, customers: list.dictEntry })
    expect(after.connectors.demo.entries).toEqual({ A: {} })
  })
  it('opens the application in the dictionary when it had nothing there', () => {
    expect(withList({}, 'demo', list)).toEqual({ connectors: { demo: { lookups: { customers: list.dictEntry } } } })
  })
})

describe('takenNames', () => {
  it('knows the query names of the connector and the lists of the application and the shared ones', () => {
    const connectors = { demo: { queries: [{ name: 'q1' }], lookups: [{ name: 'l1' }], sequences: [{ name: 's1' }], tables: [{ name: 'orders', get: {}, put: {} }] } }
    const dictionary = { lookups: { shared_list: {} }, connectors: { demo: { lookups: { own_list: {} } }, other: { lookups: { not_mine: {} } } } }
    const taken = takenNames(connectors, dictionary, 'demo', 'demo')
    expect([...taken.queries].sort()).toEqual(['l1', 'orders_get', 'orders_put', 'q1', 's1'])
    expect([...taken.lists].sort()).toEqual(['own_list', 'shared_list'])
  })
})
