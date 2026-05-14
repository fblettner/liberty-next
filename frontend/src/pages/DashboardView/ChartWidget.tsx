// Chart widget for the dashboard grid — fetches its own (connector, query) result, then renders
// it via the shared `ChartCanvas` (no editable spec bar in this slice; the dashboard's spec is
// the source of truth). A wider slice may add an "Open in TableView" affordance.
import { useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import { Banner, Centered } from '../../common'
import { ChartCanvas } from '../TableView/ChartCanvas'
import type { QueryResult } from '../../types/connectors'
import type { ChartWidgetWire } from '../../types/dashboards'
import type { ChartSpec } from '../../types/charts'

export function ChartWidget({ widget }: { widget: ChartWidgetWire }) {
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
    api
      .get<QueryResult>(`/api/sql/${encodeURIComponent(widget.connector)}/${encodeURIComponent(widget.query)}`)
      .then((r) => { if (!cancelled) setResult(r) })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [widget.connector, widget.query])

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!result) return <Centered />
  // The wire `spec` uses snake_case (it came from the backend); ChartCanvas reads the runtime
  // camelCase shape. Map the optional flags through so the canvas's defaults still apply.
  const runtimeSpec: ChartSpec = {
    type: widget.spec.type,
    x: widget.spec.x,
    y: widget.spec.y,
    aggregation: widget.spec.aggregation,
    ...(widget.spec.stacked != null ? { stacked: widget.spec.stacked } : {}),
    ...(widget.spec.show_legend != null ? { showLegend: widget.spec.show_legend } : {}),
    ...(widget.spec.show_grid != null ? { showGrid: widget.spec.show_grid } : {}),
    ...(widget.spec.sort_by_x != null ? { sortByX: widget.spec.sort_by_x } : {}),
  }
  return <ChartCanvas result={result} spec={runtimeSpec} connector={widget.connector} />
}
