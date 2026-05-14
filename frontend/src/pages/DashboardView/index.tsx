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
import { colors, fontSize, radius } from '../../theme'

// 12-column grid; each widget claims col_span x row_span cells. We use `grid-auto-rows` so the
// row height is predictable (charts need a fixed canvas to lay out their axes against).
const Grid = styled.div`
  display: grid; grid-template-columns: repeat(12, 1fr); gap: 14px;
  grid-auto-rows: 320px;
`
const WidgetFrame = styled.div<{ $cs: number; $rs: number }>`
  grid-column: span ${({ $cs }) => $cs};
  grid-row: span ${({ $rs }) => $rs};
  display: flex; flex-direction: column; gap: 6px; min-width: 0; min-height: 0;
`
const WidgetTitle = styled.div`
  font-size: ${fontSize.sm}; color: ${colors.text.secondary}; font-weight: 600;
  padding: 0 4px; flex-shrink: 0;
`
const EmptyState = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
  border: 1px dashed ${colors.border}; border-radius: ${radius.md}; padding: 30px; text-align: center;
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
