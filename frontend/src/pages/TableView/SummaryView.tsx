// Summary (aggregate) view — server-aggregated parent rows (GROUP BY <dimensions> + COUNT(*))
// with the underlying rows as native sub-rows (same columns, same row height — looks like
// TanStack grouping, not a grid-in-a-grid). The count comes from the database over the whole
// result, not the row-capped grid.
//
// The grid uses the DETAIL columns (dimensions moved to the front so a parent row reads
// left→right); parent rows are the aggregate rows (only the dimension columns are populated,
// plus a count badge); expanding one lazily fetches that group's rows and attaches them as
// sub-rows, which TanStack renders with the same column cells.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { Centered } from '../../common'
import { ResultTable } from './ResultTable'
import type { Column, QueryResult } from '../../types/connectors'
import type { ScreenDetail, ScreenSummary } from '../../types/screens'

type Row = Record<string, unknown>

/** ``_group`` spec — ``COL`` / ``COL~day`` — from the screen's summary dimensions. */
function groupSpec(summary: ScreenSummary): string {
  return summary.dimensions.map((d) => (d.bucket ? `${d.column}~${d.bucket}` : d.column)).join(',')
}

/** Read a dimension's value off a row, case-insensitively (the aggregate column may come back
 *  lower-cased while the config column is upper). */
function dimValue(row: Row, column: string): unknown {
  if (column in row) return row[column]
  const hit = Object.keys(row).find((k) => k.toLowerCase() === column.toLowerCase())
  return hit ? row[hit] : undefined
}

function buildQuery(base: Record<string, string>, extra: Record<string, string>): string {
  return new URLSearchParams({ ...base, ...extra }).toString()
}

export function SummaryView({
  connector, query, screen, params, screenApp, screenId, detailColumns,
}: {
  connector: string; query: string; screen: ScreenDetail
  /** Current run params + active server filters (so the summary respects them). */
  params: Record<string, string>
  screenApp?: string; screenId?: string
  /** The flat read query's columns — the children render in these (summary parents share them). */
  detailColumns: Column[]
}) {
  const summary = screen.summary as ScreenSummary
  const [result, setResult] = useState<QueryResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const baseParams = useMemo<Record<string, string>>(() => ({
    ...params,
    ...(screenId ? { _screen: screenId, _app: screenApp ?? connector } : {}),
  }), [params, screenId, screenApp, connector])

  // Detail columns with the summary dimensions pulled to the front, so a parent row's populated
  // cells read left→right before the (parent-blank) detail-only columns.
  const columns = useMemo<Column[]>(() => {
    const order = summary.dimensions.map((d) => d.column.toLowerCase())
    const rank = (c: Column) => { const i = order.indexOf(c.name.toLowerCase()); return i < 0 ? order.length + 1 : i }
    return [...detailColumns].sort((a, b) => rank(a) - rank(b))
  }, [detailColumns, summary])

  // Fetch the aggregate parent rows (respecting the active filters).
  useEffect(() => {
    let cancelled = false
    void (async () => {
      setErr(null); setResult(null)
      try {
        const r = await api.get<QueryResult>(
          `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}?${buildQuery(baseParams, { _summary: '1', _group: groupSpec(summary) })}`,
        )
        if (!cancelled) setResult(r)
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => { cancelled = true }
  }, [connector, query, summary, baseParams])

  // Lazily load one group's rows on expand, attach them as sub-rows of the parent (mutate +
  // bump the rows array ref so TanStack recomputes while parent identities — and the expansion
  // state — stay stable).
  const onRowExpand = useCallback((row: Row) => {
    if (row.__children !== undefined) return
    const extra: Record<string, string> = { _group: groupSpec(summary) }
    for (const d of summary.dimensions) {
      const v = dimValue(row, d.column)
      extra[d.column] = v == null ? '' : String(v)
    }
    const finish = (kids: Row[]) => {
      row.__children = kids
      setResult((prev) => (prev ? { ...prev, rows: [...prev.rows] } : prev))
    }
    void api.get<QueryResult>(
      `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}?${buildQuery(baseParams, extra)}`,
    ).then((r) => finish(r.rows as Row[])).catch(() => finish([]))
  }, [connector, query, summary, baseParams])

  const view = useMemo<QueryResult | null>(() => (result ? { ...result, columns } : null), [result, columns])

  if (err) return <Centered error>{err}</Centered>
  if (!view) return <Centered />
  return (
    <ResultTable
      result={view}
      connector={connector}
      query={query}
      // Pass the screen so child (statement) rows keep their row menu (e.g. "Display values") +
      // row-click drill. Parents are group rows — DataTable doesn't fire row click/menu on them.
      screen={screen}
      // A distinct persistence key so the summary grid's columns/views don't collide with the flat table's.
      tableId={screenId ? `screen:${screenApp ?? connector}:${screenId}:summary` : `sql:${connector}:${query}:summary`}
      getSubRows={(r) => r.__children as Row[] | undefined}
      onRowExpand={(r) => onRowExpand(r as Row)}
      subRowCount={(r) => Number((r as Row)._count) || 0}
    />
  )
}
