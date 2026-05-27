// Runtime shapes for the Phase-8 chart widget. The spec lives client-side (per-session
// localStorage, keyed by connector+query) until a later slice adds `config/charts.toml` —
// at that point the same shape will serialise to TOML for shared/persisted charts.

export type ChartType = 'bar' | 'line' | 'pie' | 'area'

/** How to combine rows that share the same x-value. `none` leaves them as-is (the chart will
 *  render every row — useful when the X axis is already unique like a timestamp). */
export type Aggregation = 'sum' | 'avg' | 'count' | 'min' | 'max' | 'none'

/** Which side of the cartesian plot a series binds to. Mirrors backend's YAxisSide; the
 *  default is "left", "right" opts the series onto a second Y axis (the v1 dual-scale pattern). */
export type YAxisSide = 'left' | 'right'

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
  /** Per-series colour overrides, parallel to `y` and indexed positionally. Missing /
   *  empty-string entries fall back to the built-in palette (see SERIES_COLORS in
   *  ChartCanvas). Stored as CSS strings ("#3b82f6" / "rgb(...)" ) so they pass straight
   *  to Recharts. */
  colors?: string[]
  /** Per-series Y-axis assignment, parallel to `y`. Missing entries default to "left";
   *  the chart renders the right-hand YAxis only when at least one entry is "right". */
  yAxis?: YAxisSide[]
}

export const CHART_TYPES: ChartType[] = ['bar', 'line', 'area', 'pie']
export const AGGREGATIONS: Aggregation[] = ['sum', 'avg', 'count', 'min', 'max', 'none']

export function defaultChartSpec(): ChartSpec {
  return { type: 'bar', x: '', y: [], aggregation: 'sum', stacked: false, showGrid: true, sortByX: false }
}

/** The wire shape of a saved chart (GET /api/charts, GET /api/charts/{id} — backend's
 *  `ChartConfig.model_dump`). Pythonic snake_case fields are kept as-is so the runtime can
 *  pass them straight through to the builder UI / dashboard widget. */
export interface ChartConfig {
  id: string
  label: string
  description?: string | null
  connector: string
  query: string
  spec: SavedChartSpec
}

/** The persisted-side spec — same fields as the in-session `ChartSpec` but using the backend's
 *  snake_case wire naming (the Pydantic model defaults to snake_case). Optional flags are
 *  serialised as undefined when at their default, so a saved chart with stock settings is
 *  terse on disk. */
export interface SavedChartSpec {
  type: ChartType
  x: string
  y: string[]
  aggregation: Aggregation
  stacked?: boolean | null
  show_legend?: boolean | null
  show_grid?: boolean | null
  sort_by_x?: boolean | null
  /** Per-series colour overrides — parallel to `y`. Empty / omitted entries → palette. */
  colors?: string[]
  /** Per-series Y-axis assignment — parallel to `y`. Backend's `y_axis` field. */
  y_axis?: YAxisSide[]
}

/** Translate the runtime ChartSpec (camelCase, with seeded defaults) into the persisted form
 *  (snake_case, defaults stripped so the file stays diff-friendly). Used by SaveChartModal. */
export function toSavedSpec(spec: ChartSpec): SavedChartSpec {
  const out: SavedChartSpec = { type: spec.type, x: spec.x, y: spec.y, aggregation: spec.aggregation }
  if (spec.stacked != null) out.stacked = spec.stacked
  if (spec.showLegend != null) out.show_legend = spec.showLegend
  if (spec.showGrid != null) out.show_grid = spec.showGrid
  if (spec.sortByX != null) out.sort_by_x = spec.sortByX
  // Trim trailing empty / default entries before saving so a chart that only colours
  // the first of three series doesn't carry two redundant '' entries in TOML. Same for
  // y_axis — an all-"left" tail collapses to nothing, keeping the file diff-friendly.
  const colors = trimTrailing(spec.colors ?? [], (c) => !c)
  if (colors.length) out.colors = colors
  const yAxis = trimTrailing(spec.yAxis ?? [], (s) => s === 'left')
  if (yAxis.length) out.y_axis = yAxis
  return out
}

/** Translate the persisted SavedChartSpec back into the runtime ChartSpec — inverse of
 *  ``toSavedSpec``. Used by ChartView when ``screen.chart_id`` points at a saved chart and
 *  the TableView pre-fills the Chart tab from charts.toml instead of localStorage. */
export function fromSavedSpec(saved: SavedChartSpec): ChartSpec {
  return {
    type: saved.type,
    x: saved.x,
    y: saved.y,
    aggregation: saved.aggregation,
    stacked: saved.stacked ?? undefined,
    showLegend: saved.show_legend ?? undefined,
    showGrid: saved.show_grid ?? undefined,
    sortByX: saved.sort_by_x ?? undefined,
    colors: saved.colors ?? undefined,
    yAxis: saved.y_axis ?? undefined,
  }
}

/** Drop trailing entries that match a predicate (treated as "default" — not worth saving). */
function trimTrailing<T>(arr: T[], isDefault: (v: T) => boolean): T[] {
  let end = arr.length
  while (end > 0 && isDefault(arr[end - 1])) end -= 1
  return arr.slice(0, end)
}
