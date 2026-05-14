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
import { SearchSelect, type SearchSelectOption } from '../../common'
import type { QueryResult } from '../../types/connectors'
import type { DashboardFilterWire } from '../../types/dashboards'
import { colors, fontSize, fonts, glass, radius, shadow } from '../../theme'

const Bar = styled.div`
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
}

export function FilterBar({ filters, values, onChange }: FilterBarProps) {
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

  return (
    <Bar>
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
    </Bar>
  )
}
