// KPI widget for the dashboard grid — runs the widget's query, applies the configured
// aggregation to one column, displays the result as a big number. Suitable for "Active
// users · 686" style displays. For `count`, the row count is used regardless of the
// column's values; for `sum`/`avg`/`min`/`max`, non-numeric cells are skipped.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Centered } from '../../common'
import { toNumber } from '../../services/chartData'
import type { QueryResult } from '../../types/connectors'
import type { KpiWidgetWire } from '../../types/dashboards'
import type { Aggregation } from '../../types/charts'
import { colors, fontSize, fonts, radius } from '../../theme'

const Frame = styled.div`
  flex: 1; min-height: 0; display: flex; flex-direction: column; justify-content: center; align-items: center;
  gap: 6px; padding: 16px; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input};
`
const Value = styled.div`
  font-size: 2.6rem; font-weight: 700; font-family: ${fonts.sans}; color: ${colors.text.primary};
  font-variant-numeric: tabular-nums; line-height: 1.1; text-align: center;
`
const Sub = styled.div`
  font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.mono}; text-transform: uppercase;
  letter-spacing: 0.04em;
`
const Pending = styled.div`color: ${colors.text.muted};`

export function KpiWidget({ widget }: { widget: KpiWidgetWire }) {
  const { t } = useTranslation()
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

  const value: number | null = useMemo(() => {
    if (!result) return null
    return computeKpi(result, widget.column, widget.aggregation)
  }, [result, widget.column, widget.aggregation])

  if (error) return <Frame><Banner $tone="error">{error}</Banner></Frame>
  if (!result) return <Frame><Pending><Centered /></Pending></Frame>
  return (
    <Frame>
      <Value>{value === null ? '∅' : formatValue(value, widget.format)}</Value>
      <Sub>{t(`chart.agg.${widget.aggregation}`)} · {widget.column}</Sub>
    </Frame>
  )
}

/** Apply *aggregation* to *column*'s values across the result's rows. `count` returns the row
 *  count (matches SELECT COUNT(*)); the others sum/avg/min/max the parsable numeric cells. */
function computeKpi(result: QueryResult, column: string, agg: Aggregation): number | null {
  if (agg === 'count') return result.rows.length
  const values: number[] = []
  for (const r of result.rows) {
    const n = toNumber(r[column])
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
