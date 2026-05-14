// Chart widget for the dashboard grid — fetches its own (connector, query) result, then renders
// it via the shared `ChartCanvas` (no editable spec bar in this slice; the dashboard's spec is
// the source of truth). A wider slice may add an "Open in TableView" affordance.
//
// The chart's pixel height is computed from the widget's `row_span` (320px per row, less a small
// chrome allowance for the title + the canvas's own border/padding). Passing a fixed height
// keeps Recharts' ResponsiveContainer happy even when the widget's parent briefly measures 0 ×
// 0 during the loading → loaded transition or when sitting in a hidden tab.
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { api, ApiError } from '../../api/client'
import { Banner, SpinnerRing } from '../../common'
import { ChartCanvas } from '../TableView/ChartCanvas'
import type { QueryResult } from '../../types/connectors'
import type { ChartWidgetWire } from '../../types/dashboards'
import type { ChartSpec } from '../../types/charts'
import { colors, glass, radius, shadow } from '../../theme'

// Mirrors ChartCanvas's Frame so the loading placeholder looks identical to the loaded chart —
// no visible flash when data lands. Glass surface + shadow + inset highlight = same depth.
const Placeholder = styled.div<{ $h: number }>`
  height: ${({ $h }) => $h}px; min-width: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  background: ${colors.bg.card};
  ${glass.surface}
  box-shadow: ${shadow.sm}, inset 0 1px 0 rgba(255, 255, 255, 0.08);
  display: flex; align-items: center; justify-content: center;
`

const ROW_PX = 180          // matches DashboardView's `grid-auto-rows`
const FRAME_CHROME = 40     // title + gap + the canvas's own border/padding

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

  const chartHeight = Math.max(180, widget.row_span * ROW_PX - FRAME_CHROME)

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!result) return (
    <Placeholder $h={chartHeight}><SpinnerRing size={20} thickness={2} /></Placeholder>
  )
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
  return <ChartCanvas result={result} spec={runtimeSpec} connector={widget.connector} height={chartHeight} />
}
