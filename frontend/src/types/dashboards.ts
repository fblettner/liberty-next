// Runtime wire shapes for the Phase-8 dashboards API (liberty/web/dashboards.py).
//
// The backend resolves chart-widget references server-side, so every chart widget on the wire
// carries an inline `connector` + `query` + `spec` — the frontend doesn't need a second fetch
// per chart reference. `chart_id` is echoed back for the (future) "open the saved chart" affordance.
import type { Aggregation, SavedChartSpec } from './charts'

export interface ChartWidgetWire {
  type: 'chart'
  /** A subtitle under the title. */
  description?: string | null
  /** Echoed back when the widget was a reference (`chart = "<id>"` in TOML); absent for inline widgets. */
  chart_id?: string
  /** Resolved label — inherits the saved chart's label when the widget didn't override it. */
  label?: string | null
  col_span: number
  row_span: number
  connector: string
  query: string
  spec: SavedChartSpec
}

export interface KpiWidgetWire {
  type: 'kpi'
  /** A subtitle under the title. */
  description?: string | null
  label?: string | null
  col_span: number
  row_span: number
  connector: string
  query: string
  column: string
  aggregation: Aggregation
  format?: string
  /** With a dashboard period: the query also runs on the period before; the card shows the delta and the previous value. */
  compare_previous?: boolean
}

export interface TableWidgetWire {
  type: 'table'
  /** A subtitle under the title. */
  description?: string | null
  label?: string | null
  col_span: number
  row_span: number
  connector: string
  query: string
  /** Optional subset/order of result columns to show (by name). Empty → every non-hidden column. */
  columns: string[]
  /** Client-side row cap. Absent → show whatever the query returned. */
  max_rows?: number
}

export type ValueFormat = 'number' | 'integer' | 'currency' | 'percent'

/** A figure card from the query's first row: the value column formatted, a sub line templated
 *  over the row (`{COLUMN}` / `{COLUMN:format}`). */
export interface StatWidgetWire {
  type: 'stat'
  label?: string | null
  description?: string | null
  col_span: number
  row_span: number
  connector: string
  query: string
  value_column: string
  format?: ValueFormat
  unit?: string
  sub?: string
}

/** A decision / ranked list: one row per query row with a title, a subtitle, a figure, a count,
 *  a tone dot and a tag pill. */
export interface ListWidgetWire {
  type: 'list'
  label?: string | null
  description?: string | null
  col_span: number
  row_span: number
  connector: string
  query: string
  title_column: string
  subtitle_column?: string
  value_column?: string
  format?: ValueFormat
  unit?: string
  count_column?: string
  count_label?: string
  tone_column?: string
  tag_column?: string
  max_rows: number
}

/** A ranked list with a proportion bar per row. */
export interface BarsWidgetWire {
  type: 'bars'
  label?: string | null
  description?: string | null
  col_span: number
  row_span: number
  connector: string
  query: string
  label_column: string
  sub_label_column?: string
  value_column: string
  format?: ValueFormat
  unit?: string
  share: 'total' | 'max'
  note?: string
  max_rows: number
}

/** A block of Markdown — an overview's introduction. */
export interface TextWidgetWire {
  type: 'text'
  label?: string | null
  description?: string | null
  col_span: number
  row_span: number
  markdown: string
}

export interface LinkWire { label?: string | null; description?: string | null; icon?: string | null; menu?: string | null; url?: string | null }

/** A card of links: menu items of the dashboard's app (resolved in the user's menu tree) or URLs. */
export interface LinksWidgetWire {
  type: 'links'
  label?: string | null
  description?: string | null
  col_span: number
  row_span: number
  /** The dashboard's app: where `menu` links are looked up. */
  app: string
  columns: number
  links: LinkWire[]
}

export type DashboardWidget = ChartWidgetWire | KpiWidgetWire | TableWidgetWire | StatWidgetWire | ListWidgetWire | BarsWidgetWire | TextWidgetWire | LinksWidgetWire

export interface DashboardFilterOptionsWire {
  connector: string
  query: string
  value_column: string
  label_column: string
}

/** A dashboard-level filter — appears as a dropdown above the widget grid. When the user picks a
 *  value, each widget whose query has a column with `dd === dictionary_key` refetches with that
 *  column bound to the chosen value. Widgets without a matching column ignore the filter. */
export interface DashboardFilterWire {
  id: string
  label: string
  dictionary_key: string
  default_value?: string
  options: DashboardFilterOptionsWire
}

export interface PeriodBasisWire {
  value: string
  label: string
  column?: string
  dd?: string
}

/** The dashboard's period (a date range in the bar): every widget gets PERIOD_FROM / PERIOD_TO
 *  (+ PREV_FROM / PREV_TO, PERIOD_BASIS) as binds; a widget whose screen carries the named
 *  column / dd is also range-wrapped server-side on it. */
export interface DashboardPeriodWire {
  column?: string
  dd?: string
  default: string
  basis?: PeriodBasisWire[]
}

export interface Dashboard {
  id: string
  label: string
  description?: string | null
  period?: DashboardPeriodWire
  /** Empty / absent → no filter bar rendered. The backend only includes filters the caller can
   *  read (the options query gates on `sql:<conn>:<query>`). */
  filters?: DashboardFilterWire[]
  widgets: DashboardWidget[]
}

export interface DashboardsResponse { dashboards: Dashboard[] }
