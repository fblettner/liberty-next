// KPI widget for the dashboard grid — runs the widget's query, applies the configured
// aggregation to one column, displays the result as a big number. Suitable for "Active
// users · 686" style displays. For `count`, the row count is used regardless of the
// column's values; for `sum`/`avg`/`min`/`max`, non-numeric cells are skipped.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, SpinnerRing } from '../../common'
import { resolveColumnName, toNumber } from '../../services/chartData'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import type { QueryResult } from '../../types/connectors'
import type { DashboardFilterWire, KpiWidgetWire } from '../../types/dashboards'
import type { Aggregation } from '../../types/charts'
import { buildWidgetFilterParams } from './widgetFilters'
import { colors, fontSize, fonts, glass, radius, shadow } from '../../theme'

// Liquid-glass KPI card — backdrop blur over a slightly-translucent base, soft drop shadow, a
// thin inset highlight (the "specular") on the top edge that fakes a light from above (the macOS
// look). The card is its own little frosted panel that floats slightly off the page background.
const Frame = styled.div`
  flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: center; align-items: center;
  gap: 6px; padding: 14px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
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
const Value = styled.div`
  font-size: clamp(1.7rem, 4.2vw, 2.4rem); font-weight: 700; font-family: ${fonts.sans};
  color: ${colors.text.primary}; font-variant-numeric: tabular-nums; line-height: 1.05; text-align: center;
  letter-spacing: -0.02em;
`
const Sub = styled.div`
  font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.sans};
  text-transform: uppercase; letter-spacing: 0.06em; font-weight: 500; text-align: center;
`
const Pending = styled.div`color: ${colors.text.muted};`

export interface KpiWidgetProps {
  widget: KpiWidgetWire
  filters: DashboardFilterWire[]
  filterValues: Record<string, string>
}

export function KpiWidget({ widget, filters, filterValues }: KpiWidgetProps) {
  const { t } = useTranslation()
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { connectors } = useWorkspace()

  const filterParams = useMemo(
    () => buildWidgetFilterParams(widget.connector, widget.query, filters, filterValues, connectors),
    [widget.connector, widget.query, filters, filterValues, connectors],
  )
  const filterParamsKey = JSON.stringify(filterParams)

  useEffect(() => {
    let cancelled = false
    setResult(null)
    setError(null)
    const qs = new URLSearchParams(filterParams).toString()
    const url = `/api/sql/${encodeURIComponent(widget.connector)}/${encodeURIComponent(widget.query)}${qs ? `?${qs}` : ''}`
    api
      .get<QueryResult>(url)
      .then((r) => { if (!cancelled) setResult(r) })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- filterParamsKey covers filterParams
  }, [widget.connector, widget.query, filterParamsKey])

  const value: number | null = useMemo(() => {
    if (!result) return null
    return computeKpi(result, widget.column, widget.aggregation)
  }, [result, widget.column, widget.aggregation])

  // Display label for the column in the subtitle — the resolved `label` from `result.columns`
  // (came from the dictionary entry in the request's language), with the raw column name as the
  // fallback. `COUNT · User ID` reads much better than `COUNT · USR_ID`.
  const columnLabel = useMemo(() => {
    if (!result) return widget.column
    const actualName = resolveColumnName(result, widget.column)
    const col = result.columns.find((c) => c.name === actualName)
    return col?.label || actualName
  }, [result, widget.column])

  if (error) return <Frame><Banner $tone="error">{error}</Banner></Frame>
  if (!result) return <Frame><Pending><SpinnerRing size={18} thickness={2} /></Pending></Frame>
  return (
    <Frame>
      <Value>{value === null ? '∅' : formatValue(value, widget.format)}</Value>
      <Sub>{t(`chart.agg.${widget.aggregation}`)} · {columnLabel}</Sub>
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

function formatValue(v: number, fmt?: string): string {
  if (fmt === 'percent') return `${(v * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`
  if (fmt === 'currency') return v.toLocaleString(undefined, { style: 'currency', currency: 'EUR' })
  if (Number.isInteger(v)) return v.toLocaleString()
  return v.toLocaleString(undefined, { maximumFractionDigits: 3 })
}
