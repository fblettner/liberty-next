// Wire shapes for the /api/reports endpoints — mirror liberty/reports/schema.py
// (ReportParam / ReportDef / OutputFormat) and liberty/web/reports.py
// (ReportListResponse / RunReportBody).

export type OutputFormat = 'markdown' | 'pdf'

export interface ReportParam {
  name: string
  label: string
  type: 'int' | 'float' | 'bool' | 'string'
  required: boolean
  default: unknown
  description: string
}

export interface ReportDef {
  id: string                // kebab-case slug, unique within scope
  scope: string             // plugin name (e.g. "nomasx1")
  title: string             // PDF cover title + UI list headline
  description: string
  formats: OutputFormat[]   // supported output formats
  params: ReportParam[]
  callable: string          // module:function — not exposed to operators but kept for parity
  licensed: boolean
}

export interface ReportListResponse {
  reports: ReportDef[]
}

export interface RunReportBody {
  params: Record<string, unknown>
  format: OutputFormat
}
