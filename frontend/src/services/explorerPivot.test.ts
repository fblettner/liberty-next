import { describe, expect, it } from 'vitest'
import { buildPivot, flattenPivot, isAdditive, kpiTotals, pivotToSheet, shapeChart, share } from './explorerPivot'
import type { AggregateResult, ResultColumn } from '../types/explorer'

const dim = (key: string, extra: Partial<ResultColumn> = {}): ResultColumn => ({ key, kind: 'dimension', field: key, entity: 'sales', label: key, type: 'string', ...extra })
const mea = (key: string, aggregate: ResultColumn['aggregate'] = 'sum'): ResultColumn => ({ key, kind: 'measure', field: 'sales.QTY', entity: 'sales', label: 'QTY', type: 'number', aggregate })

const RES: AggregateResult = {
  mode: 'aggregate', entity: 'sales', groups: 4, scanned: 4, truncated: false, scan_truncated: false,
  columns: [dim('company'), dim('month', { bucket: 'month', type: 'date' }), mea('sum(sales.QTY)'), mea('avg(sales.QTY)', 'avg')],
  rows: [
    { company: 'C1', month: '2026-02', 'sum(sales.QTY)': 2, 'avg(sales.QTY)': 2 },
    { company: 'C1', month: '2026-01', 'sum(sales.QTY)': 3, 'avg(sales.QTY)': 3 },
    { company: 'C2', month: '2026-01', 'sum(sales.QTY)': 5, 'avg(sales.QTY)': 5 },
    { company: null, month: '2026-03', 'sum(sales.QTY)': 1, 'avg(sales.QTY)': 1 },
  ],
}

describe('pivot', () => {
  it('builds the hierarchy with subtotals for an additive measure', () => {
    const p = buildPivot(RES.rows, ['company'], 'month', 'sum(sales.QTY)', true)
    expect(p.columns).toEqual(['2026-01', '2026-02', '2026-03'])
    expect(p.root.total).toBe(11)
    expect(p.root.cells).toEqual({ '2026-01': 8, '2026-02': 2, '2026-03': 1 })
    const c1 = p.root.children[0]
    expect(c1.value).toBe('C1')
    expect(c1.total).toBe(5)
    expect(c1.cells).toEqual({ '2026-02': 2, '2026-01': 3 })
    expect(p.root.children[2].value).toBeNull()
    expect(share(c1.cells['2026-01'], p.root.cells['2026-01'], p.root.total, 'parent')).toBeCloseTo(3 / 8)
    expect(share(c1.total, null, p.root.total, 'total')).toBeCloseTo(5 / 11)
    expect(share(null, 1, 1, 'total')).toBeNull()
  })
  it('keeps leaf values only for a non-additive measure', () => {
    const p = buildPivot(RES.rows, ['company', 'month'], null, 'avg(sales.QTY)', false)
    expect(p.columns).toEqual([''])
    expect(p.root.total).toBeNull()
    expect(p.root.children[0].total).toBeNull()                  // the company level has no subtotal
    expect(p.root.children[0].children[0].cells).toEqual({ '': 2 })
  })
  it('flattens with collapsed nodes and exports a sheet', () => {
    const p = buildPivot(RES.rows, ['company', 'month'], null, 'sum(sales.QTY)', true)
    expect(flattenPivot(p.root, new Set()).map((x) => x.id)).toEqual(['/C1', '/C1/2026-02', '/C1/2026-01', '/C2', '/C2/2026-01', '/', '//2026-03'])
    expect(flattenPivot(p.root, new Set(['/C1'])).map((x) => x.id)).toEqual(['/C1', '/C2', '/C2/2026-01', '/', '//2026-03'])
    const sheet = pivotToSheet(buildPivot(RES.rows, ['company'], 'month', 'sum(sales.QTY)', true), ['Company'], 'Total')
    expect(sheet[0]).toEqual(['Company', '2026-01', '2026-02', '2026-03', 'Total'])
    expect(sheet[1]).toEqual(['C1', 3, 2, null, 5])
  })
  it('knows which aggregates add up', () => {
    expect(isAdditive('sum') && isAdditive('count')).toBe(true)
    expect(isAdditive('avg') || isAdditive('min') || isAdditive('count_distinct')).toBe(false)
  })
})

