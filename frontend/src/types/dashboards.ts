// Runtime wire shapes for the Phase-8 dashboards API (liberty/web/dashboards.py).
//
// The backend resolves chart-widget references server-side, so every chart widget on the wire
// carries an inline `connector` + `query` + `spec` — the frontend doesn't need a second fetch
// per chart reference. `chart_id` is echoed back for the (future) "open the saved chart" affordance.
import type { Aggregation, SavedChartSpec } from './charts'

export interface ChartWidgetWire {
  type: 'chart'
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
  label?: string | null
  col_span: number
  row_span: number
  connector: string
  query: string
  column: string
  aggregation: Aggregation
  format?: string
}

export type DashboardWidget = ChartWidgetWire | KpiWidgetWire

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

export interface Dashboard {
  id: string
  label: string
  description?: string | null
  /** Empty / absent → no filter bar rendered. The backend only includes filters the caller can
   *  read (the options query gates on `sql:<conn>:<query>`). */
  filters?: DashboardFilterWire[]
  widgets: DashboardWidget[]
}

export interface DashboardsResponse { dashboards: Dashboard[] }
