import { describe, expect, it } from 'vitest'
import { barShares, fillTemplate, formatValue, toneOf } from './widgets'
import type { QueryResult } from '../types/connectors'

const result = { columns: [{ name: 'invoices' }, { name: 'amount' }, { name: 'rate' }], rows: [] } as unknown as QueryResult

describe('widgets', () => {
  it('formats a figure the way the card shows it', () => {
    expect(formatValue(1234.5, 'number', null, 'en')).toBe('1,234.5')
    expect(formatValue(1234.5, 'integer', null, 'en')).toBe('1,235')
    expect(formatValue(1234, 'currency', null, 'en')).toBe('€1,234')
    expect(formatValue(0.256, 'percent', null, 'en')).toBe('25.6\u00a0%')
    expect(formatValue(12, 'number', 'users', 'en')).toBe('12\u00a0users')
    expect(formatValue('closed', 'number', null, 'en')).toBe('closed')
    expect(formatValue(null, 'number', null, 'en')).toBe('')
  })
  it('fills a template from the row, resolving the column case', () => {
    expect(fillTemplate('{INVOICES:integer} invoices · {AMOUNT:currency} · {NOPE}', { invoices: 216, amount: 1639563, rate: 0.5 }, result, 'en')).toBe('216 invoices · €1,639,563 · {NOPE}')
  })
  it('maps tones and bar shares', () => {
    expect(toneOf('ERROR')).toBe('error')
    expect(toneOf('x')).toBe('muted')
    expect(barShares([50, 30, 20], 'total')).toEqual([50, 30, 20])
    expect(barShares([50, 25, null], 'max')).toEqual([100, 50, 0])
    expect(barShares([], 'total')).toEqual([])
  })
})
