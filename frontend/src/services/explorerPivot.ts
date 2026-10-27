// Pure shaping of an aggregate result (no React): the pivot tree (row dimensions as a hierarchy,
// one dimension as columns, subtotals for additive measures only, share of parent / total), the
// chart series (x = first dimension, series from a second dimension or the measures, Top 10 +
// Others, chronological for dates) and the KPI totals. Tested in explorerPivot.test.ts.
import type { ChartSpec, ChartType } from '../types/charts'
import type { QueryResult } from '../types/connectors'
import type { Aggregate, AggregateResult, ResultColumn } from '../types/explorer'

export const isAdditive = (agg: Aggregate | undefined) => agg === 'sum' || agg === 'count'

export interface PivotNode {
  /** the dimension value at this level (null for the root) */
  value: unknown
  level: number
  children: PivotNode[]
  /** per column value (the pivot column's values; '' when there is no pivot column) */
  cells: Record<string, number | null>
  total: number | null
  /** the row's own dimension values, root → this node (for a drill) */
  path: unknown[]
}

export interface Pivot {
  columns: string[]            // the pivot column's values, sorted
  root: PivotNode
  additive: boolean
}

const colKey = (v: unknown) => (v === null || v === undefined ? '' : String(v))

function add(a: number | null, b: number | null): number | null {
  return a === null ? b : b === null ? a : a + b
}

/** The pivot: rows grouped level by level on `rowKeys`, `colKey` (a dimension key, or null)
 *  spread as columns, `measureKey` summed into the cells. Non-additive measures keep leaf values
 *  only (a subtotal of averages is meaningless). */
export function buildPivot(rows: Record<string, unknown>[], rowKeys: string[], pivotKey: string | null, measureKey: string, additive: boolean): Pivot {
  const columns = new Set<string>()
  const root: PivotNode = { value: null, level: 0, children: [], cells: {}, total: null, path: [] }
  for (const r of rows) {
    const c = pivotKey ? colKey(r[pivotKey]) : ''
    columns.add(c)
    const v = r[measureKey]
    const num = typeof v === 'number' ? v : v === null || v === undefined ? null : Number(v)
    let node = root
    if (additive) { node.cells[c] = add(node.cells[c] ?? null, num); node.total = add(node.total, num) }
    rowKeys.forEach((k, i) => {
      const val = r[k] ?? null
      let child = node.children.find((x) => x.value === val)
      if (!child) {
        child = { value: val, level: i + 1, children: [], cells: {}, total: null, path: [...node.path, val] }
        node.children.push(child)
      }
      node = child
      const leaf = i === rowKeys.length - 1
      if (additive || leaf) { node.cells[c] = add(node.cells[c] ?? null, num); node.total = add(node.total, num) }
    })
  }
  const cols = [...columns].sort((a, b) => (a === '' ? 1 : b === '' ? -1 : a < b ? -1 : a > b ? 1 : 0))
  return { columns: cols, root, additive }
}

/** The share of a cell: of its parent's same cell, or of the grand total of that column. */
export function share(cell: number | null, parent: number | null, total: number | null, mode: 'parent' | 'total'): number | null {
  const base = mode === 'parent' ? parent : total
  if (cell === null || !base) return null
  return cell / base
}

export function flattenPivot(root: PivotNode, collapsed: Set<string>): { node: PivotNode; parent: PivotNode; id: string }[] {
  const out: { node: PivotNode; parent: PivotNode; id: string }[] = []
  const walk = (n: PivotNode, prefix: string) => {
    for (const c of n.children) {
      const id = `${prefix}/${colKey(c.value)}`
      out.push({ node: c, parent: n, id })
      if (!collapsed.has(id)) walk(c, id)
    }
  }
  walk(root, '')
  return out
}

// ── chart ────────────────────────────────────────────────────────────────────

const TOP = 10
export const OTHERS = '__others__'

export interface Shaped { result: QueryResult; spec: ChartSpec; series: string[]; othersLabel: boolean }

/** The chart's data from an aggregate result: x = the first dimension (not the series one),
 *  series = the pivot column / second dimension when the measure is additive, else the
 *  measures; the top 10 series by value plus "Others"; x in chronological order for a bucketed
 *  date, by value otherwise; extra dimensions summed. */
