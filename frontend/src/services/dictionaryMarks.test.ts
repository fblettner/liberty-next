import { describe, expect, it } from 'vitest'
import { recordLine, recordMatches } from './dictionaryMarks'

describe("a dictionary record's line", () => {
  it('says an entry\'s label, format and rule', () => {
    expect(recordLine('entries', { label: 'Company', format: 'text', rules: 'lookup', rules_values: 'companies' })).toEqual({
      sub: 'Company', marks: [{ tone: 'neutral', text: 'text' }, { tone: 'purple', text: 'LOOKUP', title: 'LOOKUP companies' }] })
    expect(recordLine('entries', { rules: 'boolean' }).marks).toEqual([{ tone: 'purple', text: 'BOOLEAN' }])
    expect(recordLine('entries', { label: 'Amount' })).toEqual({ sub: 'Amount', marks: [] })
    expect(recordLine('entries', undefined)).toEqual({ sub: '', marks: [] })
  })
  it('counts an enum\'s values', () => {
    expect(recordLine('enums', { label: 'Yes / No', values: [{ value: 'Y' }, { value: 'N' }] })).toEqual({ sub: 'Yes / No', marks: [{ tone: 'green', text: '2' }] })
  })
  it('names the other connector a lookup or a sequence reads from, in one short chip', () => {
    expect(recordLine('lookups', { description: 'Companies', connector: 'jde', query: 'f0010_lookup', union: [{}] }, 'companies')).toEqual({
      sub: 'Companies', marks: [{ tone: 'blue', text: 'jde', title: 'jde.f0010_lookup', mono: true }, { tone: 'neutral', text: '+1' }], more: ['f0010_lookup'] })
    // on its own connector: no chip — the query is still what the filter can find, when it is not the record's name
    expect(recordLine('sequences', { description: 'Next role', query: 'next_role_q' }, 'next_role')).toEqual({ sub: 'Next role', marks: [], more: ['next_role_q'] })
    expect(recordLine('sequences', { description: 'Next role', query: 'next_role' }, 'next_role')).toEqual({ sub: 'Next role', marks: [] })
    expect(recordMatches('f0010', 'companies', recordLine('lookups', { connector: 'jde', query: 'f0010_lookup' }, 'companies'))).toBe(true)
    expect(recordMatches('role_q', 'next_role', recordLine('sequences', { query: 'next_role_q' }, 'next_role'))).toBe(true)
  })
  it('is found by its name, its label or a mark', () => {
    const line = recordLine('entries', { label: 'Company', rules: 'lookup', rules_values: 'companies' })
    expect(recordMatches('', 'CO', line)).toBe(true)
    expect(recordMatches('co', 'CO', line)).toBe(true)
    expect(recordMatches('compan', 'XX', line)).toBe(true)
    expect(recordMatches('LOOKUP', 'XX', line)).toBe(true)
    expect(recordMatches('companies', 'XX', line)).toBe(true)     // the rule's target, though the chip shows the rule alone
    expect(recordMatches('zzz', 'CO', line)).toBe(false)
  })
})
