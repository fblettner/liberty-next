// Runtime shapes for the Phase-8 chart widget. The spec lives client-side (per-session
// localStorage, keyed by connector+query) until a later slice adds `config/charts.toml` —
// at that point the same shape will serialise to TOML for shared/persisted charts.

export type ChartType = 'bar' | 'line' | 'pie' | 'area'

/** How to combine rows that share the same x-value. `none` leaves them as-is (the chart will
 *  render every row — useful when the X axis is already unique like a timestamp). */
export type Aggregation = 'sum' | 'avg' | 'count' | 'min' | 'max' | 'none'

export interface ChartSpec {
  type: ChartType
  /** Column name to group/categorise by — the x-axis (or slice label for pie). */
  x: string
  /** Column names for the series — the y-axis (or slice value for pie). Empty → no chart. */
  y: string[]
  aggregation: Aggregation
  /** Bar / area only — stack the series rather than place side-by-side. */
  stacked?: boolean
  /** Show the legend (default true when >1 series, false for a single one). */
  showLegend?: boolean
  /** Show the cartesian grid (default true; pie ignores). */
  showGrid?: boolean
  /** Sort categories alphabetically by x (default false → input order). */
  sortByX?: boolean
}

export const CHART_TYPES: ChartType[] = ['bar', 'line', 'area', 'pie']
export const AGGREGATIONS: Aggregation[] = ['sum', 'avg', 'count', 'min', 'max', 'none']

export function defaultChartSpec(): ChartSpec {
  return { type: 'bar', x: '', y: [], aggregation: 'sum', stacked: false, showGrid: true, sortByX: false }
}