describe('chart shaping', () => {
  it('takes x from the first dimension and series from the second', () => {
    const s = shapeChart(RES, { type: 'bar', measure: 'sum(sales.QTY)', othersLabel: 'Others' })!
    expect(s.spec).toEqual({ type: 'bar', x: 'company', y: ['2026-02', '2026-01', '2026-03'], aggregation: 'none' })
    // x by value descending: C1 (5) and C2 (5) tie, so input order holds — then the null
    expect(s.result.rows.map((r) => r.company)).toEqual(['C1', 'C2', ''])
    expect(s.result.rows[0]).toEqual({ company: 'C1', '2026-02': 2, '2026-01': 3, '2026-03': 0 })
  })
  it('orders a bucketed date chronologically and uses the measures as series otherwise', () => {
    const s = shapeChart(RES, { type: 'line', measure: 'sum(sales.QTY)', seriesDim: 'company', othersLabel: 'Others' })!
    expect(s.spec.x).toBe('month')
    expect(s.result.rows.map((r) => r.month)).toEqual(['2026-01', '2026-02', '2026-03'])
    const noSeries = shapeChart({ ...RES, columns: [dim('month', { bucket: 'month' }), mea('sum(sales.QTY)'), mea('avg(sales.QTY)', 'avg')] }, { type: 'bar', othersLabel: 'Others' })!
    expect(noSeries.spec.y).toEqual(['sum(sales.QTY)', 'avg(sales.QTY)'])
    expect(noSeries.result.rows[0]).toEqual({ month: '2026-01', 'sum(sales.QTY)': 8, 'avg(sales.QTY)': 8 })
    // a non-additive measure never gets a series dimension (summing averages is wrong)
    const avg = shapeChart(RES, { type: 'bar', measure: 'avg(sales.QTY)', othersLabel: 'Others' })!
    expect(avg.spec.y).toEqual(['sum(sales.QTY)', 'avg(sales.QTY)'])
  })
  it('folds beyond the top 10 into Others', () => {
    const rows = Array.from({ length: 13 }, (_, i) => ({ x: 'a', s: `s${i}`, 'sum(sales.QTY)': 13 - i }))
    const res: AggregateResult = { ...RES, columns: [dim('x'), dim('s'), mea('sum(sales.QTY)')], rows }
    const s = shapeChart(res, { type: 'bar', othersLabel: 'Others' })!
    expect(s.series.length).toBe(11)
    expect(s.series.at(-1)).toBe('Others')
    expect(s.othersLabel).toBe(true)
    expect(s.result.rows[0].Others).toBe(3 + 2 + 1)
    const pie = shapeChart({ ...RES, columns: [dim('s'), mea('sum(sales.QTY)')], rows: rows.map((r) => ({ s: r.s, 'sum(sales.QTY)': r['sum(sales.QTY)'] })) }, { type: 'pie', othersLabel: 'Others' })!
    expect(pie.result.rows.length).toBe(11)
    expect(pie.result.rows.at(-1)).toEqual({ s: 'Others', 'sum(sales.QTY)': 6 })
  })
  it('totals the measures for the KPI cards', () => {
    const k = kpiTotals(RES)
    expect(k.map((x) => [x.column.key, x.value])).toEqual([['sum(sales.QTY)', 11], ['avg(sales.QTY)', 2.75]])
    expect(shapeChart({ ...RES, columns: [dim('x')], rows: [] }, { type: 'bar', othersLabel: 'O' })).toBeNull()
  })
})
