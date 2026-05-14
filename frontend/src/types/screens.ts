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

/** The form shown for add / edit — optional on a screen. */
export interface ScreenDialog {
  title?: string | null
  tabs: ScreenTab[]
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
 *  dialog body (when present) + actions / row_menu (placeholders until slice 4 / 6). */
export interface ScreenDetail extends ScreenListItem {
  dialog?: ScreenDialog | null
}

/** `GET /api/screens` reply — apps → list view. */
export interface ScreensByApp {
  screens: Record<string, ScreenListItem[]>
}
