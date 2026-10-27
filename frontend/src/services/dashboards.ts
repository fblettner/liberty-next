// The config dashboards' plain logic: the export (what the page shows, as a file).
import type { DateRange } from './period'
import { postDownload } from './download'

export interface DashboardExportBody {
  format: 'xlsx' | 'pdf'
  /** The filter bar's picks by filter id (blank = all). */
  filters?: Record<string, string>
  /** The period control, when the dashboard has one (an empty range = all dates). */
  period?: { from?: string; to?: string; basis?: string }
}

export function exportPeriod(range: DateRange, basis: string): DashboardExportBody['period'] {
  return { ...(range.dateFrom ? { from: range.dateFrom } : {}), ...(range.dateTo ? { to: range.dateTo } : {}), ...(basis ? { basis } : {}) }
}

/** Download a dashboard as xlsx or pdf, run server-side with the same filters and period. */
export function downloadDashboardExport(dashboardId: string, body: DashboardExportBody): Promise<void> {
  return postDownload(`/api/dashboards/${encodeURIComponent(dashboardId)}/export`, body, `dashboard.${body.format}`)
}
