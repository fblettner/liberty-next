import { describe, expect, it } from 'vitest'
import {
  addField, addFilter, buildAggregateRequest, buildDetailRequest, compileFilters, dateFields, defaultOp, dimKey, drillCellsOf,
  emptyDefinition, fieldIndex, measureKey, moveItem, opsFor, usedKeys, withCalculated,
} from './explorer'
import type { AnalysisDefinition, ExplorerField, ExplorerModel } from '../types/explorer'

const f = (key: string, type: ExplorerField['type'], kind: ExplorerField['kind'] = 'dimension', extra: Partial<ExplorerField> = {}): ExplorerField => {
  const [entity, name] = key.split('.')
  return { key, entity, name, label: name, type, kind, ...extra }
}
const MODEL: ExplorerModel = {
  app: 'shop', model: 'sales', label: 'Shop', hub: 'sales', relations: [], warnings: [],
  entities: [
    { id: 'sales', label: 'Sales', query: 'q', key: ['ID'], cardinality: 'one', default_date: 'SOLD_ON',
      fields: [f('sales.ID', 'number'), f('sales.SOLD_ON', 'date'), f('sales.QTY', 'number', 'measure', { aggregate: 'sum' }), f('sales.STATUS', 'string', 'dimension', { rule: { kind: 'enum', values: [] } })] },
    { id: 'company', label: 'Company', query: 'c', key: ['COMPANY'], cardinality: 'one', fields: [f('company.NAME', 'string'), f('company.SINCE', 'date')] },
  ],
}

describe('keys and operators', () => {
  it('builds result keys', () => {
    expect(dimKey('sales.SOLD_ON', 'month')).toBe('sales.SOLD_ON@month')
    expect(dimKey('sales.ID')).toBe('sales.ID')
    expect(measureKey('sales.QTY', 'avg')).toBe('avg(sales.QTY)')
    expect(measureKey(null, 'count')).toBe('count(*)')
  })
  it('offers operators by type and a sensible default', () => {
    expect(opsFor('string')).toContain('contains')
    expect(opsFor('number')).not.toContain('contains')
    expect(opsFor('date')).toContain('between')
    expect(defaultOp('string', false)).toBe('contains')
    expect(defaultOp('string', true)).toBe('in')
    expect(defaultOp('date', false)).toBe('between')
    expect(defaultOp('number', false)).toBe('equals')
  })
})

describe('filters', () => {
  it('compiles rows to the flat bag', () => {
    expect(compileFilters([
      { field: 'company.NAME', op: 'contains', value: 'corp' },
      { field: 'sales.QTY', op: 'between', value: '3', valueTo: '5' },
      { field: 'sales.STATUS', op: 'in', value: 'O, C,' },
      { field: 'sales.ID', op: 'empty', value: '' },
      { field: 'sales.ID', op: 'gt', value: '' },           // no value → skipped
      { field: 'sales.SOLD_ON', op: 'between', value: '', valueTo: '2026-01-31' },
    ])).toEqual({
      'company.NAME': 'corp', 'company.NAME__op': 'contains',
      'sales.QTY__from': '3', 'sales.QTY__to': '5',
      'sales.STATUS': ['O', 'C'], 'sales.STATUS__op': 'in',
      'sales.ID': null, 'sales.ID__op': 'empty',
      'sales.SOLD_ON__to': '2026-01-31',
    })
  })
})

