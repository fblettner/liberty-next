// KPI widget for the dashboard grid — runs the widget's query, applies the configured
// aggregation to one column, displays the result as a big number. Suitable for "Active
// users · 686" style displays. For `count`, the row count is used regardless of the
// column's values; for `sum`/`avg`/`min`/`max`, non-numeric cells are skipped.
import { useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { useBranding } from '../../branding/BrandingContext'
import { cached } from '../../services/widgetCache'
import { Banner, SpinnerRing } from '../../common'
import { resolveColumnName, toNumber } from '../../services/chartData'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import type { QueryResult } from '../../types/connectors'
import type { DashboardFilterWire, DashboardPeriodWire, KpiWidgetWire } from '../../types/dashboards'
import type { Aggregation } from '../../types/charts'
import { buildWidgetFilterParams, buildWidgetPeriodParams, type PeriodSelection } from './widgetFilters'
import { formatKpi, kpiDelta } from '../../services/kpi'
import { previousPeriod } from '../../services/period'
import { colors, fontSize, fonts, glass, radius, shadow } from '../../theme'

// Liquid-glass KPI card — backdrop blur over a slightly-translucent base, soft drop shadow, a
// thin inset highlight (the "specular") on the top edge that fakes a light from above (the macOS
// look). The card is its own little frosted panel that floats slightly off the page background.
const Frame = styled.div`
  flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: center; align-items: flex-start;
  gap: 8px; padding: 16px 18px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  background: ${colors.bg.card};
  ${glass.surface}
  box-shadow: ${shadow.sm}, inset 0 1px 0 rgba(255, 255, 255, 0.08);
  /* Subtle radial vignette top-left → bottom-right; cheap depth without a real gradient layer. */
  background-image: radial-gradient(120% 100% at 0% 0%, rgba(255, 255, 255, 0.04), transparent 60%);
  transition: transform 0.15s ease, box-shadow 0.15s ease;
  &:hover { transform: translateY(-1px); box-shadow: ${shadow.md}, inset 0 1px 0 rgba(255, 255, 255, 0.10); }
`
// Big-number sized via `clamp()` so it scales with the card's width — looks reasonable from a
// 3-col card (~150px wide) up to a full-width KPI (~1200px) without ever overflowing.
// The card's own caption (nomaubl's KPI card: label, the figure, the detail — all inside).
const Label = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans}; margin-bottom: 8px;`
const Value = styled.div`
  font-size: clamp(1.7rem, 4.2vw, 2.4rem); font-weight: 700; font-family: ${fonts.sans};
  color: ${colors.text.primary}; font-variant-numeric: tabular-nums; line-height: 1.05; text-align: left;
  letter-spacing: -0.02em;
`
const Sub = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Pending = styled.div`color: ${colors.text.muted};`
const Delta = styled.div<{ $dir: 'up' | 'down' | 'flat' }>`
  display: inline-flex; align-items: center; gap: 6px; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  font-variant-numeric: tabular-nums;
  color: ${({ $dir }) => ($dir === 'up' ? colors.green.main : $dir === 'down' ? colors.red.main : colors.text.muted)};
  & .prev { color: ${colors.text.muted}; }
