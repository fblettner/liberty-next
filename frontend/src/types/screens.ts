// Runtime shapes for the screens API (liberty/web/screens.py).
// The Settings-builder shapes live in types/config.ts (different concerns: that one mirrors
// the unbridled JSON-schema shape used by `SchemaForm`, this one mirrors the resolved /
// permission-pruned runtime view served by `_list_view` / `_full_view`).

/** One ParamBind — same shape used by dialog field lookups, actions (slice 4), and row menus
 *  (slice 6). Exactly one of `value` / `source` is set in practice. */
export interface ParamBind {
  param: string
  value?: string | null
  source?: string | null
}

/** One per-field predicate evaluated against the dialog's live form state. `field` is another
 *  ScreenField.name on the same dialog; `value` is the expected value (or list of allowed
 *  values). A non-empty list of predicates AND-s — they must *all* hold for the parent rule
 *  (visible / required / disabled) to fire. v2's port of v1's `ly_cdn_params`. */
export interface FieldCondition {
  field: string
  value: string | string[]
}

/** One field on a dialog tab. `name` references a column of the screen's read query. */
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
  /** Conditional visibility (v2's port of v1's col_cdn_id) — evaluated against the form. */
  visible_when?: FieldCondition[]
  /** Conditional required — when non-empty, every predicate must hold for the field to be required. */
  required_when?: FieldCondition[]
  /** Conditional disabled — when non-empty, every predicate must hold for the field to be locked. */
  disabled_when?: FieldCondition[]
}

/** One tab in a dialog (CSS-grid of fields, `cols` wide). */
export interface ScreenTab {
  id: string
  label?: string | null
  l?: Record<string, string>
  cols?: number | null
  hide_on_add?: boolean
  hide_on_edit?: boolean
  fields: ScreenField[]
}

/** One action attached to a dialog / screen / row. Discriminated union by `type` — every variant
 *  shares `id`, optional `label`, and `stop_on_error`. ParamBind-bearing variants resolve their
 *  binds at call time against the firing context (dialog form state, selected row, …). */
export type Action =
  | (ActionCommon & {
      type: 'run_query'
      connector?: string | null
      query: string
      param_binds?: ParamBind[]
    })
  | (ActionCommon & {
      type: 'call_api'
      connector: string
      endpoint: string
      param_binds?: ParamBind[]
    })
  | (ActionCommon & {
      type: 'navigate'
      to: string
      app?: string | null
      param_binds?: ParamBind[]
    })
  | (ActionCommon & {
      type: 'set_field'
      target: string
      value?: string | null
      source?: string | null
    })
  | (ActionCommon & {
      type: 'confirm'
      message: string
      confirm_label?: string | null
      cancel_label?: string | null
    })
  | (ActionCommon & {
      type: 'notify'
      message: string
      tone?: 'info' | 'ok' | 'warn' | 'error'
    })
  | (ActionCommon & { type: 'refresh' })

interface ActionCommon {
  id: string
  label?: string | null
  stop_on_error?: boolean
}

/** The form shown for add / edit — optional on a screen. */
export interface ScreenDialog {
  title?: string | null
  tabs: ScreenTab[]
  on_save?: Action[]
}

/** List-view item — what `GET /api/screens` and `GET /api/screens/{app}` return. No dialog
 *  body, no actions; just enough to render the screen list / look up a screen by `(connector,
 *  read_query)`. */
export interface ScreenListItem {
  id: string
  app: string
  label: string
  description: string | null
  connector: string         // the effective connector (explicit `connector`, else app name)
  read_query: string
  update_query: string | null
  insert_query: string | null
  delete_query: string | null
  auto_load: boolean
  audit: boolean
  editable: boolean
  uploadable: boolean
  has_dialog: boolean
}

/** Full screen detail — what `GET /api/screens/{app}/{id}` returns: list-view fields + the
 *  dialog body + the toolbar (`actions`) and right-click (`row_menu`) action lists. Each
 *  carries the slice-4 `Action` discriminated union. The runtime fires `row_menu` items on
 *  right-click on a TableView row (slice 6); `actions` (toolbar) wires up in a later slice. */
export interface ScreenDetail extends ScreenListItem {
  dialog?: ScreenDialog | null
  actions?: Action[]
  row_menu?: Action[]
}

/** `GET /api/screens` reply — apps → list view. */
export interface ScreensByApp {
  screens: Record<string, ScreenListItem[]>
}
