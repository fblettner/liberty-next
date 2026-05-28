// DashboardView — renders a Phase-8 dashboard: a layout of widgets (chart + kpi today) on a
// 12-column CSS grid. Each widget owns its own (connector, query) and fetches its data
// independently, so a slow query in one widget doesn't block the rest.
//
// Permission: each widget's fetch hits `/api/sql/<c>/<q>`, which gates on
// `sql:<c>:<q>`. The backend already pruned widgets the caller can't see, so any widget
// the operator sees here is expected to succeed; transient errors surface inline per-widget.
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { LayoutDashboard } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Centered, Mono, PageLayout, Stack } from '../../common'
import type { Dashboard, DashboardFilterWire, DashboardWidget } from '../../types/dashboards'
import { ChartWidget } from './ChartWidget'
import { FilterBar } from './FilterBar'
import { KpiWidget } from './KpiWidget'
import { TableWidget } from './TableWidget'
import { colors, fontSize, fonts, radius } from '../../theme'

// CSS-grid layout: 12 cols on desktop, collapse to 6 on tablet, 1 on mobile so dashboards stay
// useful on a phone. Each row is 180px tall by default — fits a KPI card snugly; charts in the
// dashboard config get `row_span = 2` (→ 360px) so they have a decent canvas. A widget whose
// `col_span` is larger than the available track count simply spans the full row (CSS Grid clamps
// `span N` to "to end of row" when N > remaining tracks).
const ROW_PX = 150  // matches DashboardView/ChartWidget's `ROW_PX` so chart height computes right
const Grid = styled.div`
  display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px;
  grid-auto-rows: ${ROW_PX}px;
  @media (max-width: 1100px) { grid-template-columns: repeat(6, 1fr); }
  @media (max-width: 700px) {
    grid-template-columns: 1fr;
    & > * { grid-column: 1 / -1 !important; }
  }
`
const WidgetFrame = styled.div<{ $cs: number; $rs: number }>`
  grid-column: span ${({ $cs }) => $cs};
  grid-row: span ${({ $rs }) => $rs};
  display: flex; flex-direction: column; gap: 6px; min-width: 0; min-height: 0;
`
const WidgetTitle = styled.div`
  /* Small caps-y label above each card — matches the rest of the app's section headers
     (Tag/Sub typography), reads as a quiet caption rather than competing with the card. */
  font-size: ${fontSize.micro}; color: ${colors.text.muted};
  font-family: ${fonts.sans}; font-weight: 600; letter-spacing: 0.04em;
  padding: 0 4px; flex-shrink: 0;
`
const EmptyState = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
  border: 1px dashed ${colors.border}; border-radius: ${radius.lg}; padding: 30px; text-align: center;
`

export default function DashboardView({ dashboardId }: { dashboardId: string }) {
  const { t } = useTranslation()
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Filter state: `{[filter.id]: pickedValue}`. Empty string === "all" (no filter). Seeded from
  // `default_value` once when the dashboard loads.
  const [filterValues, setFilterValues] = useState<Record<string, string>>({})

  useEffect(() => {
    let cancelled = false
    setDashboard(null)
    setError(null)
    setFilterValues({})
    api
      .get<Dashboard>(`/api/dashboards/${encodeURIComponent(dashboardId)}`)
      .then((d) => {
        if (cancelled) return
        setDashboard(d)
        // Seed the filter state from each filter's `default_value`. The user can clear it via "All".
        if (d.filters?.length) {
          const seeded: Record<string, string> = {}
          for (const f of d.filters) if (f.default_value) seeded[f.id] = f.default_value
          setFilterValues(seeded)
        }
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [dashboardId])

  const filters = dashboard?.filters ?? []

  if (error) return <PageLayout title={dashboardId}><Banner $tone="error">{error}</Banner></PageLayout>
  if (!dashboard) return <Centered />

  return (
    <PageLayout
      icon={<LayoutDashboard size={18} />}
      title={dashboard.label}
      description={dashboard.description ? <span>{dashboard.description}</span> : <Mono>{dashboardId}</Mono>}
    >
      <Stack gap={14} style={{ flex: 1, minHeight: 0 }}>
        {filters.length > 0 && (
          <FilterBar
            filters={filters}
            values={filterValues}
            onChange={(id, value) => setFilterValues((cur) => ({ ...cur, [id]: value }))}
          />
        )}
        {dashboard.widgets.length === 0 ? (
          <EmptyState>{t('dashboard.empty')}</EmptyState>
        ) : (
          <Grid>
            {dashboard.widgets.map((w, i) => (
              <WidgetCell key={i} widget={w} filters={filters} filterValues={filterValues} />
            ))}
          </Grid>
        )}
      </Stack>
    </PageLayout>
  )
}

/** One cell in the grid. Picks the renderer by widget kind; both renderers fetch their own query.
 *  `filters` + `filterValues` thread down so each widget can resolve its own column-by-`dd` and
 *  append URL params on the data fetch. */
function WidgetCell({
  widget, filters, filterValues,
}: {
  widget: DashboardWidget
  filters: DashboardFilterWire[]
  filterValues: Record<string, string>
}) {
  return (
    <WidgetFrame $cs={widget.col_span} $rs={widget.row_span}>
      {widget.label && <WidgetTitle>{widget.label}</WidgetTitle>}
      {widget.type === 'chart'
        ? <ChartWidget widget={widget} filters={filters} filterValues={filterValues} />
        : widget.type === 'table'
        ? <TableWidget widget={widget} filters={filters} filterValues={filterValues} />
        : <KpiWidget widget={widget} filters={filters} filterValues={filterValues} />}
    </WidgetFrame>
  )
}