`

export interface KpiWidgetProps {
  widget: KpiWidgetWire
  filters: DashboardFilterWire[]
  filterValues: Record<string, string>
  period?: DashboardPeriodWire
  selection?: PeriodSelection
  /** Bumped by the bar's Refresh: refetch past the cache. */
  refreshKey?: number
}

export function KpiWidget({ widget, filters, filterValues, period, selection, refreshKey = 0 }: KpiWidgetProps) {
  const { t } = useTranslation()
  const [result, setResult] = useState<QueryResult | null>(null)
  const [previous, setPrevious] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { findScreen } = useWorkspace()
  const { ui } = useBranding()
  const lastRefresh = useRef(0)
  const { i18n } = useTranslation()
  const lang = i18n.language || undefined

  // Resolve dashboard filters → URL params via the screen's dd_map (Phase 3 — column dd hints
  // live on the Screen layer now, not the connector describe()).
  const filterParams = useMemo(
    () => ({
      ...buildWidgetFilterParams(widget.connector, widget.query, filters, filterValues, findScreen),
      ...buildWidgetPeriodParams(widget.connector, widget.query, period, selection, findScreen),
    }),
    [widget.connector, widget.query, filters, filterValues, period, selection, findScreen],
  )
  const filterParamsKey = JSON.stringify(filterParams)
  // The previous period's params: the same filters, the period shifted back (nomaubl's rule).
  const prevSelection = useMemo<PeriodSelection | undefined>(
    () => (selection && selection.range.dateFrom ? { range: previousPeriod(selection.range), basis: selection.basis } : undefined),
    [selection],
  )
  const prevParams = useMemo(
    () => (widget.compare_previous && prevSelection ? {
      ...buildWidgetFilterParams(widget.connector, widget.query, filters, filterValues, findScreen),
      ...buildWidgetPeriodParams(widget.connector, widget.query, period, prevSelection, findScreen),
    } : null),
    [widget.compare_previous, widget.connector, widget.query, filters, filterValues, period, prevSelection, findScreen],
  )
  const prevParamsKey = JSON.stringify(prevParams)

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
    // eslint-disable-next-line react-hooks/exhaustive-deps -- filterParamsKey covers filterParams
  }, [widget.connector, widget.query, filterParamsKey, refreshKey])

  useEffect(() => {
    let cancelled = false
    setPrevious(null)
    if (!prevParams) return
    const qs = new URLSearchParams(prevParams).toString()
    api
      .get<QueryResult>(`/api/sql/${encodeURIComponent(widget.connector)}/${encodeURIComponent(widget.query)}${qs ? `?${qs}` : ''}`)
      .then((r) => { if (!cancelled) setPrevious(r) })
      .catch(() => { /* the comparison is optional: the card shows the value alone */ })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- prevParamsKey covers prevParams
  }, [widget.connector, widget.query, prevParamsKey])

  const value: number | null = useMemo(() => {
    if (!result) return null
    return computeKpi(result, widget.column, widget.aggregation)
  }, [result, widget.column, widget.aggregation])
  const prevValue: number | null = useMemo(() => (previous ? computeKpi(previous, widget.column, widget.aggregation) : null), [previous, widget.column, widget.aggregation])
  const delta = useMemo(() => kpiDelta(value, prevValue, widget.format, lang), [value, prevValue, widget.format, lang])

  // Display label for the column in the subtitle — the resolved `label` from `result.columns`
  // (came from the dictionary entry in the request's language), with the raw column name as the
  // fallback. `COUNT · User ID` reads much better than `COUNT · USR_ID`.
  const columnLabel = useMemo(() => {
    if (!result) return widget.column
    const actualName = resolveColumnName(result, widget.column)
    const col = result.columns.find((c) => c.name === actualName)
    return col?.label || actualName
  }, [result, widget.column])

  if (error) return <Frame>{widget.label && <Label>{widget.label}</Label>}<Banner $tone="error">{error}</Banner></Frame>
  if (!result) return <Frame>{widget.label && <Label>{widget.label}</Label>}<Pending><SpinnerRing size={18} thickness={2} /></Pending></Frame>
  return (
    <Frame>
      {widget.label && <Label>{widget.label}</Label>}
      <Value>{value === null ? '∅' : formatKpi(value, widget.format, lang)}</Value>
      <Sub>{t(`chart.agg.${widget.aggregation}`)} · {columnLabel}</Sub>
      {delta && prevValue !== null && (
        <Delta $dir={delta.direction} title={t('dashboard.kpi.previousTitle')}>
          {delta.direction === 'up' ? <ArrowUpRight size={14} /> : delta.direction === 'down' ? <ArrowDownRight size={14} /> : <Minus size={14} />}
          <span>{delta.text}</span>
          <span className="prev">{t('dashboard.kpi.previous', { value: formatKpi(prevValue, widget.format, lang) })}</span>
        </Delta>
      )}
    </Frame>
  )
}

/** Apply *aggregation* to *column*'s values across the result's rows. `count` returns the row
 *  count (matches SELECT COUNT(*)); the others sum/avg/min/max the parsable numeric cells.
 *  Resolves the column name case-insensitively against the result so a Pg-lowercased key
 *  doesn't drop every value to null. */
function computeKpi(result: QueryResult, column: string, agg: Aggregation): number | null {
  if (agg === 'count') return result.rows.length
  const actualName = resolveColumnName(result, column)
  const values: number[] = []
  for (const r of result.rows) {
    const n = toNumber(r[actualName])
    if (n !== null) values.push(n)
  }
  if (!values.length) return null
  switch (agg) {
    case 'sum': return values.reduce((a, b) => a + b, 0)
    case 'avg': return values.reduce((a, b) => a + b, 0) / values.length
    case 'min': return Math.min(...values)
    case 'max': return Math.max(...values)
    case 'none': return values[0]
  }
}
