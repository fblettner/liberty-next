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
import type { Dashboard, DashboardWidget } from '../../types/dashboards'
import { ChartWidget } from './ChartWidget'
import { KpiWidget } from './KpiWidget'
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

  useEffect(() => {
    let cancelled = false
    setDashboard(null)
    setError(null)
    api
      .get<Dashboard>(`/api/dashboards/${encodeURIComponent(dashboardId)}`)
      .then((d) => { if (!cancelled) setDashboard(d) })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [dashboardId])

  if (error) return <PageLayout title={dashboardId}><Banner $tone="error">{error}</Banner></PageLayout>
  if (!dashboard) return <Centered />

  return (
    <PageLayout
      icon={<LayoutDashboard size={18} />}
      title={dashboard.label}
      description={dashboard.description ? <span>{dashboard.description}</span> : <Mono>{dashboardId}</Mono>}
    >
      <Stack gap={14} style={{ flex: 1, minHeight: 0 }}>
        {dashboard.widgets.length === 0 ? (
          <EmptyState>{t('dashboard.empty')}</EmptyState>
        ) : (
          <Grid>
            {dashboard.widgets.map((w, i) => (
              <WidgetCell key={i} widget={w} />
            ))}
          </Grid>
        )}
      </Stack>
    </PageLayout>
  )
}

/** One cell in the grid. Picks the renderer by widget kind; both renderers fetch their own query. */
function WidgetCell({ widget }: { widget: DashboardWidget }) {
  return (
    <WidgetFrame $cs={widget.col_span} $rs={widget.row_span}>
      {widget.label && <WidgetTitle>{widget.label}</WidgetTitle>}
      {widget.type === 'chart' ? <ChartWidget widget={widget} /> : <KpiWidget widget={widget} />}
    </WidgetFrame>
  )
}
