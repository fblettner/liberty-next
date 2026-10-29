import { describe, expect, it } from 'vitest'
import type { Column } from '../types/connectors'
import { defaultPeriods, filterBinds, isActiveFilter, isDateColumn, seededFilter } from './serverFilters'

describe('serverFilters', () => {
  it('sends a period as <col>_from / <col>_to and a value as <col> + <col>_op', () => {
    expect(filterBinds({
      checked_at: { op: 'period', val: '2026-10-23', to: '2026-10-29', preset: 'last7' },
      opened: { op: 'period', val: '', to: '2026-10-29' },
      instance: { op: 'contains', val: 'Ent' },
      empty: { op: 'equals', val: '' },
    })).toEqual({
      checked_at_from: '2026-10-23', checked_at_to: '2026-10-29',
      opened_to: '2026-10-29',
      instance: 'Ent', instance_op: 'contains',
    })
  })

  it('a period with no bound filters nothing', () => {
    expect(filterBinds({ d: { op: 'period', val: '', to: '', preset: 'all' } })).toEqual({})
    expect(isActiveFilter({ op: 'period', val: '', to: '' })).toBe(false)
    expect(isActiveFilter({ op: 'period', val: '', to: '2026-10-29' })).toBe(true)
    expect(isActiveFilter({ op: 'equals', val: 'x' })).toBe(true)
  })

  it('a date column by its format or its type', () => {
    expect(isDateColumn({ format: 'datetime', type: null })).toBe(true)
    expect(isDateColumn({ type: 'timestamp with time zone' })).toBe(true)
    expect(isDateColumn({ format: 'text', type: 'varchar' })).toBe(false)
    expect(isDateColumn({ type: 'update_date_x' })).toBe(false)
  })

  it('a drill’s date on a date column seeds that one day as a period, anything else an exact match', () => {
    expect(seededFilter({ format: 'datetime' }, '2026-10-29')).toEqual({ op: 'period', val: '2026-10-29', to: '2026-10-29', preset: 'custom' })
    expect(filterBinds({ checked_at: seededFilter({ format: 'datetime' }, '2026-10-29') }))
      .toEqual({ checked_at_from: '2026-10-29', checked_at_to: '2026-10-29' })
    expect(seededFilter({ format: 'datetime' }, '2026-10-29T08:00:00')).toEqual({ op: 'equals', val: '2026-10-29T08:00:00' })
    expect(seededFilter({ format: 'text' }, '2026-10-29')).toEqual({ op: 'equals', val: '2026-10-29' })
    expect(seededFilter(undefined, 'JDE')).toEqual({ op: 'equals', val: 'JDE' })
  })

  it('opens each date filter on its column’s filter_period, as of today', () => {
    const cols = [
      { name: 'check_date', format: 'date', filter: true, filter_period: 'last7' },
      { name: 'no_preset', format: 'date', filter: true },
      { name: 'all', format: 'date', filter: true, filter_period: 'all' },
      { name: 'not_a_date', format: 'text', filter: true, filter_period: 'last7' },
      { name: 'not_a_filter', format: 'date', filter_period: 'last7' },
    ] as Column[]
    expect(defaultPeriods(cols, new Date(2026, 9, 29))).toEqual({
      check_date: { op: 'period', val: '2026-10-23', to: '2026-10-29', preset: 'last7' },
    })
  })
})
