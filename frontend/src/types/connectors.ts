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
  /** Pool NAMES this connector may run against (multi-environment). `[pool] + pools` from the
   *  config — identifiers only, never URLs/credentials. Length > 1 ⇒ the app switcher offers a
   *  pool picker; a chosen pool rides on `X-Liberty-Pool` for this connector's queries. */
  pools?: string[]
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
  | { kind: 'enum'; values: { value: string; label: string }[]; /** the enum set's name — shown as a dropdown's empty-option label */ title?: string }
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
      /** the lookup's human description — shown as a dropdown's empty-option label */
      title?: string
      /** Static parameter bindings for the lookup's query (v1 ly_dictionary_filters flt_type='VALUE').
       *  Required for queries that take `:placeholder` params to even run — without these a UDC
       *  query returns nothing because SY/RT are NULL. The fetcher passes them as ?p=v on /api/sql. */
      params?: Record<string, string>
      /** Columns this lookup can flow back on a pick — the menu a column's ``return_binds`` picks
       *  from. No longer auto-mapped by dd; the explicit mapping lives on ``Column.return_binds``. */
      return_params?: string[]
      /** Extra result columns shown beside code + label in the dropdown (display only). */
      display_fields?: string[]
      /** Which display_fields get an in-dropdown facet-chip filter (subset of display_fields). */
      filter_fields?: string[]
      /** Multi-source union: every source `{connector, query}` (primary first) is fetched on its
       *  own connector and the rows concatenated (UNION ALL, sorted by value) — enables combining
       *  rows across databases. Absent for a plain single-query lookup. */
      sources?: { connector: string; query: string }[]
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
  /** LOOKUP return → target-column fills: on a pick, write the picked row's ``param`` value into
   *  the sibling column ``column`` on the same row. Drives the grid bulk-edit + Excel import
   *  return-fill (explicit replacement for v1's auto-by-dd return-param mapping). */
  return_binds?: { param: string; column: string }[]
  /** Conditional forced defaults: when sibling `field` == `value`, this column is set to `default`
   *  and locked (read-only). First matching rule wins. Honoured by the grid + dialog. */
  default_when?: { field: string; value: string | string[]; default: string; lock?: boolean }[]
  /** LOOKUP/ENUM: show only the code column in the grid, not the resolved-label column. */
  hide_label?: boolean
  /** Conditional rule overrides: when sibling `field` == `value`, use `rule` (a resolved display
   *  rule, or null → plain) instead of the base `rule`. First match wins; evaluated per row/form.
   *  Each entry carries its OWN `lookup_param_binds` + `return_binds` (independent of the column's
   *  base binds), so two rules on the same discriminator bind different params (f00950 FSDTAI:
   *  get_form_name narrowed by OBNM vs get_data_item with no narrow). */
  rules_when?: {
    field: string; value: string | string[]; rule: DisplayRule | null
    lookup_param_binds?: { param: string; value?: string | null; source?: string | null }[]
    return_binds?: { param: string; column: string }[]
  }[]
  /** conditional visibility (v1's cdn_*): a list of `{field, value}` conditions, all of which must
   *  hold for the column to appear — a condition holds when its `field` server-filter is unset, or
   *  its value matches `value` (or is in `value` when it's an array). So a set filter outside the
   *  allowed set drops the column from the grid. (A bare `{field, value}` is treated as one item.) */
  visible_when?: { field: string; value: string | string[] } | { field: string; value: string | string[] }[]
  /** Write this column only when the condition holds (opt-in, independent of `visible_when`).
   *  Backend-enforced: when set and it doesn't hold for the row, the column is written as its
   *  type-neutral value (blank / 0) and its DD default/rule is suppressed. Blank → always written. */
  write_when?: { field: string; value: string | string[] } | { field: string; value: string | string[] }[]
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
  /** Part of the row's natural key (the Columns-tab ``key`` flag; legacy ``key_columns`` is
   *  folded into this server-side). Used to stamp an action's writes with the firing row's
   *  identity so the change package groups them under that record. */
  key?: boolean
  /** Read-only when ADDING a new row (editable on edit). Honoured by the grid bulk-editor and the
   *  dialog — locked cells fall back to their read-only display in that mode. UI-only. */
  disable_on_add?: boolean
  /** Read-only when EDITING an existing row (editable on add). Per-column replacement for v1's
   *  blanket 'lock all keys on edit'. Honoured by the grid bulk-editor and the dialog. UI-only. */
  disable_on_edit?: boolean
  /** Static read-only — the operator sees the value but can't change it. Honoured by both the dialog
   *  (``fieldStateOf``) and the grid bulk-editor. UI-only. */
  disabled?: boolean
  /** Conditional read-only — locked only when every ``{field, value}`` holds against the row. Empty
   *  → the static ``disabled`` decides. Honoured by the dialog + the grid bulk-editor. UI-only. */
  disabled_when?: { field: string; value: string | string[] }[]
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