export function shapeChart(
  res: AggregateResult, opts: { type: ChartType; measure?: string | null; seriesDim?: string | null; othersLabel: string },
): Shaped | null {
  const dims = res.columns.filter((c) => c.kind === 'dimension')
  const measures = res.columns.filter((c) => c.kind === 'measure')
  if (measures.length === 0) return null
  const m = measures.find((c) => c.key === opts.measure) ?? measures[0]
  const seriesCol = opts.seriesDim ? dims.find((d) => d.key === opts.seriesDim) ?? null : dims.length >= 2 ? dims[1] : null
  const xCol = dims.find((d) => d.key !== seriesCol?.key) ?? null
  const useSeries = !!seriesCol && isAdditive(m.aggregate)
  const single = !xCol
  // x → series → value
  const table = new Map<string, Map<string, number>>()
  const seriesTotal = new Map<string, number>()
  const xTotal = new Map<string, number>()
  const label = (v: unknown) => (v === null || v === undefined ? '' : String(v))
  for (const r of res.rows) {
    const x = single ? '' : label(r[xCol!.key])
    const vals: [string, number | null][] = useSeries
      ? [[label(r[seriesCol!.key]), num(r[m.key])]]
      : measures.map((mm) => [mm.key, num(r[mm.key])])
    const row = table.get(x) ?? new Map<string, number>()
    for (const [s, v] of vals) {
      if (v === null) continue
      row.set(s, (row.get(s) ?? 0) + v)
      seriesTotal.set(s, (seriesTotal.get(s) ?? 0) + v)
      xTotal.set(x, (xTotal.get(x) ?? 0) + v)
    }
    table.set(x, row)
  }
  let series = [...seriesTotal.keys()]
  let others = false
  if (useSeries && series.length > TOP) {
    series = series.sort((a, b) => (seriesTotal.get(b) ?? 0) - (seriesTotal.get(a) ?? 0))
    const keep = new Set(series.slice(0, TOP))
    others = true
    for (const row of table.values()) {
      let o = 0
      for (const [s, v] of [...row.entries()]) if (!keep.has(s)) { o += v; row.delete(s) }
      if (o) row.set(OTHERS, o)
    }
    series = [...keep, OTHERS]
  }
  let xs = [...table.keys()]
  if (xCol?.bucket || xCol?.type === 'date' || xCol?.type === 'datetime') xs.sort()
  else xs.sort((a, b) => (xTotal.get(b) ?? 0) - (xTotal.get(a) ?? 0))
  if (opts.type === 'pie' && !useSeries && xs.length > TOP) {
    // a pie over too many slices: keep the top 10 and fold the rest
    const keep = xs.slice(0, TOP)
    const rest = xs.slice(TOP)
    const folded = new Map<string, number>()
    for (const x of rest) for (const [s, v] of table.get(x)!.entries()) folded.set(s, (folded.get(s) ?? 0) + v)
    table.set(OTHERS, folded)
    xs = [...keep, OTHERS]
    others = true
  }
  const xName = xCol ? xCol.key : 'x'
  const rows = xs.map((x) => {
    const row: Record<string, unknown> = { [xName]: x === OTHERS ? opts.othersLabel : x }
    const vals = table.get(x) ?? new Map()
    for (const s of series) row[s === OTHERS ? opts.othersLabel : s] = vals.get(s) ?? 0
    return row
  })
  const names = series.map((s) => (s === OTHERS ? opts.othersLabel : s))
  const columns = [
    { name: xName, type: null, ...(xCol?.rule && !xCol.bucket ? { rule: xCol.rule } : {}) },
    ...names.map((s) => ({ name: s, type: 'number' })),
  ]
  const result: QueryResult = { connector: '', query: 'explorer', statement_type: 'SELECT', columns, rows, row_count: rows.length, rowcount: -1, truncated: false, duration_ms: 0 }
  const spec: ChartSpec = { type: opts.type, x: xName, y: names, aggregation: 'none' }
  return { result, spec, series: names, othersLabel: others }
}

function num(v: unknown): number | null {
  if (v === null || v === undefined) return null
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isFinite(n) ? n : null
}

/** One total per measure for the KPI cards: a sum for additive measures, the min / max for
 *  those, the average of the rows' averages otherwise. */
export function kpiTotals(res: AggregateResult): { column: ResultColumn; value: number | null }[] {
  return res.columns.filter((c) => c.kind === 'measure').map((c) => {
    const vals = res.rows.map((r) => num(r[c.key])).filter((v): v is number => v !== null)
    if (vals.length === 0) return { column: c, value: null }
    const value = c.aggregate === 'min' ? Math.min(...vals) : c.aggregate === 'max' ? Math.max(...vals)
      : c.aggregate === 'avg' ? vals.reduce((a, b) => a + b, 0) / vals.length : vals.reduce((a, b) => a + b, 0)
    return { column: c, value }
  })
}

/** The pivot as rows of cells for an Excel sheet: one column per row level, the pivot columns,
 *  the total; group rows carry their subtotal. */
export function pivotToSheet(p: Pivot, levels: string[], valueLabel: string): unknown[][] {
  const head = [...levels, ...p.columns.map((c) => c || '—'), ...(p.columns.length > 1 ? [valueLabel] : [])]
  const out: unknown[][] = [head]
  const walk = (n: PivotNode) => {
    for (const c of n.children) {
      const cells = [...Array(levels.length)].map((_, i) => (i === c.level - 1 ? (c.value ?? '') : ''))
      out.push([...cells, ...p.columns.map((k) => c.cells[k] ?? null), ...(p.columns.length > 1 ? [c.total] : [])])
      walk(c)
    }
  }
  walk(p.root)
  return out
}
