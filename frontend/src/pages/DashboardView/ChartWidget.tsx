// Chart widget for the dashboard grid — fetches its own (connector, query) result, then renders
// it via the shared `ChartCanvas` (no editable spec bar in this slice; the dashboard's spec is
// the source of truth). A wider slice may add an "Open in TableView" affordance.
//
// The chart's pixel height is computed from the widget's `row_span` (320px per row, less a small
// chrome allowance for the title + the canvas's own border/padding). Passing a fixed height
// keeps Recharts' ResponsiveContainer happy even when the widget's parent briefly measures 0 ×
// 0 during the loading → loaded transition or when sitting in a hidden tab.
import { useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { api, ApiError } from '../../api/client'
import { useBranding } from '../../branding/BrandingContext'
import { cached } from '../../services/widgetCache'
import { Banner, SpinnerRing } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { ChartCanvas } from '../TableView/ChartCanvas'
import type { QueryResult } from '../../types/connectors'
import type { ChartWidgetWire, DashboardFilterWire, DashboardPeriodWire } from '../../types/dashboards'
import type { ChartSpec } from '../../types/charts'
import { buildWidgetFilterParams, buildWidgetPeriodParams, type PeriodSelection } from './widgetFilters'
import { Sub, Title } from './cardParts'
import { colors, glass, radius, shadow } from '../../theme'

// Mirrors ChartCanvas's Frame so the loading placeholder looks identical to the loaded chart —
// no visible flash when data lands. Glass surface + shadow + inset highlight = same depth.
const Placeholder = styled.div<{ $h: number }>`
  height: ${({ $h }) => $h}px; min-width: 0; padding: 14px; flex-direction: column;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  background: ${colors.bg.card};
  ${glass.surface}
  box-shadow: ${shadow.sm}, inset 0 1px 0 rgba(255, 255, 255, 0.08);
  display: flex; align-items: center; justify-content: center;
`

const ROW_PX = 150          // matches DashboardView's `grid-auto-rows`
const FRAME_CHROME = 50     // the canvas's own border/padding + legend; the title inside adds HEADER_PX
const HEADER_PX = 44        // the title + description inside the card

export interface ChartWidgetProps {
  widget: ChartWidgetWire
  /** Dashboard filter defs (the per-dashboard `filters` array). Empty → no filter bar; this
   *  widget just fetches unfiltered. */
  filters: DashboardFilterWire[]
  /** Current filter selections (`{[filter.id]: pickedValue}`). Empty values = "All", no bind. */
  filterValues: Record<string, string>
  period?: DashboardPeriodWire
  selection?: PeriodSelection
  /** Bumped by the bar's Refresh: refetch past the cache. */
  refreshKey?: number
}

export function ChartWidget({ widget, filters, filterValues, period, selection, refreshKey = 0 }: ChartWidgetProps) {
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { findScreen } = useWorkspace()
  const { ui } = useBranding()
  const lastRefresh = useRef(0)

  // Resolve dashboard filters → URL params for this widget's specific query. Memoized so we
  // don't refetch on every render — only when the actual bound params change. The screen
  // lookup walks an indexed Map; cheap enough to ignore the identity of `findScreen` (it
  // stabilises on `screens` only via WorkspaceContext's useCallback).
  const filterParams = useMemo(
    () => ({
      ...buildWidgetFilterParams(widget.connector, widget.query, filters, filterValues, findScreen),
      ...buildWidgetPeriodParams(widget.connector, widget.query, period, selection, findScreen),
    }),
    [widget.connector, widget.query, filters, filterValues, period, selection, findScreen],
  )
  // Stable key for the useEffect deps — JSON-stringify the params so object identity changes
  // don't trigger a refetch unless the actual values changed.
  const filterParamsKey = JSON.stringify(filterParams)

  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
    const qs = new URLSearchParams(filterParams).toString()
    const url = `/api/sql/${encodeURIComponent(widget.connector)}/${encodeURIComponent(widget.query)}${qs ? `?${qs}` : ''}`
    cached(url, ui.dashboard_cache_seconds, () => api.get<QueryResult>(url), refreshKey > 0 && refreshKey !== lastRefresh.current)
      .finally(() => { lastRefresh.current = refreshKey })
      .then((r) => { if (!cancelled) setResult(r) })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
    // filterParamsKey covers filterParams; eslint can't follow that, hence the disable.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.connector, widget.query, filterParamsKey, refreshKey])

  const chartHeight = Math.max(180, widget.row_span * ROW_PX - FRAME_CHROME - (widget.label ? (widget.description ? HEADER_PX : HEADER_PX - 18) : 0))
  // the title and description inside the card, like the stat / list / bars cards
  const header = widget.label ? (
    <div style={{ marginBottom: 8, alignSelf: 'stretch' }}>
      <Title>{widget.label}</Title>
      {widget.description && <Sub>{widget.description}</Sub>}
    </div>
  ) : null

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!result) return (
    <Placeholder $h={chartHeight + FRAME_CHROME}>{header}<div style={{ flex: 1, display: 'flex', alignItems: 'center' }}><SpinnerRing size={20} thickness={2} /></div></Placeholder>
  )
  // The wire `spec` uses snake_case (it came from the backend); ChartCanvas reads the runtime
  // camelCase shape. Map every optional field through — missing ``colors`` / ``y_axis`` here is
  // what made per-series colour overrides disappear in dashboards (the chart fell back to the
  // default palette even when the saved spec explicitly set bar colours).
  const runtimeSpec: ChartSpec = {
    type: widget.spec.type,
    x: widget.spec.x,
    y: widget.spec.y,
    aggregation: widget.spec.aggregation,
    ...(widget.spec.series ? { series: widget.spec.series } : {}),
    ...(widget.spec.stacked != null ? { stacked: widget.spec.stacked } : {}),
    ...(widget.spec.show_legend != null ? { showLegend: widget.spec.show_legend } : {}),
    ...(widget.spec.show_grid != null ? { showGrid: widget.spec.show_grid } : {}),
    ...(widget.spec.sort_by_x != null ? { sortByX: widget.spec.sort_by_x } : {}),
    ...(widget.spec.colors ? { colors: widget.spec.colors } : {}),
    ...(widget.spec.y_axis ? { yAxis: widget.spec.y_axis } : {}),
  }
  return <ChartCanvas result={result} spec={runtimeSpec} connector={widget.connector} height={chartHeight} header={header} />
}
