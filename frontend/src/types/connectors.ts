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
  /** Phase 3 — these per-screen fields used to live on the connector's describe(). They now
   *  ride on the matching Screen (``GET /api/screens/{app}/{id}``) only. The TableView reads
   *  them from ``ScreenListItem`` / ``ScreenDetail`` when a Screen exists for this
   *  (connector, query); otherwise the inline grid editor / "Run" button apply. Fields below
   *  stay optional so a stale frontend that still reads them gets ``undefined``.
   */
  auto_load?: boolean
  update_query?: string | null
  insert_query?: string | null
  delete_query?: string | null
  columns?: Column[]
  key_columns?: string[]
}

export interface ApiEndpointMeta {
  name: string
  label: string | null
  description: string | null
  method: string
  path: string
  params: ParamDef[]
}

/** One CRUD slot of a table in the runtime connector meta — a query-meta plus which CRUD verb
 *  it fills (`get` / `put` / `post` / `delete`). */
export interface TableSlotMeta extends SqlQueryMeta {
  crud: string
}

/** A first-class CRUD table in the runtime connector meta — its own name / label / description
 *  plus the present CRUD slots. Emitted by `public_connector` alongside the flat `queries` so the
 *  screen editor can offer "pick a table → fill its read/update/insert/delete queries at once". */
export interface TableMeta {
  name: string
  label: string | null
  description: string | null
  slots: TableSlotMeta[]
}

export interface SqlConnectorMeta {
  name: string
  type: 'sql'
  /** Every runnable query, flat — table CRUD slots (synthesised `<base>_<crud>` names), custom
   *  queries, sequences and lookups. The historical shape; TableView + pickers read this. */
  queries: SqlQueryMeta[]
  /** The CRUD tables, grouped — present alongside `queries` for editors that want the grouping. */
  tables?: TableMeta[]
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
  | {
      kind: 'boolean'
      /** Value to send on check (also "yes" / ✓ render). v1's ``dd_rules_values``. */
      true_value: string
      /** Value to send on uncheck. Set explicitly by ``DictionaryEntry.false_value`` or
       *  inferred by the backend (Y→N, 1→0, true→false). Omitted → uncheck sends null
       *  (the v1 default; the DB column must accept NULL). */
      false_value?: string
    }
  | { kind: 'enum'; values: { value: string; label: string }[] }
  | {
      /** Form-layer auto-fill — seeds the field on dialog open (add mode only). ``source``
       *  is a built-in id the runtime resolves via the auth-builtins layer: ``current_date``
       *  (today's YYYY-MM-DD, from the JS clock) / ``login_user`` (the authenticated user's
       *  username). v2's port of v1's ``dd_rules`` SYSDATE / CURRENT_DATE / LOGIN. */
      kind: 'auto_fill'
      source: 'current_date' | 'login_user' | string
    }
  | {
      kind: 'lookup'
      connector: string
      query: string
      value: string
      label: string
      /** Static parameter bindings for the lookup's query (v1 ly_dictionary_filters flt_type='VALUE').
       *  Required for queries that take `:placeholder` params to even run — without these a UDC
       *  query returns nothing because SY/RT are NULL. The fetcher passes them as ?p=v on /api/sql. */
      params?: Record<string, string>
      /** v1's ly_lkp_params with lkp_dir='OUT' — extra dd_ids the picked row writes back to
       *  other form fields / grid cells beyond the headline ``value`` / ``label`` columns. */
      return_params?: string[]
      /** The lookup's declared query params double as the **key columns** that disambiguate a
       *  non-unique ``value`` (e.g. USR_ID is only unique per USR_APPS_ID). The grid resolves the
       *  label per row by matching these same-named columns — automatic, no per-column filter_from. */
      key_columns?: string[]
    }

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
  /** conditional visibility (v1's cdn_*): a list of `{field, value}` conditions, all of which must
   *  hold for the column to appear — a condition holds when its `field` server-filter is unset, or
   *  its value matches `value` (or is in `value` when it's an array). So a set filter outside the
   *  allowed set drops the column from the grid. (A bare `{field, value}` is treated as one item.) */
  visible_when?: { field: string; value: string | string[] } | { field: string; value: string | string[] }[]
  width?: number
  align?: 'left' | 'right' | 'center' | string
  format?: string
  /** Resolved BOOLEAN / ENUM / LOOKUP display rule from the field dictionary (the v2 form of v1's dd_rules). */
  rule?: DisplayRule
  /** Dictionary key the column was hinted with (v1's ``col_dd_id``). Surfaced so dashboard
   *  filters can cross-map columns by dd — one `APPS_ID` filter targets USR_APPS_ID,
   *  RLU_APPS_ID, CFD_APPS_ID, etc. across queries. Absent when the operator never set `dd`. */
  dd?: string
  /** The related-table write-back group this column belongs to (Screen.column_groups[].id). When
   *  set, the column comes from the read query's JOIN and is written back to the related table on
   *  Save (not the main update query). Absent ⇒ writes to the main table. */
  group?: string | null
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
