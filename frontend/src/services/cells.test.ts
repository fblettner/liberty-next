// Cell rendering helpers: the dictionary display rules (BOOLEAN / ENUM / LOOKUP) applied to a
// raw value, plus the null / object text fallbacks the grid relies on.
import { describe, expect, it } from 'vitest'
import type { Column } from '../types/connectors'
import { cellText, enumMap, ruleCell } from './cells'

const col = (rule?: Column['rule']): Column => ({ name: 'X', type: 'text', rule })

describe('cellText', () => {
  it('null-ish → empty + isNull; objects → JSON; everything else → String()', () => {
    expect(cellText(null)).toEqual({ text: '', isNull: true })
    expect(cellText(undefined)).toEqual({ text: '', isNull: true })
    expect(cellText({ a: 1 })).toEqual({ text: '{"a":1}', isNull: false })
    expect(cellText(42)).toEqual({ text: '42', isNull: false })
  })
})

describe('ruleCell', () => {
  it('no rule → plain; null → null kind', () => {
    expect(ruleCell('v', col())).toEqual({ text: 'v', isNull: false, kind: 'plain' })
    expect(ruleCell(null, col({ kind: 'boolean', true_value: 'Y' })).kind).toBe('null')
  })

  it("BOOLEAN compares the raw value with the rule's true_value", () => {
    const c = col({ kind: 'boolean', true_value: 'Y' })
    expect(ruleCell('Y', c).kind).toBe('boolean-true')
    expect(ruleCell('N', c).kind).toBe('boolean-false')
    expect(ruleCell('y', c).kind).toBe('boolean-false') // exact match, like the backend
  })

  it('ENUM resolves the label from the precomputed map, else from the rule, else stays plain', () => {
    const rule = { kind: 'enum' as const, values: [{ value: 'A', label: 'Active' }, { value: 'I', label: 'Inactive' }] }
    const c = col(rule)
    expect(ruleCell('A', c, enumMap(rule))).toEqual({ text: 'Active', isNull: false, kind: 'enum' })
    expect(ruleCell('I', c)).toEqual({ text: 'Inactive', isNull: false, kind: 'enum' })
    expect(ruleCell('Z', c, enumMap(rule))).toEqual({ text: 'Z', isNull: false, kind: 'plain' })
  })

  it('LOOKUP is pending until the batch map arrives, then resolves or falls back to the code', () => {
    const c = col({ kind: 'lookup', connector: 'foo', query: 'roles_get', value: 'ROL_ID', label: 'ROL_NAME' })
    expect(ruleCell('7', c).kind).toBe('lookup-pending')
    expect(ruleCell('7', c, undefined, new Map([['7', 'Admins']]))).toEqual({ text: 'Admins', isNull: false, kind: 'lookup' })
    expect(ruleCell('8', c, undefined, new Map([['7', 'Admins']]))).toEqual({ text: '8', isNull: false, kind: 'plain' })
  })
})
