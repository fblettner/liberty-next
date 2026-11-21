import { describe, expect, it } from 'vitest'
import { expectSummary, freshStepName, linesList, linesText, pairsObject, pairsText, tidyStep } from './appTests'

describe('the tests editor', () => {
  it('shows a dictionary as lines and reads it back', () => {
    expect(pairsText({ AN8: '9999', MAX: 5, deep: { a: 1 } })).toBe('AN8 = 9999\nMAX = 5\ndeep = {"a":1}')
    expect(pairsObject('AN8 = 9999\nALPH = TEST')).toEqual({ AN8: '9999', ALPH: 'TEST' })
    expect(pairsObject('')).toBeUndefined()
    expect(linesList(' a.b \n\n c ')).toEqual(['a.b', 'c'])
    expect(linesList('')).toBeUndefined()
    expect(linesText(['a', 'b'])).toBe('a\nb')
  })
  it('says what a step expects in a few words', () => {
    expect(expectSummary({ name: 's', endpoint: 'e' })).toBe('works')
    expect(expectSummary({ name: 's', endpoint: 'e', expect: { rows_min: 1, first_row: { AN8: '9999', ALPH: 'TEST' }, present: ['x'] } })).toBe('≥ 1 row(s) · 3 value(s)')
    expect(expectSummary({ name: 's', endpoint: 'e', expect: { success: false, error_contains: 'unknown table' } })).toBe('refused (unknown table)')
  })
  it('keeps a step tidy for the file, and names a new one', () => {
    expect(tidyStep({ name: 's', endpoint: 'e', params: {}, capture: {}, keep: [], always: false, rows: '',
      expect: { success: true, rows: null, rows_min: 1, first_row: {}, present: [], error_contains: '' } }))
      .toEqual({ name: 's', endpoint: 'e', expect: { rows_min: 1 } })
    expect(tidyStep({ name: 's', endpoint: 'e', expect: { success: false } }).expect).toEqual({ success: false })
    expect(freshStepName([{ name: 'step 2', endpoint: 'e' }])).toBe('step 3')
    expect(freshStepName([])).toBe('step 1')
  })
})
