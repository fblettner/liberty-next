// Summary (aggregate) view — server-aggregated parent rows (GROUP BY <dimensions> + COUNT(*))
// with a chevron that lazily loads each group's underlying rows. Replaces a materialised rollup
// table: the counts come from the database over the whole result, not the row-capped grid.
//
// Both levels reuse ResultTable for rendering (column hints / formatting apply): the summary
// itself is a ResultTable fed the aggregate QueryResult + a `renderDetail` that mounts a
// <GroupDetail> per expanded row; GroupDetail fetches that one group's rows and renders them as
// a nested ResultTable.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { api } from '../../api/client'
import { Centered } from '../../common'
import { ResultTable } from './ResultTable'
import type { QueryResult } from '../../types/connectors'
import type { ScreenDetail, ScreenSummary } from '../../types/screens'
import { colors, fontSize, fonts } from '../../theme'

const DetailWrap = styled.div`padding: 8px 8px 8px 28px;`
const ErrLine = styled.div`padding: 10px 12px; color: ${colors.red.main}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`

/** ``_group`` spec — ``COL`` / ``COL~day`` — from the screen's summary dimensions. */
function groupSpec(summary: ScreenSummary): string {
  return summary.dimensions.map((d) => (d.bucket ? `${d.column}~${d.bucket}` : d.column)).join(',')
}

/** Read a dimension's value off a summary row, case-insensitively (the aggregate result column
 *  may come back lower-cased while the config column is upper). */
function dimValue(row: Record<string, unknown>, column: string): unknown {
  if (column in row) return row[column]
  const hit = Object.keys(row).find((k) => k.toLowerCase() === column.toLowerCase())
  return hit ? row[hit] : undefined
}

function buildQuery(base: Record<string, string>, extra: Record<string, string>): string {
  const qs = new URLSearchParams({ ...base, ...extra })
  return qs.toString()
}

/** One expanded group's rows — fetched on mount (collapsing unmounts; re-expanding refetches). */
function GroupDetail({
  connector, query, screen, summary, row, baseParams,
}: {
  connector: string; query: string; screen: ScreenDetail; summary: ScreenSummary
  row: Record<string, unknown>; baseParams: Record<string, string>
}) {
  const { t } = useTranslation()
  const [result, setResult] = useState<QueryResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const extra: Record<string, string> = { _group: groupSpec(summary) }
    for (const d of summary.dimensions) {
      const v = dimValue(row, d.column)
      extra[d.column] = v == null ? '' : String(v)
    }
    void (async () => {
      try {
        const r = await api.get<QueryResult>(
          `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}?${buildQuery(baseParams, extra)}`,
        )
        if (!cancelled) setResult(r)
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- row identity is the group key; deps stable per panel
  }, [])

  if (err) return <ErrLine>{err}</ErrLine>
  if (!result) return <Centered />
  return (
    <DetailWrap>
      {result.rows.length === 0 ? (
        <ErrLine style={{ color: colors.text.muted }}>{t('table.noResults')}</ErrLine>
      ) : (
        <ResultTable result={result} connector={connector} query={query} screen={screen} />
      )}
    </DetailWrap>
  )
}

export function SummaryView({
  connector, query, screen, params, screenApp, screenId,
}: {
  connector: string; query: string; screen: ScreenDetail
  /** Current run params + active server filters (so the summary respects them). */
  params: Record<string, string>
  screenApp?: string; screenId?: string
}) {
  const summary = screen.summary as ScreenSummary
  const [result, setResult] = useState<QueryResult | null>(null)
  const [err, setErr] = useState<string | null>(null)

  // Params shared by the summary + every detail fetch: the run params/filters + the screen pin.
  const baseParams = useMemo<Record<string, string>>(() => ({
    ...params,
    ...(screenId ? { _screen: screenId, _app: screenApp ?? connector } : {}),
  }), [params, screenId, screenApp, connector])

  useEffect(() => {
    let cancelled = false
    const extra = { _summary: '1', _group: groupSpec(summary) }
    void (async () => {
      setErr(null); setResult(null)
      try {
        const r = await api.get<QueryResult>(
          `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}?${buildQuery(baseParams, extra)}`,
        )
        if (!cancelled) setResult(r)
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e))
      }
    })()
    return () => { cancelled = true }
  }, [connector, query, summary, baseParams])

  // Label the COUNT(*) column from the config (the aggregate column has no dictionary hint).
  const labelled = useMemo<QueryResult | null>(() => {
    if (!result) return null
    const count_label = summary.count_label || 'Count'
    return {
      ...result,
      columns: result.columns.map((c) =>
        c.name === '_count' ? { ...c, label: count_label, format: c.format ?? 'integer' } : c,
      ),
    }
  }, [result, summary])

  if (err) return <Centered error>{err}</Centered>
  if (!labelled) return <Centered />
  return (
    <ResultTable
      key={labelled.columns.map((c) => c.name).join('|')}
      result={labelled}
      connector={connector}
      query={query}
      renderDetail={(row) => (
        <GroupDetail
          connector={connector}
          query={query}
          screen={screen}
          summary={summary}
          row={row as Record<string, unknown>}
          baseParams={baseParams}
        />
      )}
    />
  )
}
