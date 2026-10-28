import { describe, expect, it } from 'vitest'
import { orderOptions, sortOptions } from './options'

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
