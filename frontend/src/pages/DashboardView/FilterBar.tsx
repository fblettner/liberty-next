// The filter bar that sits above the widget grid when a dashboard declares any filters.
// Each filter is a SearchSelect populated by a one-shot fetch of its options query
// (cached per `(connector, query)` for the session so multiple dashboards sharing one
// options query don't refetch). Picking a value lifts state into DashboardView, which
// passes the filter values down to every widget; widgets refetch with the resolved
// column bound as a URL param when their query has a matching `dd` column.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { FileDown, RefreshCw } from 'lucide-react'
import { Button, DateRangeFilter, SearchSelect, type DateBasis, type DatePreset, type DateRange, type SearchSelectOption } from '../../common'
import type { QueryResult } from '../../types/connectors'
import type { DashboardFilterWire } from '../../types/dashboards'
import { colors, fontSize, fonts, glass, radius, shadow } from '../../theme'

// `position: relative; z-index: 20` is non-obvious but necessary: every dashboard card below
// uses `backdrop-filter: blur(...)` (`glass.surface`), which creates its OWN stacking context.
// Inside that context, a sibling card painted later in the DOM sits in front of the FilterBar's
// inner SearchSelect popover (z-index: 300 *within* the FilterBar's local stacking context, not
// document-global). Lifting the FilterBar itself to `z-index: 20` in the surrounding Stack
// promotes its stacking context above the Grid's, so the popover paints on top.
const Bar = styled.div`
  position: relative; z-index: 20;
  display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end;
  padding: 12px 16px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  background: ${colors.bg.card};
  ${glass.surface}
  box-shadow: ${shadow.sm}, inset 0 1px 0 rgba(255, 255, 255, 0.08);
`
const Cell = styled.div`min-width: 220px;`
const Label = styled.div`
  font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.sans};
  text-transform: uppercase; letter-spacing: 0.04em; font-weight: 600; margin-bottom: 4px;
`

// Module-level cache for `(connector/query)` options. Keyed by the URL we'd fetch — same shape
// as services/lookups.ts. Survives across DashboardView mounts within one session.
const optionsCache = new Map<string, Promise<SearchSelectOption[]>>()

function fetchOptions(filter: DashboardFilterWire): Promise<SearchSelectOption[]> {
  const o = filter.options
  const key = `${o.connector}/${o.query}`
  let p = optionsCache.get(key)
  if (!p) {
    p = api
      .get<QueryResult>(`/api/sql/${encodeURIComponent(o.connector)}/${encodeURIComponent(o.query)}`)
      .then((r) => {
        // The wire returns the discovered case (lowercase on Postgres) — match case-insensitively
        // against the operator-provided value_column / label_column.
        const cols = r.columns.map((c) => c.name)
        const lowerToActual = new Map(cols.map((c) => [c.toLowerCase(), c]))
        const vKey = lowerToActual.get(o.value_column.toLowerCase()) ?? o.value_column
        const lKey = lowerToActual.get(o.label_column.toLowerCase()) ?? o.label_column
        return r.rows
          .filter((row) => row[vKey] !== null && row[vKey] !== undefined)
          .map((row) => ({
            value: String(row[vKey]),
            label: row[lKey] !== null && row[lKey] !== undefined ? String(row[lKey]) : String(row[vKey]),
            mono: String(row[vKey]),
          }))
      })
      .catch((e) => {
        if (e instanceof ApiError) return []
        throw e
      })
    optionsCache.set(key, p)
  }
  return p
}

export interface FilterBarProps {
  filters: DashboardFilterWire[]
  values: Record<string, string>
  onChange: (id: string, value: string) => void
  /** The dashboard's period control, when it declares one. */
  period?: { preset: DatePreset; range: DateRange; onChange: (preset: DatePreset, range: DateRange) => void; basis?: DateBasis }
  /** When the newest widget result was fetched (epoch ms), shown next to the Refresh button. */
  updatedAt?: number | null
  onRefresh?: () => void
  /** Export what the page shows (the same filters and period), as a file. */
  onExport?: (format: 'xlsx' | 'pdf') => void
}

export function FilterBar({ filters, values, onChange, period, updatedAt, onRefresh, onExport }: FilterBarProps) {
  const { t } = useTranslation()
  // One options-map per filter. Each filter loads its own options once; the cache above keeps
  // the same `(connector, query)` pair from refetching across filters / dashboards.
  const [optionsMap, setOptionsMap] = useState<Record<string, SearchSelectOption[]>>({})
  useEffect(() => {
    let cancelled = false
    filters.forEach((f) => {
      void fetchOptions(f).then((opts) => {
        if (cancelled) return
        setOptionsMap((cur) => ({ ...cur, [f.id]: opts }))
      })
    })
    return () => { cancelled = true }
  }, [filters])
  // "All" sentinel — value `""` clears the filter (the runtime treats blank == no filter).
  const allLabel = useMemo(() => t('dashboard.filter.all'), [t])
  const updatedLabel = updatedAt ? new Date(updatedAt).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }) : null

  return (
    <Bar>
      {period && (
        <Cell style={{ minWidth: 0 }}>
          <Label>{t('period.label')}</Label>
          <DateRangeFilter preset={period.preset} range={period.range} onChange={period.onChange} basis={period.basis} />
        </Cell>
      )}
      {filters.map((f) => {
        const opts = optionsMap[f.id]
        const loading = opts === undefined
        return (
          <Cell key={f.id}>
            <Label>{f.label}</Label>
            <SearchSelect
              value={values[f.id] ?? ''}
              onChange={(v) => onChange(f.id, v)}
              options={opts ?? []}
              placeholder={loading ? t('dashboard.filter.loading') : allLabel}
              anyLabel={allLabel}
            />
          </Cell>
        )
      })}
      {(onRefresh || onExport) && (
        <Cell style={{ minWidth: 0, marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 8 }}>
          {onExport && <Button $size="sm" $variant="ghost" onClick={() => onExport('xlsx')} title={t('dashboard.exportExcel')}><FileDown size={13} /> Excel</Button>}
          {onExport && <Button $size="sm" $variant="ghost" onClick={() => onExport('pdf')} title={t('dashboard.exportPdf')}><FileDown size={13} /> PDF</Button>}
          {onRefresh && <Button $size="sm" $variant="ghost" onClick={onRefresh} title={t('dashboard.refresh')}><RefreshCw size={13} /></Button>}
          {updatedLabel && <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('dashboard.updatedAt', { time: updatedLabel })}</span>}
        </Cell>
      )}
    </Bar>
  )
}
