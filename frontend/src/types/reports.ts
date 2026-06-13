// Wire shapes for the /api/reports endpoints — mirror liberty/reports/schema.py
// (ReportParam / ReportDef / OutputFormat) and liberty/web/reports.py
// (ReportListResponse / RunReportBody).

export type OutputFormat = 'markdown' | 'pdf'

export interface ReportParamOption {
  value: unknown
  label: string
}

// When a param declares `options`, the run form renders a searchable dropdown and
// resolves the choices via GET /api/reports/{scope}/{id}/options/{param}. See
// liberty/reports/schema.py:ReportParamOptions.
export interface ReportParamOptions {
  kind: 'static' | 'connectors' | 'schemas' | 'query'
  values: ReportParamOption[]
  connector_param: string | null   // another param whose value is the connector (cascading)
  connector: string | null
  query: string | null
  value_column: string | null
  label_column: string | null
}

export interface ReportParam {
  name: string
  label: string
  type: 'int' | 'float' | 'bool' | 'string'
  required: boolean
  default: unknown
  description: string
  options?: ReportParamOptions | null
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
