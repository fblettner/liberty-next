// Pure helpers of the Data Explorer (no React): the analysis definition ⇄ the backend requests,
// the field catalogue with calculated fields, operators by type, keys. Tested in explorer.test.ts.
import type {
  Aggregate, AnalysisDefinition, Bucket, CalcDef, DrillCell, ExplorerEntity, ExplorerField, ExplorerModel, FieldType, FilterOp, FilterRow,
} from '../types/explorer'
import { rangeForPreset, type DateRange } from './period'

export const AGGREGATES: Aggregate[] = ['sum', 'avg', 'min', 'max', 'count', 'count_distinct']
export const NUMERIC_AGGREGATES: Aggregate[] = ['sum', 'avg', 'min', 'max', 'count', 'count_distinct']
export const BUCKETS: Bucket[] = ['day', 'week', 'month', 'quarter', 'year']
export const DEFAULT_LIMIT = 1000

export function emptyDefinition(entity: string): AnalysisDefinition {
  return { entity, view: 'table', rows: [], measures: [], filterRows: [], period: null, sort: [], limit: DEFAULT_LIMIT, calculated: [] }
}

/** The dimension's result key: `field` or `field@bucket`. */
export function dimKey(field: string, bucket?: Bucket | null): string {
  return bucket ? `${field}@${bucket}` : field
}

/** The measure's result key: `agg(field)` or `count(*)`. */
export function measureKey(field: string | null, agg: Aggregate): string {
  return field ? `${agg}(${field})` : 'count(*)'
}

/** Operators that make sense for a field type (the backend accepts the same sets, plus `between`
 *  which the request compiles to `__from` / `__to`). */
export function opsFor(type: FieldType): FilterOp[] {
  if (type === 'string' || type === 'boolean') return ['contains', 'equals', 'notEquals', 'startsWith', 'endsWith', 'in', 'empty', 'notEmpty']
  return ['equals', 'notEquals', 'between', 'lt', 'le', 'gt', 'ge', 'in', 'empty', 'notEmpty']
}

export function defaultOp(type: FieldType, hasRule: boolean): FilterOp {
  if (hasRule) return 'in'
  return type === 'string' ? 'contains' : type === 'date' || type === 'datetime' ? 'between' : 'equals'
}

/** The editable filter rows → the flat bag the backend takes (`key`, `key__op`, `key__from`,
 *  `key__to`; `in` splits on commas). Rows without a value are skipped, except empty / notEmpty. */
export function compileFilters(rows: FilterRow[]): Record<string, unknown> {
  const bag: Record<string, unknown> = {}
  for (const r of rows) {
    if (r.op === 'empty' || r.op === 'notEmpty') { bag[r.field] = null; bag[`${r.field}__op`] = r.op; continue }
    if (r.op === 'between') {
      if (r.value) bag[`${r.field}__from`] = r.value
      if (r.valueTo) bag[`${r.field}__to`] = r.valueTo
      continue
    }
    if (!r.value) continue
    if (r.op === 'in') {
      bag[r.field] = r.value.split(',').map((s) => s.trim()).filter(Boolean)
      bag[`${r.field}__op`] = 'in'
      continue
    }
    bag[r.field] = r.value
    bag[`${r.field}__op`] = r.op
  }
  return bag
}

function periodWire(def: AnalysisDefinition): { field: string; from?: string; to?: string } | null {
  const p = def.period
  if (!p || !p.field || p.preset === 'all') return null
  const range: DateRange = p.preset === 'custom' ? p.range : rangeForPreset(p.preset)
  const out: { field: string; from?: string; to?: string } = { field: p.field }
  if (range.dateFrom) out.from = range.dateFrom
  if (range.dateTo) out.to = range.dateTo
  return out
}

/** The aggregate request of a definition. */
export function buildAggregateRequest(def: AnalysisDefinition, opts: { debug?: boolean } = {}) {
  return {
    entity: def.entity,
    calculated: def.calculated,
    rows: def.rows.map((r) => (r.bucket ? { field: r.field, bucket: r.bucket } : { field: r.field })),
    measures: def.measures.map((m) => (m.field ? { field: m.field, agg: m.agg } : { agg: 'count' as const })),
    filters: compileFilters(def.filterRows),
    period: periodWire(def),
    sort: def.sort,
    limit: def.limit,
    debug: !!opts.debug,
  }
}

/** The detail request: the rows shelf's fields as columns (the server picks the primary's
 *  visible fields when empty), the same filters and period, a drill when coming from a cell. */
