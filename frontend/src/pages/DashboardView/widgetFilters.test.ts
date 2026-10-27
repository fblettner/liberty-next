// The period's URL params per widget: the four range binds, the basis, and the column bounds
// when the dashboard names the date column directly or through a dictionary item.
import { describe, expect, it } from 'vitest'
import type { ScreenListItem } from '../../types/screens'
import { buildWidgetPeriodParams } from './widgetFilters'

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
    // a widget whose screen lacks the dd gets the binds only
    const out = buildWidgetPeriodParams('other', 'q', { default: 'thisMonth', dd: 'SALES_DATE' }, { range, basis: '' }, findScreen)
    expect(Object.keys(out).sort()).toEqual(['PERIOD_FROM', 'PERIOD_TO', 'PREV_FROM', 'PREV_TO'])
  })
  it('the chosen basis picks the column; the first basis is the default', () => {
    const period = { default: 'thisMonth', dd: 'SALES_DATE', basis: [{ value: 'doc', label: 'Doc', dd: 'SALES_DATE' }, { value: 'paid', label: 'Paid', column: 'PAID_ON' }] }
    expect(buildWidgetPeriodParams('sales', 'facts_get', period, { range, basis: 'paid' }, findScreen)).toMatchObject({ PERIOD_BASIS: 'paid', PAID_ON_from: '2026-10-01' })
    expect(buildWidgetPeriodParams('sales', 'facts_get', period, { range, basis: '' }, findScreen)).toMatchObject({ PERIOD_BASIS: 'doc', sold_on_from: '2026-10-01' })
  })
  it('an empty range (all dates) sends nothing', () => {
    expect(buildWidgetPeriodParams('sales', 'facts_get', { default: 'all' }, { range: { dateFrom: '', dateTo: '' }, basis: '' }, findScreen)).toEqual({})
    expect(buildWidgetPeriodParams('sales', 'facts_get', undefined, { range, basis: '' }, findScreen)).toEqual({})
  })
})
