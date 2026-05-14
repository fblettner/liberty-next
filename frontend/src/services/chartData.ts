// Transform a QueryResult into chart-ready rows for Recharts. `buildChartData(result, spec)`
// groups rows by the X column, applies the spec's aggregation per Y column, and emits a flat
// list ready to hand to <BarChart data={data}> / <LineChart> / etc. Each datum is
// `{ x: <category label>, <yCol>: <number>, ... }` — Recharts then keys series by Y column name.
//
// Number-ness is best-effort: a cell that parses to a finite number contributes; everything else
// counts as missing. For categorical X values we keep the original ordering (first occurrence),
// unless `spec.sortByX` is on (alphabetic). Aggregations: `sum` / `avg` / `min` / `max` operate on
// the y cells; `count` ignores the y value and just counts rows with that x; `none` keeps every
// row as its own datum (suitable for time-series where x is already unique).
import type { Column, QueryResult } from '../types/connectors'
import type { Aggregation, ChartSpec } from '../types/charts'

export interface ChartDatum {
  /** Display label for the X axis (or slice for pie). Always a string after build. */
  x: string
  /** One numeric column per series — keyed by the original Y column name. */
  [series: string]: string | number | null
}

/** Pull a number out of a cell, or null when we can't (string, object, NaN, …). Booleans count as 1/0. */
export function toNumber(v: unknown): number | null {
  if (v === null || v === undefined) return null
  if (typeof v === 'number') return Number.isFinite(v) ? v : null
  if (typeof v === 'boolean') return v ? 1 : 0
  if (typeof v === 'string') {
    const s = v.trim()
    if (!s) return null
    const n = Number(s)
    return Number.isFinite(n) ? n : null
  }
  return null
}

/** A best-effort numeric-column heuristic — used by ChartView's default Y picker. We honour the
 *  column's `type` hint when present (the backend exposes Postgres OIDs / SQLAlchemy type names);
 *  failing that we don't try to sniff the rows here (callers can fall through). */
const NUMERIC_TYPE_RE = /\b(int|integer|bigint|smallint|tinyint|float|double|real|decimal|numeric|number)\b/i

export function isNumericColumn(col: Column): boolean {
  return !!col.type && NUMERIC_TYPE_RE.test(col.type)
}

/** Resolve a spec column name (which an operator might author in any case — uppercase to match the
 *  TOML / dictionary conventions, lowercase to match what they saw in a Postgres CLI, …) to the
 *  actual case used in the result rows. The backend keeps the *discovered* case from
 *  `cursor.description` — Postgres lowercases unquoted identifiers, Oracle uppercases — so the row
 *  dict's keys can differ from the chart spec's. Returns `name` unchanged when nothing matches
 *  (the row lookup will then just return undefined, same as before). */
export function resolveColumnName(result: QueryResult, name: string): string {
  if (!name) return name
  // Exact match short-circuits — by far the common case.
  for (const c of result.columns) {
    if (c.name === name) return name
  }
  const low = name.toLowerCase()
  for (const c of result.columns) {
    if (c.name.toLowerCase() === low) return c.name
  }
  return name
}

function applyAggregation(values: (number | null)[], agg: Aggregation): number | null {
  const nums = values.filter((v): v is number => v !== null)
  if (agg === 'count') return values.length // count *rows*, not just non-null values
  if (!nums.length) return null
  switch (agg) {
    case 'sum':
      return nums.reduce((a, b) => a + b, 0)
    case 'avg':
      return nums.reduce((a, b) => a + b, 0) / nums.length
    case 'min':
      return Math.min(...nums)
    case 'max':
      return Math.max(...nums)
    case 'none':
      return nums[0] // a `none` group should only have one row anyway
  }
}

export function buildChartData(result: QueryResult, spec: ChartSpec): ChartDatum[] {
  if (!spec.x || spec.y.length === 0) return []
  // Resolve spec column names to the actual case used in result rows once, here. Postgres
  // returns lowercase keys for unquoted identifiers; if the operator authored the spec with
  // uppercase column names (matching v1's conventions), a direct `r[spec.x]` lookup would miss.
  const xKey = resolveColumnName(result, spec.x)
  const yKeys = spec.y.map((y) => resolveColumnName(result, y))
  // Track each datum's Y values under both the canonical (spec.y) name and the resolved one,
  // since Recharts indexes by the spec's literal `y` (`<Bar dataKey={y}>`).
  // No grouping → one datum per row, in input order.
  if (spec.aggregation === 'none') {
    return result.rows.map((r) => {
      const datum: ChartDatum = { x: formatCategory(r[xKey]) }
      spec.y.forEach((y, i) => { datum[y] = toNumber(r[yKeys[i]]) })
      return datum
    })
  }
  // Otherwise group rows by their X value and aggregate each Y across the group.
  const groups = new Map<string, Record<string, unknown>[]>()
  const order: string[] = []  // preserve first-occurrence order; `sortByX` re-sorts at the end
  for (const r of result.rows) {
    const x = formatCategory(r[xKey])
    let list = groups.get(x)
    if (!list) {
      list = []
      groups.set(x, list)
      order.push(x)
    }
    list.push(r)
  }
  const keys = spec.sortByX ? [...order].sort() : order
  return keys.map((x) => {
    const list = groups.get(x) ?? []
    const datum: ChartDatum = { x }
    spec.y.forEach((y, i) => {
      const yKey = yKeys[i]
      const values = list.map((r) => toNumber(r[yKey]))
      datum[y] = applyAggregation(values, spec.aggregation)
    })
    return datum
  })
}

/** Render an arbitrary cell as a category label. Null / undefined → "(empty)" so the chart still
 *  shows the bucket rather than silently dropping rows. Objects / arrays get JSON-stringified. */
function formatCategory(v: unknown): string {
  if (v === null || v === undefined) return '(empty)'
  if (typeof v === 'string') return v
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try { return JSON.stringify(v) } catch { return String(v) }
}
