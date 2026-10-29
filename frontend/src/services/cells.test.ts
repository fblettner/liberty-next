// Cell rendering helpers: the dictionary display rules (BOOLEAN / ENUM / LOOKUP) applied to a
// raw value, plus the null / object text fallbacks the grid relies on.
import { describe, expect, it } from 'vitest'
import type { Column } from '../types/connectors'
import { cellText, cssColor, enumMap, formatLink, isDateTimeColumn, localDateTime, mailtoOf, ruleCell, safeUrl } from './cells'

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
    // a JDE UDC key is right-justified ("       C") while the column holds "C": trim-tolerant, the exact key first
    const udc = new Map([['       C', 'Customers'], ['       V', 'Suppliers'], ['V', 'Exact']])
    expect(ruleCell('C', c, undefined, udc).text).toBe('Customers')
    expect(ruleCell('V', c, undefined, udc).text).toBe('Exact')
    expect(ruleCell('Z', c, undefined, udc)).toEqual({ text: 'Z', isNull: false, kind: 'plain' })
    expect(ruleCell('8', c, undefined, new Map([['7', 'Admins']]))).toEqual({ text: '8', isNull: false, kind: 'plain' })
  })
})

describe('zoned date-times in local time', () => {
  // the expected text built from a Date, so the tests hold in any time zone
  const local = (iso: string) => {
    const d = new Date(iso), p = (n: number) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  it('reads the zone forms PostgreSQL and ISO use', () => {
    expect(localDateTime('2026-10-28 23:54:08.859444+00:00')).toBe(local('2026-10-28T23:54:08Z'))
    expect(localDateTime('2026-10-28T23:54:08Z')).toBe(local('2026-10-28T23:54:08Z'))
    expect(localDateTime('2026-10-28 23:54:08+02')).toBe(local('2026-10-28T21:54:08Z'))
    expect(localDateTime('2026-10-28 23:54+0530')).toBe(local('2026-10-28T18:24:00Z'))
  })
  it('leaves a value without a zone alone', () => {
    expect(localDateTime('2026-06-13 13:06:24')).toBeNull()
    expect(localDateTime('2026-06-13')).toBeNull()
    expect(localDateTime('not a date')).toBeNull()
  })
  it('applies to date-time columns only', () => {
    const dt = { name: 'checked_at', type: null, format: 'datetime' } as Column
    expect(isDateTimeColumn(dt)).toBe(true)
    expect(isDateTimeColumn({ type: 'timestamp with time zone' })).toBe(true)
    expect(ruleCell('2026-10-28 23:54:08.859444+00:00', dt).text).toBe(local('2026-10-28T23:54:08Z'))
    expect(ruleCell('2026-10-28 23:54:08+00:00', { name: 'note', type: null, format: 'text' } as Column).text).toBe('2026-10-28 23:54:08+00:00')
  })
})

describe('url / email / color formats', () => {
  it('a link only for an http(s) address', () => {
    expect(safeUrl('https://docs.nomana-it.fr/liberty')).toBe('https://docs.nomana-it.fr/liberty')
    expect(safeUrl('www.oracle.com')).toBe('https://www.oracle.com/')
    expect(safeUrl('javascript:alert(1)')).toBeNull()
    expect(safeUrl('ftp://host/file')).toBeNull()
    expect(safeUrl('not a url')).toBeNull()
    expect(safeUrl('')).toBeNull()
  })
  it('a mailto only for an address', () => {
    expect(mailtoOf(' jane.doe@nomana-it.fr ')).toBe('mailto:jane.doe@nomana-it.fr')
    expect(mailtoOf('jane.doe')).toBeNull()
    expect(mailtoOf('a@b')).toBeNull()
    expect(mailtoOf('x@y.fr?bcc=z@w.fr')).toBeNull()
  })
  it('a swatch only for a real colour', () => {
    expect(cssColor('#1a2B3c')).toBe('#1a2B3c')
    expect(cssColor('1a2b3c')).toBe('#1a2b3c')
    expect(cssColor('#abc')).toBe('#abc')
    expect(cssColor('rgb(10, 20, 30)')).toBe('rgb(10, 20, 30)')
    expect(cssColor('red; background: url(x)')).toBeNull()
    expect(cssColor('#12')).toBeNull()
  })
  it('by the column format; any other format is plain', () => {
    expect(formatLink({ format: 'url' }, 'www.x.fr')).toEqual({ kind: 'url', href: 'https://www.x.fr/' })
    expect(formatLink({ format: 'email' }, 'a@b.fr')).toEqual({ kind: 'email', href: 'mailto:a@b.fr' })
    expect(formatLink({ format: 'color' }, 'ff0000')).toEqual({ kind: 'color', href: '#ff0000' })
    expect(formatLink({ format: 'text' }, 'https://x.fr')).toBeNull()
    expect(formatLink({ format: 'url' }, 'nope')).toBeNull()
  })
})
