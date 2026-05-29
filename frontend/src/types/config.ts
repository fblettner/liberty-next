// Shapes of the structured-config admin endpoints (liberty/web/admin.py — the Phase-7 builders).
import type { FrameworkEnums, JsonSchema } from '../common/SchemaForm'

/** GET /admin/config/schema — JSON Schema of the config models the builder forms render from
 *  (`sql` / `api` carry their own `$defs` for QueryDef / ColumnHint / ParamDef / EndpointDef),
 *  plus `framework_enums` — v2's port of v1's `ly_enum`-for-the-framework registry, threaded into
 *  the form via FrameworkEnumsContext so fields with `x_enum_ref` render as themed dropdowns. */
export interface ConfigSchemas {
  pool: JsonSchema
  sql: JsonSchema
  api: JsonSchema
  dictionary: JsonSchema
  menus: JsonSchema
  screens: JsonSchema
  charts: JsonSchema
  dashboards: JsonSchema
  framework_enums: FrameworkEnums
}

/** GET /admin/config/pools — the current `[pools.*]` as `{name: PoolConfig dict}`. */
export interface PoolsDoc {
  path: string
  pools: Record<string, Record<string, unknown>>
}

/** GET /admin/config/connectors/parsed — the current `[connectors.*]` (default-valued keys dropped). */
export interface ConnectorsDoc {
  path: string
  connectors: Record<string, Record<string, unknown>>
}

/** One section of the shared dictionary (top-level OR nested under `connectors.<name>`). */
export interface DictionarySection {
  entries?: Record<string, Record<string, unknown>>
  enums?: Record<string, Record<string, unknown>>
  lookups?: Record<string, Record<string, unknown>>
  /** v1's `ly_sequence` → first-class registry section. Each entry: ``{description?, connector?,
   *  query, params?}``. Dictionary entries with ``rules = "SEQUENCE"`` / ``"NN"`` carry the
   *  sequence id (a key into this map) in ``rules_values``; the SQL connector resolves it at
   *  INSERT time. */
  sequences?: Record<string, Record<string, unknown>>
}

/** GET /admin/config/dictionary/parsed — the current `dictionary.toml`, default keys dropped.
 *  `framework_enums` is top-level only (no per-connector overlay) — it overrides the bundled
 *  framework registry that drives the builder dropdowns. */
export interface DictionaryDoc {
  path: string
  dictionary: DictionarySection & {
    default_language?: string
    connectors?: Record<string, DictionarySection>
    framework_enums?: Record<string, Record<string, unknown>>
  }
}

/** Which kind of dictionary record the builder is editing. `framework_enums` is the operator
 *  override for the bundled `liberty/framework_enums.py` registry — shared scope only.
 *  `sequences` is v2's port of v1's `ly_sequence` (a named "next number" source). */
export type DictionaryKind = 'entries' | 'enums' | 'lookups' | 'sequences' | 'framework_enums'

/** One flat menu item — matches `liberty/menus/config.py::MenuItem` on the wire. Folders have
 *  no `type`; leaves carry `type` + `target`. The tree builder reconstructs the hierarchy from
 *  `parent`. `params` and `roles` round-trip as-is but are rarely set in the builder. */
export interface MenuItem {
  id: string
  parent?: string | null
  label: string
  l?: Record<string, string>
  icon?: string
  type?: 'query' | 'endpoint'
  connector?: string
  target?: string
  params?: Record<string, unknown>
  roles?: string[]
}

/** One `[menus.<app>]` block. */
export interface AppMenu {
  label?: string
  /** Whether this connector appears as a switchable app in the top app switcher (default true). */
  show_in_switcher?: boolean
  /** Default landing menu item id — when set + the caller can reach it, picking this app
   *  from the workspace picker navigates to that item's path (``/dashboard/<id>`` for a
   *  dashboard, ``/sql/<connector>/<query>`` for a query, ``/http/<c>/<endpoint>`` for an
   *  endpoint). Unset → fall through to the connector index. */
  home?: string | null
  items: MenuItem[]
}

/** GET /admin/config/menus/parsed. */
export interface MenusDoc {
  path: string
  menus: Record<string, AppMenu>
}

/** One `ParamBind` — v2's port of v1's `ly_dlg_filters`. Exactly one of `value` (literal) /
 *  `source` (column or form-field whose live value to bind) is set per row in practice. */
export interface ParamBind {
  param: string
  value?: string | null
  source?: string | null
}

/** One per-field condition predicate (matches `liberty/screens/config.py::FieldCondition`). */
export interface FieldCondition {
  field: string
  value: string | string[]
}

/** One field on a dialog tab — **Phase 2: layout-only**. Display metadata (``dd`` / ``label``
 *  / ``format`` / ``rules`` / ``rules_values`` / ``default`` / ``lookup_param_binds``) lives
 *  on the matching ``Screen.columns`` entry (``ColumnHint``) — single source of truth for
 *  both the grid editor and the dialog form. Matches `liberty/screens/config.py::ScreenField`. */
export interface ScreenField {
  name: string
  hidden?: boolean
  disabled?: boolean
  required?: boolean
  colspan?: number | null
  visible_when?: FieldCondition[]
  required_when?: FieldCondition[]
  disabled_when?: FieldCondition[]
}

