// The documentation reports' catalogue and the download path of one (no React here).
import { api } from '../api/client'
import type { DocReport, DocReportKind, DocReportRequest } from '../types/docReports'

export const getDocReports = (lang: string) =>
  api.get<{ reports: DocReport[] }>(`/admin/dev/reports?lang=${encodeURIComponent(lang)}`).then((r) => r.reports)

/** The sections ticked when a report is first shown: the ones the server marks as default. */
export function defaultSections(report: DocReport): string[] {
  return report.sections.filter((s) => s.default).map((s) => s.id)
}

/** The GET path that renders *kind*: sections in the catalogue's order, only the options whose section is ticked. */
export function docReportPath(kind: DocReportKind, report: DocReport | undefined, req: DocReportRequest): string {
  const order = report ? report.sections.map((s) => s.id) : req.sections
  const sections = order.filter((id) => req.sections.includes(id))
  const q = new URLSearchParams({ lang: req.lang, format: req.format, sections: sections.join(',') })
  if (req.date?.trim()) q.set('date', req.date.trim())
  for (const o of report?.options ?? []) {
    if (req.options?.[o.id] && sections.includes(o.section)) q.set(o.id, 'true')
  }
  return `/admin/dev/reports/${kind}?${q.toString()}`
}
