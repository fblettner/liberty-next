import { describe, expect, it } from 'vitest'
import { sortOptions } from './options'

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
