// Connector / query / endpoint shapes returned by liberty/web (the `/api/*` routes).

export interface ParamDef {
  name: string
  label: string | null
  default: string | null
}

export interface SqlQueryMeta {
  name: string
  label: string | null
  description: string | null
  writable: boolean
  /** the writable query that updates one row of this query's result (`<base>_put`), if any — enables inline edit */
  update_query: string | null
  statement_type: string
  params: ParamDef[]
  bind_params: string[]
}

export interface ApiEndpointMeta {
  name: string
  label: string | null
  description: string | null
  method: string
  path: string
  params: ParamDef[]
}

export interface SqlConnectorMeta {
  name: string
  type: 'sql'
  queries: SqlQueryMeta[]
}

export interface ApiConnectorMeta {
  name: string
  type: 'api'
  base_url: string | null
  auth_type: string | null
  endpoints: ApiEndpointMeta[]
}

export type ConnectorMeta = SqlConnectorMeta | ApiConnectorMeta

export type DisplayRule =
  | { kind: 'boolean'; true_value: string }
  | { kind: 'enum'; values: { value: string; label: string }[] }
  | { kind: 'lookup'; connector: string; query: string; value: string; label: string }

export interface Column {
  name: string
  type: string | null
  /** Optional display hints from the query's `columns` config (see ColumnHint on the backend). */
  label?: string
  hidden?: boolean
  width?: number
  align?: 'left' | 'right' | 'center' | string
  format?: string
  /** Resolved BOOLEAN / ENUM / LOOKUP display rule from the field dictionary (the v2 form of v1's dd_rules). */
  rule?: DisplayRule
}

export interface QueryResult {
  connector: string
  query: string
  statement_type: string
  columns: Column[]
  rows: Record<string, unknown>[]
  row_count: number
  rowcount: number
  truncated: boolean
  duration_ms: number
}

export interface ApiResult {
  connector: string
  endpoint: string
  success: boolean
  status_code: number
  url: string
  json: unknown
  body: string | null
  extracted: unknown
  mapped: Record<string, unknown>
  error: string | null
  duration_ms: number
}
