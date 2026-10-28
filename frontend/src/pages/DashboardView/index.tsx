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
import { useBranding } from '../../branding/BrandingContext'
import { lastFetchedAt } from '../../services/widgetCache'
import { downloadDashboardExport, exportPeriod } from '../../services/dashboards'
import { Banner, Centered, Mono, PageLayout, Stack } from '../../common'
import type { Dashboard, DashboardFilterWire, DashboardWidget, TextWidgetWire } from '../../types/dashboards'
import { isPreset, rangeForPreset, type DatePreset, type DateRange } from '../../services/period'
import type { PeriodSelection } from './widgetFilters'
import { ChartWidget } from './ChartWidget'
import { FilterBar } from './FilterBar'
import { KpiWidget } from './KpiWidget'
import { TableWidget } from './TableWidget'
import { StatWidget } from './StatWidget'
import { ListWidget } from './ListWidget'
import { BarsWidget } from './BarsWidget'
import { TextWidget } from './TextWidget'
import { LinksWidget } from './LinksWidget'
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
const WidgetSub = styled.span`
  display: block; font-weight: 400; letter-spacing: 0; text-transform: none; font-size: ${fontSize.micro}; color: ${colors.text.muted}; margin-top: 1px;
`
const EmptyState = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
  border: 1px dashed ${colors.border}; border-radius: ${radius.lg}; padding: 30px; text-align: center;
