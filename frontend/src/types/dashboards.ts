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

export interface Dashboard {
  id: string
  label: string
  description?: string | null
  widgets: DashboardWidget[]
}

export interface DashboardsResponse { dashboards: Dashboard[] }