/** One tab in a dialog. */
export interface ScreenTab {
  id: string
  label?: string | null
  l?: Record<string, string>
  cols?: number | null
  hide_on_add?: boolean
  hide_on_edit?: boolean
  fields?: ScreenField[]
}

/** One action attached to a dialog / screen / row — mirrors the Pydantic discriminated union
 *  in `liberty/screens/config.py`. Used by the SchemaForm-driven builder; the runtime types in
 *  `types/screens.ts` are equivalent. */
export type Action =
  | { id: string; label?: string | null; stop_on_error?: boolean; type: 'run_query'; connector?: string | null; query: string; param_binds?: ParamBind[] }
  | { id: string; label?: string | null; stop_on_error?: boolean; type: 'call_api'; connector: string; endpoint: string; param_binds?: ParamBind[] }
  | { id: string; label?: string | null; stop_on_error?: boolean; type: 'navigate'; to: string; connector?: string | null; param_binds?: ParamBind[] }
  | { id: string; label?: string | null; stop_on_error?: boolean; type: 'set_field'; target: string; value?: string | null; source?: string | null }
  | { id: string; label?: string | null; stop_on_error?: boolean; type: 'confirm'; message: string; confirm_label?: string | null; cancel_label?: string | null }
  | { id: string; label?: string | null; stop_on_error?: boolean; type: 'notify'; message: string; tone?: 'info' | 'ok' | 'warn' | 'error' }
  | { id: string; label?: string | null; stop_on_error?: boolean; type: 'refresh' }

/** Optional dialog body — the form shown for adding / editing a row. */
export interface ScreenDialog {
  title?: string | null
  tabs?: ScreenTab[]
  on_save?: Action[]
}

/** One ColumnHint — per-screen display metadata. Phase 2: single source of truth for grid +
 *  dialog (so editing a column's label / dd / format / rules / default / lookup_param_binds
 *  affects both surfaces, no duplication). Most fields are optional — empty hints just keep
 *  the column at its discovered defaults. */
export interface ColumnHint {
  name: string
  dd?: string | null
  label?: string | null
  hidden?: boolean
  /** Mark this column as part of the row's primary key. Drives the Excel-import update-vs-
   *  insert match and locks the column in the dialog's edit mode. Operators tick this in the
   *  Visual Designer's Columns tab — the backend's ``Screen.effective_key_columns()`` derives
   *  the runtime list from these flags (the explicit ``Screen.key_columns`` is an override). */
  key?: boolean
  filter?: boolean
  filter_from?: { source: string; column: string }[]
  visible_when?: { field: string; value: string | string[] } | { field: string; value: string | string[] }[] | null
  width?: number | null
  align?: 'left' | 'right' | 'center' | null
  format?: string | null
  /** Per-column rule override (v1's col_rules). When set, replaces the dictionary entry's rule
   *  for this column on this screen. Same kinds as DictionaryEntry.rules. */
  rules?: string | null
  /** The rule's argument — enum/lookup/sequence id, BOOLEAN true value. */
  rules_values?: string | null
  /** Pre-fill value on a new row (v1's col_default), used by the dialog in add mode. */
  default?: string | null
  /** ParamBinds for the column's lookup query when its rule resolves to LOOKUP. */
  lookup_param_binds?: ParamBind[]
}

/** One screen — collapses v1's table + dialog into a single entity. */
export interface Screen {
  id?: string
  label?: string | null
  description?: string | null
  connector?: string | null
  read_query: string
  update_query?: string | null
  insert_query?: string | null
  delete_query?: string | null
  /** Phase 2 — single source of truth for per-screen display metadata (grid + dialog share). */
  columns?: ColumnHint[]
  auto_load?: boolean
  /** Phase 3 — audit table name (string) replaces the legacy ``audit`` bool. When set,
   *  every successful write through this screen's update/insert/delete query mirrors a row
   *  into ``<audit_table>``. */
  audit_table?: string | null
  /** Phase 3 — per-screen SELECT row cap (was ``QueryDef.max_rows`` pre-Phase-3). */
  max_rows?: number | null
  /** Phase 3 — result columns that identify a row (was ``QueryDef.key_columns`` pre-Phase-3).
   *  Used by the Excel-import update-vs-insert match. */
  key_columns?: string[]
  editable?: boolean
  uploadable?: boolean
  dialog?: ScreenDialog | null
}

/** GET /admin/config/screens/parsed. Map of `<app>.<screen_id>` → Screen. */
export interface ScreensDoc {
  path: string
  screens: Record<string, Record<string, Screen>>
}

/** GET /admin/config/charts/parsed — `[charts.<id>]` map (default-valued keys dropped). The
 *  shape mirrors `liberty/charts/config.py::ChartConfig` + nested `ChartSpec` (`type`/`x`/`y`/…). */
export interface ChartsDoc {
  path: string
  charts: Record<string, Record<string, unknown>>
}

/** GET /admin/config/dashboards/parsed — `[dashboards.<id>]` map. Each entry is a
 *  `liberty/dashboards/config.py::Dashboard` shape (id injected from the key by the parser;
 *  default-valued keys dropped). */
export interface DashboardsDoc {
  path: string
  dashboards: Record<string, Record<string, unknown>>
}
