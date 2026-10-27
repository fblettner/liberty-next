// Data-scope rule helpers: wire clean-up, descriptions, the dictionary item list and value sources.
import { describe, expect, it } from 'vitest'
import type { DictionaryDoc } from '../../../types/config'
import { attributeFromText, attributeKeys, attributeOf, attributeToText, cleanRule, describeRule, describeScope, dictionaryItems, screenColumnNames, sourceFor, valueSourceFor } from './scopeRules'

const doc: DictionaryDoc = {
  path: 'dictionary.toml',
  dictionary: {
    entries: { CO: { label: 'Company', rules: 'LOOKUP', rules_values: 'companies' } },
    lookups: { companies: { query: 'companies_get', value: 'CCCO', label: 'CCNAME' } },
    enums: { yesno: { values: [{ value: 'Y', label: 'Yes' }, { value: 'N' }] } },
    connectors: {
      jdedwards: { entries: { SY: { label: 'System code', rules: 'LOOKUP', rules_values: 'companies' }, FLAG: { rules: 'ENUM', rules_values: 'yesno' } } },
    },
  },
}

describe('scope rules', () => {
  it('cleanRule keeps only what the backend needs', () => {
    expect(cleanRule({ dd: 'CO', column: 'X', values: ['1'], source: null, exclude: false, mode: 'ignore', connector: null })).toEqual({ dd: 'CO', values: ['1'] })
    expect(cleanRule({ column: 'COMPANY', source: '#USER.company#', exclude: true, mode: 'deny', connector: 'nomasx1' })).toEqual({ column: 'COMPANY', source: '#USER.company#', exclude: true, mode: 'deny', connector: 'nomasx1' })
    expect(attributeOf('#USER.company#')).toBe('company')
    expect(attributeOf('company')).toBeNull()
    expect(sourceFor('pole')).toBe('#USER.pole#')
  })

  it('describes a rule and a resolved scope in words', () => {
    const labels = { CO: 'Company' }
    expect(describeRule({ dd: 'CO', values: ['00001', '00050'] }, labels)).toBe('Company (CO) in 00001, 00050')
    expect(describeRule({ dd: 'SY', values: ['00'], exclude: true, mode: 'deny', connector: 'jdedwards' })).toBe('SY not in 00  [deny when absent, jdedwards]')
    expect(describeRule({ column: 'COMPANY', source: '#USER.company#' })).toBe('COMPANY = user.company')
    expect(describeRule({ dd: 'CO', values: [] })).toBe('CO: nothing allowed')
    expect(describeScope([[{ dd: 'CO', values: ['A'] }, { dd: 'POLE', values: ['X'] }], [{ dd: 'CO', values: ['B'] }]])).toEqual(['CO in A  and  POLE in X', 'CO in B'])
    expect(describeScope(null)).toEqual([])
  })

  it('lists dictionary items across sections and finds where their values come from', () => {
    const items = dictionaryItems(doc)
    expect(items.map((i) => `${i.section}:${i.key}`)).toEqual(['shared:CO', 'jdedwards:FLAG', 'jdedwards:SY'])
    // a shared LOOKUP with no connector of its own has no runnable source
    expect(valueSourceFor(items[0], doc)).toBeNull()
    // a per-connector entry runs the lookup on its connector
    expect(valueSourceFor(items[2], doc)).toEqual({ kind: 'lookup', spec: { connector: 'jdedwards', query: 'companies_get', value: 'CCCO', label: 'CCNAME' } })
    // an ENUM gives its members, a missing label falls back to the code
    expect(valueSourceFor(items[1], doc)).toEqual({ kind: 'enum', options: [{ value: 'Y', label: 'Yes' }, { value: 'N', label: 'N' }] })
    expect(dictionaryItems(null)).toEqual([])
  })

  it('collects the screens\' column names per connector', () => {
    const doc = { path: 'screens.toml', screens: {
      nomasx1: { users: { read_query: 'q', columns: [{ name: 'USR_ID' }, { name: 'COMPANY' }] }, roles: { read_query: 'r', connector: 'other', columns: [{ name: 'ROL_ID' }] } },
      nomajde: { f0005: { read_query: 'f', columns: [{ name: 'DRSY' }, { name: 'COMPANY' }] } },
    } }
    expect(screenColumnNames(doc as never)).toEqual({ nomasx1: ['COMPANY', 'USR_ID'], other: ['ROL_ID'], nomajde: ['COMPANY', 'DRSY'] })
    expect(screenColumnNames(null)).toEqual({})
  })

  it('collects attribute names and converts attribute text', () => {
    expect(attributeKeys([{ scope: [{ dd: 'CO', source: '#USER.company#' }] }, { scope: null }], [{ attributes: { pole: 'N' } }, {}])).toEqual(['company', 'pole'])
    expect(attributeToText(['A', 'B'])).toBe('A, B')
    expect(attributeToText('A')).toBe('A')
    expect(attributeFromText('A, B ,')).toEqual(['A', 'B'])
    expect(attributeFromText(' A ')).toBe('A')
    expect(attributeFromText('')).toBe('')
  })
})
