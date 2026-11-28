import { describe, expect, it } from 'vitest'
import { cleanUserParam, cleanUserParams, composeChoicesFrom, forEachCandidates, parseChoicesFrom } from './jobParams'

describe("where a job parameter's choices come from", () => {
  it('reads each form the file may hold', () => {
    expect(parseChoicesFrom('')).toEqual({ kind: 'none' })
    expect(parseChoicesFrom('query:nomasx1/settings_applications_get:APPS_ID:APPS_NAME'))
      .toEqual({ kind: 'query', connector: 'nomasx1', query: 'settings_applications_get', value: 'APPS_ID', label: 'APPS_NAME' })
    expect(parseChoicesFrom('query:db/units:CODE')).toEqual({ kind: 'query', connector: 'db', query: 'units', value: 'CODE', label: '' })
    expect(parseChoicesFrom('explorer_models:nomajde')).toEqual({ kind: 'explorer_models', app: 'nomajde' })
    expect(parseChoicesFrom('test_suites')).toEqual({ kind: 'test_suites', connector: '' })
    expect(parseChoicesFrom('test_suites:ais_connection')).toEqual({ kind: 'test_suites', connector: 'ais_connection' })
    expect(parseChoicesFrom('pools:jdedwards')).toEqual({ kind: 'pools', connector: 'jdedwards' })
    expect(composeChoicesFrom({ kind: 'pools', connector: '' })).toBe('pools')
    expect(parseChoicesFrom('connectors:sql')).toEqual({ kind: 'connectors', type: 'sql' })
    expect(composeChoicesFrom({ kind: 'connectors', type: '' })).toBe('connectors')
    expect(parseChoicesFrom('connectors:other')).toEqual({ kind: 'other', text: 'connectors:other' })
  })

  it('writes back exactly what it read', () => {
    for (const s of ['query:nomasx1/settings_applications_get:APPS_ID:APPS_NAME', 'query:db/units:CODE', 'explorer_models:nomajde', 'test_suites', 'test_suites:ais_connection'])
      expect(composeChoicesFrom(parseChoicesFrom(s))).toBe(s)
    expect(composeChoicesFrom({ kind: 'none' })).toBe('')
  })

  it('keeps a source it does not know as written', () => {
    expect(parseChoicesFrom('something:else')).toEqual({ kind: 'other', text: 'something:else' })
    expect(parseChoicesFrom('query:a/b:c:d:e')).toEqual({ kind: 'other', text: 'query:a/b:c:d:e' })
    expect(composeChoicesFrom(parseChoicesFrom('query:a/b:c:d:e'))).toBe('query:a/b:c:d:e')
  })
})

describe('a parameter as it is saved', () => {
  it('leaves out what is blank or off', () => {
    expect(cleanUserParam({ name: ' apps_id ', label: 'Applications', l: { fr: 'Applications', de: ' ' }, step: '', default: '', required: false,
      choices: [' ', 'a'], choices_from: '', multiple: true }))
      .toEqual({ name: 'apps_id', label: 'Applications', l: { fr: 'Applications' }, choices: ['a'], multiple: true })
    expect(cleanUserParam({ name: 'n' })).toEqual({ name: 'n' })
    expect(cleanUserParam({ name: 'n', default: '0' })).toEqual({ name: 'n', default: '0' })
  })

  it('drops an unnamed parameter, and the list when nothing is left', () => {
    expect(cleanUserParams([{ name: '' }, { name: 'a' }])).toEqual([{ name: 'a' }])
    expect(cleanUserParams([{ name: ' ' }])).toBeUndefined()
    expect(cleanUserParams(undefined)).toBeUndefined()
  })

  it('offers the names "one run per value" may point at', () => {
    expect(forEachCandidates({ source_connector: 'jde', apps_id: 'all' }, [{ name: 'apps_id' }, { name: 'period' }])).toEqual(['apps_id', 'period', 'source_connector'])
    expect(forEachCandidates(undefined, undefined)).toEqual([])
  })
})
