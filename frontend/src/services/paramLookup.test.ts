import { describe, expect, it } from 'vitest'
import { paramLookupSpec } from './paramLookup'

const lookup = { kind: 'lookup' as const, connector: 'jde', query: 'udos_get', value: 'UDO', label: 'NAME' }

describe('paramLookupSpec', () => {
  it('is the lookup itself when nothing narrows it', () => {
    expect(paramLookupSpec(lookup, ['WHO'], {})).toEqual({ spec: { connector: 'jde', query: 'udos_get', value: 'UDO', label: 'NAME' }, waiting: [] })
  })
  it('sends the value of the parameter a bind names, and a fixed value as it is', () => {
    const got = paramLookupSpec({ ...lookup, binds: { KIND: 'category', SY: '04' } }, ['WHO', 'CATEGORY'], { CATEGORY: ' WATCHLIST ' })
    expect(got.spec?.params).toEqual({ KIND: 'WATCHLIST', SY: '04' })
  })
  it('waits for the parameter that narrows it', () => {
    expect(paramLookupSpec({ ...lookup, binds: { KIND: 'CATEGORY' } }, ['CATEGORY'], { CATEGORY: '' })).toEqual({ spec: null, waiting: ['CATEGORY'] })
  })
  it('keeps the sources of a lookup made of several queries', () => {
    const sources = [{ connector: 'a', query: 'q1' }, { connector: 'b', query: 'q2' }]
    expect(paramLookupSpec({ ...lookup, sources }, [], {}).spec?.sources).toEqual(sources)
  })
})
