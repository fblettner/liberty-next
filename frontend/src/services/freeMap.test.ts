import { describe, expect, it } from 'vitest'
import { freeMapText, mergeFreeMap, targetParamNames } from './freeMap'

describe('freeMapText', () => {
  it('shows every value as text', () => {
    expect(freeMapText({ STATUS: 'open', YEAR: 2026, ACTIVE: true, NONE: null, LIST: ['a', 'b'] }))
      .toEqual({ STATUS: 'open', YEAR: '2026', ACTIVE: 'true', NONE: '', LIST: '["a","b"]' })
  })
})

describe('mergeFreeMap', () => {
  const original = { STATUS: 'open', YEAR: 2026, ACTIVE: true }
  it('keeps the kind of what was not touched', () => {
    expect(mergeFreeMap(original, { STATUS: 'open', YEAR: '2026', ACTIVE: 'true' })).toEqual(original)
  })
  it('takes a typed value as text', () => {
    expect(mergeFreeMap(original, { STATUS: 'closed', YEAR: '2027', ACTIVE: 'true' }))
      .toEqual({ STATUS: 'closed', YEAR: '2027', ACTIVE: true })
  })
  it('adds and removes rows', () => {
    expect(mergeFreeMap(original, { STATUS: 'open', COMPANY: '00001' })).toEqual({ STATUS: 'open', COMPANY: '00001' })
    expect(mergeFreeMap({}, { '': '' })).toEqual({ '': '' })
  })
  it('keeps the kind of a value whose row was renamed', () => {
    expect(mergeFreeMap(original, { STATUS: 'open', FISCAL_YEAR: '2026', ACTIVE: 'true' }))
      .toEqual({ STATUS: 'open', FISCAL_YEAR: 2026, ACTIVE: true })
  })
})

describe('targetParamNames', () => {
  const screen = { columns: [{ name: 'STATUS', filter: true }, { name: 'LABEL' }, { name: 'YEAR', filter: true }] }
  const connector = {
    tables: [{ name: 'orders', get: { params: [{ name: 'COMPANY' }] } }],
    queries: [{ name: 'late_orders', params: [{ name: 'DAYS' }, { name: 'COMPANY' }] }],
  }
  it('offers the filters of the screen an entry opens', () => {
    expect(targetParamNames({ type: 'screen', target: 'orders' }, screen, connector)).toEqual(['STATUS', 'YEAR'])
  })
  it('offers the declared parameters of the query an entry opens', () => {
    expect(targetParamNames({ type: 'query', target: 'orders_get' }, null, connector)).toEqual(['COMPANY'])
    expect(targetParamNames({ type: 'query', target: 'late_orders' }, null, connector)).toEqual(['DAYS', 'COMPANY'])
  })
  it('offers nothing for a folder, a dashboard or an entry that opens nothing yet', () => {
    expect(targetParamNames({ type: null }, screen, connector)).toEqual([])
    expect(targetParamNames({ type: 'dashboard', target: 'demo.home' }, screen, connector)).toEqual([])
    expect(targetParamNames({ type: 'screen', target: '' }, screen, connector)).toEqual([])
    expect(targetParamNames(null, screen, connector)).toEqual([])
  })
})
