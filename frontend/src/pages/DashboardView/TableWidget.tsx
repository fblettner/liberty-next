// Table widget for the dashboard grid — runs its (connector, query) and lists the rows in a
// compact scrollable grid. Suitable for "recent failures" / "top N" panels next to the charts.
// Shares the dashboard filter binding with the chart/kpi widgets (a column whose `dd` matches a
// filter's `dictionary_key` is bound on the fetch). Cells reuse the result's resolved column
// labels + the shared ruleCell formatter (boolean ●, enum labels); lookups show their raw code
// (a dashboard table stays lightweight — no per-cell lookup fetch).
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { api, ApiError } from '../../api/client'
import { Banner, SpinnerRing } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { enumMap, ruleCell } from '../../services/cells'
import type { Column, QueryResult } from '../../types/connectors'
import type { DashboardFilterWire, TableWidgetWire } from '../../types/dashboards'
import { buildWidgetFilterParams } from './widgetFilters'
import { colors, fontSize, fonts, glass, radius, shadow } from '../../theme'

const Frame = styled.div`
  flex: 1; min-height: 0; min-width: 0; overflow: auto;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  background: ${colors.bg.card};
  ${glass.surface}
  box-shadow: ${shadow.sm}, inset 0 1px 0 rgba(255, 255, 255, 0.08);
`
const Center = styled.div`height: 100%; display: flex; align-items: center; justify-content: center;`
const Table = styled.table`
  width: 100%; border-collapse: collapse; font-family: ${fonts.sans}; font-size: ${fontSize.base};
`
const Th = styled.th<{ $right?: boolean }>`
  position: sticky; top: 0; z-index: 1;
  text-align: ${({ $right }) => ($right ? 'right' : 'left')};
  padding: 7px 10px; white-space: nowrap;
  color: ${colors.text.muted}; font-weight: 600; font-size: ${fontSize.sm};
  background: ${colors.bg.dropdown}; border-bottom: 1px solid ${colors.border};
`
const Td = styled.td<{ $right?: boolean }>`
  text-align: ${({ $right }) => ($right ? 'right' : 'left')};
  padding: 6px 10px; white-space: nowrap; max-width: 320px; overflow: hidden; text-overflow: ellipsis;
  color: ${colors.text.primary}; border-bottom: 1px solid ${colors.border};
  font-variant-numeric: tabular-nums;
`
const Tr = styled.tr`&:hover td { background: var(--hover-subtle); }`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 20px; text-align: center;`

function isNumericish(c: Column): boolean {
  return /int|num|dec|float|double|real|money|currency|percent/i.test(`${c.format ?? ''} ${c.type ?? ''}`)
}

export interface TableWidgetProps {
  widget: TableWidgetWire
  filters: DashboardFilterWire[]
  filterValues: Record<string, string>
}

export function TableWidget({ widget, filters, filterValues }: TableWidgetProps) {
  const [result, setResult] = useState<QueryResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const { findScreen } = useWorkspace()

  const filterParams = useMemo(
    () => buildWidgetFilterParams(widget.connector, widget.query, filters, filterValues, findScreen),
    [widget.connector, widget.query, filters, filterValues, findScreen],
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widget.connector, widget.query, filterParamsKey])

  // Columns to show: the operator's explicit subset/order (matched case-insensitively against the
  // result), else every non-hidden result column. Pre-build the enum value→label maps once.
  const cols = useMemo<Column[]>(() => {
    if (!result) return []
    if (widget.columns.length) {
      const byLower = new Map(result.columns.map((c) => [c.name.toLowerCase(), c]))
      return widget.columns.map((n) => byLower.get(n.toLowerCase())).filter((c): c is Column => !!c)
    }
    return result.columns.filter((c) => !c.hidden)
  }, [result, widget.columns])
  const enumMaps = useMemo(
    () => new Map(cols.filter((c) => c.rule?.kind === 'enum').map((c) => [c.name, enumMap(c.rule as never)])),
    [cols],
  )

  const rows = useMemo(() => {
    if (!result) return []
    return widget.max_rows ? result.rows.slice(0, widget.max_rows) : result.rows
  }, [result, widget.max_rows])

  if (error) return <Banner $tone="error">{error}</Banner>
  if (!result) return <Frame><Center><SpinnerRing size={20} thickness={2} /></Center></Frame>
  if (rows.length === 0 || cols.length === 0) return <Frame><Empty>—</Empty></Frame>

  return (
    <Frame>
      <Table>
        <thead>
          <tr>{cols.map((c) => <Th key={c.name} $right={isNumericish(c)}>{c.label ?? c.name}</Th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <Tr key={i}>
              {cols.map((c) => {
                const cell = ruleCell(row[c.name], c, enumMaps.get(c.name))
                const boolTone = cell.kind === 'boolean-true' ? colors.green.main
                  : cell.kind === 'boolean-false' ? colors.red.main : undefined
                return (
                  <Td key={c.name} $right={isNumericish(c)}
                      style={boolTone ? { color: boolTone, textAlign: 'center' } : undefined}
                      title={cell.isNull ? undefined : cell.text}>
                    {cell.isNull ? '—' : cell.text}
                  </Td>
                )
              })}
            </Tr>
          ))}
        </tbody>
      </Table>
    </Frame>
  )
}
