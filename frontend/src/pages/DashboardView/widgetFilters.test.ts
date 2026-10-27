// The period's URL params per widget: the four range binds, the basis, and the column bounds
// when the dashboard names the date column directly or through a dictionary item.
import { describe, expect, it } from 'vitest'
import type { ScreenListItem } from '../../types/screens'
import { buildWidgetFilterParams, buildWidgetPeriodParams } from './widgetFilters'

const screen = { dd_map: { SALES_DATE: 'sold_on' } } as unknown as ScreenListItem
const findScreen = (c: string, q: string) => (c === 'sales' && q === 'facts_get' ? screen : null)
const range = { dateFrom: '2026-10-01', dateTo: '2026-10-27' }

describe('buildWidgetPeriodParams', () => {
  it('sends the range, the previous range and the basis to every widget', () => {
    const out = buildWidgetPeriodParams('sales', 'facts_get', { default: 'thisMonth' }, { range, basis: '' }, findScreen)
    expect(out).toEqual({ PERIOD_FROM: '2026-10-01', PERIOD_TO: '2026-10-27', PREV_FROM: '2026-09-01', PREV_TO: '2026-09-27' })
  })
  it('bounds the date column by name or through the screen dd map', () => {
    expect(buildWidgetPeriodParams('sales', 'facts_get', { default: 'thisMonth', column: 'SOLD_ON' }, { range, basis: '' }, findScreen)).toMatchObject({ SOLD_ON_from: '2026-10-01', SOLD_ON_to: '2026-10-27' })
    expect(buildWidgetPeriodParams('sales', 'facts_get', { default: 'thisMonth', dd: 'SALES_DATE' }, { range, basis: '' }, findScreen)).toMatchObject({ sold_on_from: '2026-10-01', sold_on_to: '2026-10-27' })
    // a widget without a screen column for the dd sends the item itself for the server to resolve
    const out = buildWidgetPeriodParams('other', 'q', { default: 'thisMonth', dd: 'SALES_DATE' }, { range, basis: '' }, findScreen)
    expect(out).toMatchObject({ _dd_SALES_DATE_from: '2026-10-01', _dd_SALES_DATE_to: '2026-10-27' })
  })
  it('the chosen basis picks the column; the first basis is the default', () => {
    const period = { default: 'thisMonth', dd: 'SALES_DATE', basis: [{ value: 'doc', label: 'Doc', dd: 'SALES_DATE' }, { value: 'paid', label: 'Paid', column: 'PAID_ON' }] }
    expect(buildWidgetPeriodParams('sales', 'facts_get', period, { range, basis: 'paid' }, findScreen)).toMatchObject({ PERIOD_BASIS: 'paid', PAID_ON_from: '2026-10-01' })
    expect(buildWidgetPeriodParams('sales', 'facts_get', period, { range, basis: '' }, findScreen)).toMatchObject({ PERIOD_BASIS: 'doc', sold_on_from: '2026-10-01' })
  })
  it('all dates binds the widest range and no column bounds', () => {
    const out = buildWidgetPeriodParams('sales', 'facts_get', { default: 'all', column: 'SOLD_ON' }, { range: { dateFrom: '', dateTo: '' }, basis: '' }, findScreen)
    expect(out).toEqual({ PERIOD_FROM: '0001-01-01', PERIOD_TO: '9999-12-31', PREV_FROM: '0001-01-01', PREV_TO: '9999-12-31' })
    expect(buildWidgetPeriodParams('sales', 'facts_get', undefined, { range, basis: '' }, findScreen)).toEqual({})
  })
})

describe('buildWidgetFilterParams', () => {
  const filters = [{ id: 'bu', label: 'Unit', dictionary_key: 'SALES_DATE', options: { connector: 'c', query: 'q', value_column: 'v', label_column: 'l' } }, { id: 'co', label: 'Company', dictionary_key: 'CO', options: { connector: 'c', query: 'q', value_column: 'v', label_column: 'l' } }]
  it('binds the screen column with equals, else the item as a dictionary bind; blank picks bind nothing', () => {
    expect(buildWidgetFilterParams('sales', 'facts_get', filters, { bu: 'X', co: 'C1' }, findScreen)).toEqual({ sold_on: 'X', sold_on_op: 'equals', _dd_CO: 'C1' })
    expect(buildWidgetFilterParams('other', 'q', filters, { bu: 'X', co: '' }, findScreen)).toEqual({ _dd_SALES_DATE: 'X' })
    expect(buildWidgetFilterParams('other', 'q', [], { bu: 'X' }, findScreen)).toEqual({})
  })
})
