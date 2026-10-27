// One fetch per widget, bound like every other widget: the filter picks through the screen's dd
// map (or a dictionary bind), the period's binds; cached per URL for the dashboard's cache window,
// refetched past the cache on the bar's Refresh.
import { useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError } from '../../api/client'
import { useBranding } from '../../branding/BrandingContext'
import { cached } from '../../services/widgetCache'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import type { QueryResult } from '../../types/connectors'
import type { DashboardFilterWire, DashboardPeriodWire } from '../../types/dashboards'
import { buildWidgetFilterParams, buildWidgetPeriodParams, type PeriodSelection } from './widgetFilters'

export interface WidgetCommon {
  filters: DashboardFilterWire[]
  filterValues: Record<string, string>
  period?: DashboardPeriodWire
  selection?: PeriodSelection
  refreshKey?: number
}

export function useWidgetResult(connector: string, query: string, { filters, filterValues, period, selection, refreshKey = 0 }: WidgetCommon): { result: QueryResult | null; error: string | null } {
  const { findScreen } = useWorkspace()
  const { ui } = useBranding()
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const lastRefresh = useRef(0)
  const params = useMemo(
    () => ({ ...buildWidgetFilterParams(connector, query, filters, filterValues, findScreen), ...buildWidgetPeriodParams(connector, query, period, selection, findScreen) }),
    [connector, query, filters, filterValues, period, selection, findScreen],
  )
  const paramsKey = JSON.stringify(params)
  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
    const qs = new URLSearchParams(params).toString()
    const url = `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}${qs ? `?${qs}` : ''}`
    cached(url, ui.dashboard_cache_seconds, () => api.get<QueryResult>(url), refreshKey > 0 && refreshKey !== lastRefresh.current)
      .finally(() => { lastRefresh.current = refreshKey })
      .then((r) => { if (!cancelled) setResult(r) })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- paramsKey covers params
  }, [connector, query, paramsKey, refreshKey])
  return { result, error }
}
