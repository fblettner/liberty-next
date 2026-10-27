// Period presets, the previous-period rule and the range label.
import { describe, expect, it } from 'vitest'
import { formatRange, previousPeriod, rangeForPreset } from './period'

const today = new Date(2026, 9, 27) // 27 Oct 2026, a Tuesday

describe('rangeForPreset', () => {
  it('covers the usual presets as of today', () => {
    expect(rangeForPreset('today', today)).toEqual({ dateFrom: '2026-10-27', dateTo: '2026-10-27' })
    expect(rangeForPreset('yesterday', today)).toEqual({ dateFrom: '2026-10-26', dateTo: '2026-10-27' })
    expect(rangeForPreset('last7', today)).toEqual({ dateFrom: '2026-10-21', dateTo: '2026-10-27' })
    expect(rangeForPreset('last30', today)).toEqual({ dateFrom: '2026-09-28', dateTo: '2026-10-27' })
    expect(rangeForPreset('thisMonth', today)).toEqual({ dateFrom: '2026-10-01', dateTo: '2026-10-27' })
    expect(rangeForPreset('lastMonth', today)).toEqual({ dateFrom: '2026-09-01', dateTo: '2026-09-30' })
    expect(rangeForPreset('thisQuarter', today)).toEqual({ dateFrom: '2026-10-01', dateTo: '2026-10-27' })
    expect(rangeForPreset('thisYear', today)).toEqual({ dateFrom: '2026-01-01', dateTo: '2026-10-27' })
    expect(rangeForPreset('lastYear', today)).toEqual({ dateFrom: '2025-01-01', dateTo: '2025-12-31' })
    expect(rangeForPreset('last12', today)).toEqual({ dateFrom: '2025-10-28', dateTo: '2026-10-27' })
    expect(rangeForPreset('all', today)).toEqual({ dateFrom: '', dateTo: '' })
  })
})

describe('previousPeriod', () => {
  it('year to date compares with last year to date', () => {
    expect(previousPeriod({ dateFrom: '2026-01-01', dateTo: '2026-10-27' })).toEqual({ dateFrom: '2025-01-01', dateTo: '2025-10-27' })
    expect(previousPeriod({ dateFrom: '2026-01-01', dateTo: '2026-02-28' })).toEqual({ dateFrom: '2025-01-01', dateTo: '2025-02-28' })  // month end stays month end
  })
  it('a month or a quarter shifts by the months covered', () => {
    expect(previousPeriod({ dateFrom: '2026-10-01', dateTo: '2026-10-27' })).toEqual({ dateFrom: '2026-09-01', dateTo: '2026-09-27' })   // month to date
    expect(previousPeriod({ dateFrom: '2026-09-01', dateTo: '2026-09-30' })).toEqual({ dateFrom: '2026-08-01', dateTo: '2026-08-31' })   // whole month
    expect(previousPeriod({ dateFrom: '2026-07-01', dateTo: '2026-09-30' })).toEqual({ dateFrom: '2026-04-01', dateTo: '2026-06-30' })   // whole quarter
    expect(previousPeriod({ dateFrom: '2026-03-01', dateTo: '2026-03-31' })).toEqual({ dateFrom: '2026-02-01', dateTo: '2026-02-28' })   // shorter month
    expect(previousPeriod({ dateFrom: '2026-10-01', dateTo: '2026-10-01' })).toEqual({ dateFrom: '2026-09-01', dateTo: '2026-09-01' })   // the 1st alone
  })
  it('anything else takes the same length just before', () => {
    expect(previousPeriod({ dateFrom: '2026-10-21', dateTo: '2026-10-27' })).toEqual({ dateFrom: '2026-10-14', dateTo: '2026-10-20' })
    expect(previousPeriod({ dateFrom: '2026-10-27', dateTo: '2026-10-27' })).toEqual({ dateFrom: '2026-10-26', dateTo: '2026-10-26' })
    expect(previousPeriod({ dateFrom: '', dateTo: '' })).toEqual({ dateFrom: '', dateTo: '' })
  })
})

describe('formatRange', () => {
  it('shortens what the two dates share', () => {
    expect(formatRange({ dateFrom: '2026-09-01', dateTo: '2026-09-30' }, 'en')).toMatch(/^1 – (Sep|Sept) 30, 2026$|^1 – 30 Sep/)
    expect(formatRange({ dateFrom: '2026-10-25', dateTo: '2026-10-25' }, 'en')).toMatch(/Oct 25, 2026|25 Oct 2026/)
    expect(formatRange({ dateFrom: '', dateTo: '' }, 'en')).toBe('')
  })
})
