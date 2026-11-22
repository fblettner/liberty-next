import { describe, expect, it } from 'vitest'
import { deliver, missingAnalyses, shippedId, withdraw } from './explorerShipped'

const saved = (id: string, name: string, extra: Record<string, unknown> = {}) => ({ id, name, definition: { rows: ['PERIOD'] }, ...extra })

describe('shippedId', () => {
  it('keeps the saved id when the file can carry it', () => {
    expect(shippedId(saved('sales_by_month', 'Sales by month'), [])).toBe('sales_by_month')
  })
  it('makes one from the name otherwise, free of the ones taken', () => {
    expect(shippedId(saved('a b', 'Ventes par période'), [])).toBe('ventes_par_periode')
    expect(shippedId(saved('a b', 'Ventes par période'), ['ventes_par_periode'])).toBe('ventes_par_periode_2')
    expect(shippedId(saved('é', '???'), [])).toBe('item')
  })
})

describe('deliver', () => {
  it('adds a saved object at version 1', () => {
    expect(deliver(undefined, 'by_month', saved('by_month', 'By month', { audience: 'sales', description: 'Per period' })))
      .toEqual({ by_month: { label: 'By month', version: 1, definition: { rows: ['PERIOD'] }, description: 'Per period', audience: 'sales' } })
  })
  it('replaces one already delivered, a version higher, and leaves the others', () => {
    const before = { by_month: { label: 'Old', version: 3, definition: {} }, other: { label: 'Other', version: 1 } }
    const after = deliver(before, 'by_month', saved('by_month', 'By month'))
    expect(after.by_month).toEqual({ label: 'By month', version: 4, definition: { rows: ['PERIOD'] } })
    expect(after.other).toBe(before.other)
  })
})

describe('withdraw', () => {
  it('removes one, and the whole list with the last', () => {
    expect(withdraw({ a: {}, b: {} }, 'a')).toEqual({ b: {} })
    expect(withdraw({ a: {} }, 'a')).toBeUndefined()
    expect(withdraw(undefined, 'a')).toBeUndefined()
  })
})

describe('missingAnalyses', () => {
  it('names the analyses a delivered dashboard shows that are not delivered', () => {
    const dash = { definition: { tiles: [{ analysis: 'a' }, { analysis: 'b' }, { analysis: 'a' }, {}] } }
    expect(missingAnalyses(dash, { a: {} })).toEqual(['b'])
    expect(missingAnalyses(dash, { a: {}, b: {} })).toEqual([])
    expect(missingAnalyses({}, undefined)).toEqual([])
  })
})
