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
  /** run the query immediately when the screen opens (no "Run" click) — v1's per-table auto-load flag. */
  auto_load: boolean
  writable: boolean
  /** writable companion queries — `<base>_put` / `<base>_post` / `<base>_delete` — that
   *  update / insert / delete a row of this query's result. Drive the TableView's batch edit. */
  update_query: string | null
  insert_query: string | null
  delete_query: string | null
  statement_type: string
  params: ParamDef[]
  bind_params: string[]
  /** resolved display hints from the query's `columns` config (label/format/hidden/filter/width/align/rule);
   *  `filter: true` ones are surfaced as server-filter fields in TableView. */
  columns: Column[]
  /** result columns that identify a row (v1's `col_key`) — the Excel import matches imported rows
   *  against the loaded ones on these to decide update (→ `update_query`) vs insert (→ `insert_query`). */
  key_columns: string[]
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
  /** surface this column in the TableView filter panel (v1's col_filter). */
  filter?: boolean
  /** cascading-filter deps (v1's ly_tbl_filters): when the `source` filter has a value, this
   *  column's LOOKUP options are narrowed to the rows whose `column` matches it. */
  filter_from?: { source: string; column: string }[]
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