export function buildDetailRequest(def: AnalysisDefinition, opts: { page?: number; pageSize?: number; drill?: DrillCell[]; columns?: string[]; debug?: boolean } = {}) {
  return {
    entity: def.entity,
    calculated: def.calculated,
    columns: opts.columns ?? def.rows.map((r) => r.field),
    filters: compileFilters(def.filterRows),
    period: periodWire(def),
    drill: (opts.drill ?? []).map((c) => (c.bucket ? { field: c.field, bucket: c.bucket, value: c.value } : { field: c.field, value: c.value })),
    sort: def.sort.filter((s) => !s.key.includes('(')),
    page: opts.page ?? 1,
    page_size: opts.pageSize ?? 50,
    debug: !!opts.debug,
  }
}

/** A result cell's drill cells: the row's dimension values, as the server wants them. */
export function drillCellsOf(row: Record<string, unknown>, dims: { field: string; bucket?: Bucket | null }[]): DrillCell[] {
  return dims.map((d) => ({ field: d.field, bucket: d.bucket ?? null, value: row[dimKey(d.field, d.bucket)] ?? null }))
}

/** The model's entities plus a `calc` pseudo-entity holding the definition's calculated fields,
 *  each typed from a previous test when known (number → measure), else a dimension. */
export function withCalculated(model: ExplorerModel, calculated: CalcDef[], types: Record<string, FieldType> = {}): ExplorerEntity[] {
  if (calculated.length === 0) return model.entities
  const fields: ExplorerField[] = calculated.map((c) => {
    const type = types[c.name] ?? 'string'
    return { key: `calc.${c.name}`, entity: 'calc', name: c.name, label: c.label || c.name, type, kind: type === 'number' ? 'measure' : 'dimension', aggregate: type === 'number' ? 'sum' : undefined }
  })
  return [...model.entities, { id: 'calc', label: 'calc', query: '', key: [], cardinality: 'one', fields }]
}

/** Every field by key, over the entities given. */
export function fieldIndex(entities: ExplorerEntity[]): Map<string, ExplorerField> {
  const m = new Map<string, ExplorerField>()
  for (const e of entities) for (const f of e.fields) m.set(f.key, f)
  return m
}

/** The date fields a period can apply to, the primary entity's default date first. */
export function dateFields(entities: ExplorerEntity[], primary: string): ExplorerField[] {
  const out: ExplorerField[] = []
  for (const e of entities) for (const f of e.fields) if (f.type === 'date' || f.type === 'datetime') out.push(f)
  const p = entities.find((e) => e.id === primary)
  const def = p?.default_date ? `${p.id}.${p.default_date.toUpperCase()}` : null
  return def ? [...out.filter((f) => f.key === def), ...out.filter((f) => f.key !== def)] : out
}

/** What a click on a field in the pane does: a dimension joins the rows (a date with a month
 *  bucket), a measure joins the measures with its default aggregate; in detail view everything
 *  is a column (a row). Returns the next definition. */
export function addField(def: AnalysisDefinition, f: ExplorerField): AnalysisDefinition {
  if (def.view !== 'detail' && f.kind === 'measure') {
    if (def.measures.some((m) => m.field === f.key)) return def
    return { ...def, measures: [...def.measures, { field: f.key, agg: f.aggregate ?? 'sum' }] }
  }
  if (def.rows.some((r) => r.field === f.key)) return def
  const bucket: Bucket | null = def.view !== 'detail' && (f.type === 'date' || f.type === 'datetime') ? 'month' : null
  return { ...def, rows: [...def.rows, bucket ? { field: f.key, bucket } : { field: f.key }] }
}

export function addFilter(def: AnalysisDefinition, f: ExplorerField): AnalysisDefinition {
  if (def.filterRows.some((r) => r.field === f.key)) return def
  return { ...def, filterRows: [...def.filterRows, { field: f.key, op: defaultOp(f.type, !!f.rule), value: '' }] }
}

/** Keys used by the definition (to highlight the pane). */
export function usedKeys(def: AnalysisDefinition): Set<string> {
  const s = new Set<string>()
  for (const r of def.rows) s.add(r.field)
  for (const m of def.measures) if (m.field) s.add(m.field)
  for (const f of def.filterRows) s.add(f.field)
  return s
}

export function moveItem<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return list
  const next = list.slice()
  const [it] = next.splice(from, 1)
  next.splice(to, 0, it)
  return next
}
