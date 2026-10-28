import { describe, expect, it } from 'vitest'
import type { Row } from '@tanstack/react-table'
import { selectFilterFn } from './DataTableFilter'

const row = (v: unknown) => ({ getValue: () => v }) as unknown as Row<unknown>
const match = (v: unknown, filter: unknown) => selectFilterFn(row(v), 'c', filter, () => {})

describe('selectFilterFn', () => {
  it('matches one code, trim- and case-tolerant (CHAR-padded JDE values)', () => {
    expect(match('F   ', 'f')).toBe(true)
    expect(match('G', 'F')).toBe(false)
  })
  it('matches any of a multi filter’s codes', () => {
    expect(match('B ', ['A', 'b'])).toBe(true)
    expect(match('C', ['A', 'B'])).toBe(false)
    expect(match(null, ['', 'A'])).toBe(false)
  })
  it('an empty filter matches every row and is removed', () => {
    expect(match('X', [])).toBe(true)
    expect(match('X', '')).toBe(true)
    expect(selectFilterFn.autoRemove?.([])).toBe(true)
    expect(selectFilterFn.autoRemove?.(['A'])).toBe(false)
  })
})
