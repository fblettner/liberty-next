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
 *  override for the bundled `liberty/framework_enums.py` registry — shared scope only. */
export type DictionaryKind = 'entries' | 'enums' | 'lookups' | 'framework_enums'

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

/** One field on a dialog tab (matches `liberty/screens/config.py::ScreenField`). */
export interface ScreenField {
  name: string
  dd?: string | null
  label?: string | null
  hidden?: boolean
  disabled?: boolean
  required?: boolean
  colspan?: number | null
  default?: string | null
  lookup_param_binds?: ParamBind[]
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
  auto_load?: boolean
  audit?: boolean
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