`

export default function DashboardView({ dashboardId }: { dashboardId: string }) {
  const { t } = useTranslation()
  const { ui } = useBranding()
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Filter state: `{[filter.id]: pickedValue}`. Empty string === "all" (no filter). Seeded from
  // `default_value` once when the dashboard loads.
  const [filterValues, setFilterValues] = useState<Record<string, string>>({})
  // The period: preset + range + basis; seeded from the dashboard's `period.default` on load.
  const [preset, setPreset] = useState<DatePreset>('thisMonth')
  const [range, setRange] = useState<DateRange>({ dateFrom: '', dateTo: '' })
  const [basis, setBasis] = useState('')
  // Refresh: bumping the key makes every widget refetch past the cache; the bar shows when the
  // newest widget result was fetched.
  const [refreshKey, setRefreshKey] = useState(0)
  const [updatedAt, setUpdatedAt] = useState<number | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

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
        if (d.period) {
          // the dashboard's own default, else the app's; the pick then lives as long as this tab
          // is open (every open tab stays mounted) and resets when it is closed
          const fallback = d.period.default ?? ui.default_period
          const p: DatePreset = isPreset(fallback) ? fallback : 'thisMonth'
          setPreset(p)
          setRange(rangeForPreset(p))
          setBasis(d.period.basis?.[0]?.value ?? '')
        }
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ui.default_period is read once per dashboard load
  }, [dashboardId])

  // After each round of widget fetches, show when the newest result arrived.
  useEffect(() => {
    if (!dashboard) return
    const tick = () => setUpdatedAt(lastFetchedAt('/api/sql/'))
    tick()
    const h = window.setInterval(tick, 2000)
    return () => window.clearInterval(h)
  }, [dashboard, refreshKey, range, basis, filterValues])

  const filters = dashboard?.filters ?? []
  const period = dashboard?.period
  const selection: PeriodSelection | undefined = period ? { range, basis } : undefined

  // The export runs server-side with what the bar holds: the picks and the period.
  const exportFile = async (format: 'xlsx' | 'pdf') => {
    setExportError(null)
    try {
      await downloadDashboardExport(dashboardId, { format, filters: filterValues, ...(period ? { period: exportPeriod(range, basis) } : {}) })
    } catch (e) { setExportError(e instanceof Error ? e.message : String(e)) }
  }

  if (error) return <PageLayout title={dashboardId}><Banner $tone="error">{error}</Banner></PageLayout>
  if (!dashboard) return <Centered />

  return (
    <PageLayout
      icon={<LayoutDashboard size={18} />}
      title={dashboard.label}
      description={dashboard.description ? <span>{dashboard.description}</span> : <Mono>{dashboardId}</Mono>}
    >
      <Stack gap={14} style={{ flex: 1, minHeight: 0 }}>
        <FilterBar
          filters={filters}
          values={filterValues}
          onChange={(id, value) => setFilterValues((cur) => ({ ...cur, [id]: value }))}
          period={period ? {
            preset, range,
            onChange: (p, r) => { setPreset(p); setRange(r) },
            basis: period.basis && period.basis.length > 1
              ? { value: basis, options: period.basis.map((b) => ({ value: b.value, label: b.label })), onChange: setBasis }
              : undefined,
          } : undefined}
          updatedAt={updatedAt}
          onRefresh={() => setRefreshKey((k) => k + 1)}
          onExport={(f) => void exportFile(f)}
        />
        {exportError && <Banner $tone="error">{exportError}</Banner>}
        {dashboard.widgets.length === 0 ? (
          <EmptyState>{t('dashboard.empty')}</EmptyState>
        ) : (
          // a full-width text widget flows at its natural height between the grids (no fixed
          // row, no scrollbar); everything else lays out in the 12-column grid of fixed rows
          segments(dashboard.widgets).map((seg, k) => (
            seg.text ? (
              <TextWidget key={k} widget={seg.text} />
            ) : (
              <Grid key={k}>
                {seg.widgets.map((w, i) => (
                  <WidgetCell key={i} widget={w} filters={filters} filterValues={filterValues} period={period} selection={selection} refreshKey={refreshKey} />
                ))}
              </Grid>
            )
          ))
        )}
      </Stack>
    </PageLayout>
  )
}

/** The widget list cut into runs: a full-width text widget stands alone (rendered at its natural
 *  height), the widgets between two of them form one grid. */
function segments(widgets: DashboardWidget[]): ({ text: TextWidgetWire; widgets?: undefined } | { text?: undefined; widgets: DashboardWidget[] })[] {
  const out: ({ text: TextWidgetWire; widgets?: undefined } | { text?: undefined; widgets: DashboardWidget[] })[] = []
  let run: DashboardWidget[] = []
  for (const w of widgets) {
    if (w.type === 'text' && w.col_span >= 12) {
      if (run.length) { out.push({ widgets: run }); run = [] }
      out.push({ text: w })
    } else run.push(w)
  }
  if (run.length) out.push({ widgets: run })
  return out
}

/** One cell in the grid. Picks the renderer by widget kind; both renderers fetch their own query.
 *  `filters` + `filterValues` thread down so each widget can resolve its own column-by-`dd` and
 *  append URL params on the data fetch. */
function WidgetCell({
  widget, filters, filterValues, period, selection, refreshKey,
}: {
  widget: DashboardWidget
  filters: DashboardFilterWire[]
  filterValues: Record<string, string>
  period?: Dashboard['period']
  selection?: PeriodSelection
  refreshKey: number
}) {
  const common = { filters, filterValues, period, selection, refreshKey }
  return (
    <WidgetFrame $cs={widget.col_span} $rs={widget.row_span}>
      {/* a card widget (kpi, stat, list, bars) carries its own title inside (nomaubl's cards); a chart
          or a table keeps the caption above, with the subtitle under it */}
      {widget.label && (widget.type === 'chart' || widget.type === 'table') && (
        <WidgetTitle>{widget.label}{widget.description && <WidgetSub>{widget.description}</WidgetSub>}</WidgetTitle>
      )}
      {widget.type === 'chart'
        ? <ChartWidget widget={widget} {...common} />
        : widget.type === 'table'
        ? <TableWidget widget={widget} {...common} />
        : widget.type === 'stat'
        ? <StatWidget widget={widget} {...common} />
        : widget.type === 'list'
        ? <ListWidget widget={widget} {...common} />
        : widget.type === 'bars'
        ? <BarsWidget widget={widget} {...common} />
        : widget.type === 'text'
        ? <TextWidget widget={widget} />
        : widget.type === 'links'
        ? <LinksWidget widget={widget} />
        : <KpiWidget widget={widget} {...common} />}
    </WidgetFrame>
  )
}