describe('requests', () => {
  const def: AnalysisDefinition = {
    ...emptyDefinition('sales'),
    rows: [{ field: 'company.NAME' }, { field: 'sales.SOLD_ON', bucket: 'month' }],
    measures: [{ field: 'sales.QTY', agg: 'sum' }, { field: null, agg: 'count' }],
    filterRows: [{ field: 'company.NAME', op: 'startsWith', value: 'C' }],
    period: { field: 'sales.SOLD_ON', preset: 'custom', range: { dateFrom: '2026-01-01', dateTo: '2026-03-31' } },
    sort: [{ key: 'sum(sales.QTY)', dir: 'desc' }],
    limit: 50,
    calculated: [{ name: 'up', expr: 'REVENUE / QTY' }],
  }
  it('builds the aggregate request', () => {
    expect(buildAggregateRequest(def, { debug: true })).toEqual({
      entity: 'sales', calculated: [{ name: 'up', expr: 'REVENUE / QTY' }],
      rows: [{ field: 'company.NAME' }, { field: 'sales.SOLD_ON', bucket: 'month' }],
      measures: [{ field: 'sales.QTY', agg: 'sum' }, { agg: 'count' }],
      filters: { 'company.NAME': 'C', 'company.NAME__op': 'startsWith' },
      period: { field: 'sales.SOLD_ON', from: '2026-01-01', to: '2026-03-31' },
      sort: [{ key: 'sum(sales.QTY)', dir: 'desc' }], limit: 50, debug: true,
    })
    // "all" means no period; a preset resolves to real dates
    expect(buildAggregateRequest({ ...def, period: { field: 'sales.SOLD_ON', preset: 'all', range: { dateFrom: '', dateTo: '' } } }).period).toBeNull()
    const p = buildAggregateRequest({ ...def, period: { field: 'sales.SOLD_ON', preset: 'thisYear', range: { dateFrom: '', dateTo: '' } } }).period
    expect(p?.from?.slice(5)).toBe('01-01')
  })
  it('builds the detail request with a drill, measure sorts dropped', () => {
    const row = { 'company.NAME': 'Corp one', 'sales.SOLD_ON@month': '2026-01', 'sum(sales.QTY)': 5 }
    const drill = drillCellsOf(row, def.rows)
    expect(drill).toEqual([{ field: 'company.NAME', bucket: null, value: 'Corp one' }, { field: 'sales.SOLD_ON', bucket: 'month', value: '2026-01' }])
    const r = buildDetailRequest(def, { drill, page: 2, pageSize: 25 })
    expect(r.columns).toEqual(['company.NAME', 'sales.SOLD_ON'])
    expect(r.drill).toEqual([{ field: 'company.NAME', value: 'Corp one' }, { field: 'sales.SOLD_ON', bucket: 'month', value: '2026-01' }])
    expect(r.sort).toEqual([])
    expect(r.page).toBe(2)
    expect(r.page_size).toBe(25)
  })
})

describe('fields pane helpers', () => {
  it('adds a calc pseudo-entity typed from the tests', () => {
    const ents = withCalculated(MODEL, [{ name: 'up', expr: 'x' }, { name: 'tag', expr: 'y' }], { up: 'number' })
    expect(ents.at(-1)?.id).toBe('calc')
    const idx = fieldIndex(ents)
    expect(idx.get('calc.up')).toMatchObject({ kind: 'measure', aggregate: 'sum', type: 'number' })
    expect(idx.get('calc.tag')).toMatchObject({ kind: 'dimension', type: 'string' })
    expect(withCalculated(MODEL, [])).toBe(MODEL.entities)
  })
  it('lists date fields with the primary default first', () => {
    expect(dateFields(MODEL.entities, 'sales').map((x) => x.key)).toEqual(['sales.SOLD_ON', 'company.SINCE'])
    expect(dateFields(MODEL.entities, 'company').map((x) => x.key)).toEqual(['sales.SOLD_ON', 'company.SINCE'])
  })
  it('adds fields by kind and view, without duplicates', () => {
    const idx = fieldIndex(MODEL.entities)
    let def = emptyDefinition('sales')
    def = addField(def, idx.get('sales.QTY')!)
    def = addField(def, idx.get('sales.SOLD_ON')!)
    def = addField(def, idx.get('sales.SOLD_ON')!)
    expect(def.measures).toEqual([{ field: 'sales.QTY', agg: 'sum' }])
    expect(def.rows).toEqual([{ field: 'sales.SOLD_ON', bucket: 'month' }])
    const detail = addField({ ...emptyDefinition('sales'), view: 'detail' }, idx.get('sales.QTY')!)
    expect(detail.rows).toEqual([{ field: 'sales.QTY' }])
    expect(detail.measures).toEqual([])
    def = addFilter(def, idx.get('sales.STATUS')!)
    expect(def.filterRows).toEqual([{ field: 'sales.STATUS', op: 'in', value: '' }])
    expect([...usedKeys(def)].sort()).toEqual(['sales.QTY', 'sales.SOLD_ON', 'sales.STATUS'])
  })
  it('moves items', () => {
    expect(moveItem([1, 2, 3], 0, 2)).toEqual([2, 3, 1])
    expect(moveItem([1, 2, 3], 2, 0)).toEqual([3, 1, 2])
    expect(moveItem([1, 2, 3], 1, 5)).toEqual([1, 2, 3])
  })
})
