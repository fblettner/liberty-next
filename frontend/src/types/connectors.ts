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

export interface Column {
  name: string
  type: string | null
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
