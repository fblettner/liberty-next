import { describe, expect, it } from 'vitest'
import { lookupTable, orderOptions, sortOptions } from './options'

describe('sortOptions', () => {
  it('sorts by label, numerically and case-insensitively, the empty value first, stable', () => {
    const out = sortOptions([
      { value: 'b', label: 'item 10' }, { value: 'a', label: 'Item 2' }, { value: '', label: '(none)' },
      { value: 'c', label: 'f0111' }, { value: 'd', label: 'F0101' }, { value: 'e', label: 'item 2' },
    ])
    expect(out.map((o) => o.value)).toEqual(['', 'd', 'c', 'a', 'e', 'b'])
  })
  it('leaves the input untouched', () => {
    const input = [{ value: 'z', label: 'z' }, { value: 'a', label: 'a' }]
    sortOptions(input)
    expect(input.map((o) => o.value)).toEqual(['z', 'a'])
  })
})

describe('orderOptions', () => {
  const o = (value: string, group?: string) => ({ value, label: value, ...(group ? { group } : {}) })
  it('keeps a list without groups as it is (sorted on demand)', () => {
    expect(orderOptions([o('b'), o('a')]).map((x) => x.value)).toEqual(['b', 'a'])
    expect(orderOptions([o('b'), o('a')], true).map((x) => x.value)).toEqual(['a', 'b'])
  })
  it('puts the ungrouped first, then each group in the order met, or alphabetical with sort', () => {
    const list = [o('z', 'nomasx1'), o('m'), o('b', 'nomajde'), o('a', 'nomasx1')]
    expect(orderOptions(list).map((x) => x.value)).toEqual(['m', 'z', 'a', 'b'])
    expect(orderOptions(list, true).map((x) => `${x.group ?? ''}:${x.value}`)).toEqual([':m', 'nomajde:b', 'nomasx1:a', 'nomasx1:z'])
  })
})

describe('sortOptions — a line that shows a code', () => {
  it('orders on the code, not on the description', () => {
    const o = sortOptions([
      { value: 'truncate_items', mono: 'truncate_items', label: 'Before a full reload of the item file.' },
      { value: 'customers_from_sales', mono: 'customers_from_sales', label: 'After a load: add the customers' },
      { value: 'dim_company_get', mono: 'dim_company_get', label: 'Companies with their business unit' },
    ])
    expect(o.map((x) => x.value)).toEqual(['customers_from_sales', 'dim_company_get', 'truncate_items'])
  })
  it('orders a line without code on its label, among the others', () => {
    const o = sortOptions([
      { value: 'b', mono: 'F0101', label: 'Address book' },
      { value: 'a', label: 'F0006 — business units' },
      { value: 'c', mono: ' ', label: 'F0005' },
    ])
    expect(o.map((x) => x.value)).toEqual(['c', 'a', 'b'])
  })
})

describe('narrowing a list', () => {
  const opts = [
    { value: 'P0411', label: 'Voucher Entry', mono: 'P0411', cells: ['APPL', '04'] },
    { value: 'R04423', label: 'Aging', mono: 'R04423', cells: ['UBE', '04'] },
    { value: 'F0411', label: 'Accounts Payable Ledger', mono: 'F0411', cells: ['TBLE', '04'] },
  ]
  it('lists the columns filtered by chips with their values', async () => {
    const { facetColumns } = await import('./options')
    expect(facetColumns([{ label: 'Type', filterable: true }, { label: 'System' }], opts)).toEqual([{ index: 0, label: 'Type', values: ['APPL', 'TBLE', 'UBE'] }])
    expect(facetColumns(undefined, opts)).toEqual([])
  })
  it('keeps what the text and the chips leave', async () => {
    const { filterOptions } = await import('./options')
    expect(filterOptions(opts, 'ledger').map((o) => o.value)).toEqual(['F0411'])
    expect(filterOptions(opts, '04', { 0: 'UBE' }).map((o) => o.value)).toEqual(['R04423'])
    expect(filterOptions(opts, '')).toBe(opts)
  })
  it('offers a long list, or one with columns, as a table', async () => {
    const { browsable, BROWSE_FROM } = await import('./options')
    expect(browsable(opts)).toBe(true)
    expect(browsable([{ value: 'a', label: 'A' }])).toBe(false)
    expect(browsable(Array.from({ length: BROWSE_FROM }, (_x, i) => ({ value: String(i), label: String(i) })))).toBe(true)
  })
})

describe('lookupTable', () => {
  const words = { code: 'Code', label: 'Description' }
  it('draws the code, the label and each cell under its heading, a filterable one with its values to pick', () => {
    const t = lookupTable([
      { value: 'P1', label: 'First', mono: 'P1', cells: ['Complete', 'Bug'] },
      { value: 'P2', label: 'Second', mono: 'P2', cells: ['In Production', 'Bug'] },
      { value: 'P3', label: 'Third', mono: 'P3', cells: ['Complete'] },
    ], [{ label: 'Status', filterable: true }, { label: 'Type' }], words)
    expect(t.columns).toEqual([
      { key: 'code', label: 'Code', mono: true }, { key: 'label', label: 'Description' },
      { key: 'c0', label: 'Status', choices: ['Complete', 'In Production'] }, { key: 'c1', label: 'Type' },
    ])
    expect(t.rows[2]).toEqual({ value: 'P3', code: 'P3', label: 'Third', c0: 'Complete', c1: '' })
  })
  it('has no code column when the lines show none', () => {
    expect(lookupTable([{ value: 'a', label: 'a' }], undefined, words).columns).toEqual([{ key: 'label', label: 'Description' }])
  })
})
