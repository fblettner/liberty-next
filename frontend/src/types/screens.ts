// Runtime shapes for the screens API (liberty/web/screens.py).
// The Settings-builder shapes live in types/config.ts (different concerns: that one mirrors
// the unbridled JSON-schema shape used by `SchemaForm`, this one mirrors the resolved /
// permission-pruned runtime view served by `_list_view` / `_full_view`).

import type { Column } from './connectors'

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

/** A column-like display rule resolved server-side from a dictionary entry — same shape the
 *  read-result `Column.rule` carries. Used on a PromptField so the prompt sub-dialog can pick
 *  the right widget without round-tripping the dictionary lookup. */
export type DisplayRule =
  | { kind: 'boolean'; true_value: string; false_value?: string }
  | { kind: 'enum'; values: { value: string; label: string }[] }
  | {
      kind: 'lookup'
      connector: string
      query: string
      value: string
      label: string
      params?: Record<string, string>
      /** v1's ly_lkp_params with lkp_dir='OUT' — extra dd_ids the picked row writes back to
       *  other form fields / grid cells beyond the headline ``value`` / ``label`` columns. */
      return_params?: string[]
    }

/** One input field on the *prompt sub-dialog* shown before an action with `prompt_fields` fires.
 *  v2's port of v1's `ly_act_params`. Shape mirrors `ScreenField`, but with two practical
 *  differences:
 *
 *  - **No backing column** — `name` is the prompt's own key (becomes the ParamBind `source`
 *    target). The widget comes from `dd` / `format`, resolved server-side on the screens API
 *    into `label` / `format` / `rule` keys (same shape a Column carries).
 *  - **No grid context** — the prompt dialog is its own modal; `colspan` controls the grid
 *    spread inside the prompt.
 *
 *  Conditional rules evaluate against the prompt dialog's own form state, not the parent's. */
export interface PromptField {
  name: string
  dd?: string | null
  label?: string | null
  format?: string | null
  /** Server-resolved display rule (BOOLEAN / ENUM / LOOKUP). Absent when the entry has no
   *  display-relevant rule (or no dd). */
  rule?: DisplayRule | null
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

/** One field on a dialog tab. `name` references a column of the screen's read query. */
export interface ScreenField {
  name: string
  dd?: string | null
  label?: string | null
  /** Format override (v1's `col_type`) — e.g. force a column to render as a date even when the
   *  dictionary's entry has no format. Wins over the read-column's format on this dialog. */
  format?: string | null
  hidden?: boolean
  disabled?: boolean
  required?: boolean
  colspan?: number | null
  default?: string | null
  /** Per-field rule override (v1's `col_rules`). When set, replaces the dictionary entry's rule
   *  for this field on this dialog. Same kinds as DictionaryEntry.rules. */
  rules?: string | null
  /** The rule's argument (v1's `col_rules_values`) — enum/lookup/sequence id, etc. */
  rules_values?: string | null
  /** Server-resolved display rule from `rules` + `rules_values` (BOOLEAN / ENUM / LOOKUP), or
   *  the dictionary entry's rule when no per-field override is set. Frontend prefers this over
   *  the read-column's `rule` so a screen-level LOOKUP override actually renders as a dropdown. */
  rule?: DisplayRule | null
  lookup_param_binds?: ParamBind[]
  /** Conditional visibility (v2's port of v1's col_cdn_id) — evaluated against the form. */
  visible_when?: FieldCondition[]
  /** Conditional required — when non-empty, every predicate must hold for the field to be required. */
  required_when?: FieldCondition[]
  /** Conditional disabled — when non-empty, every predicate must hold for the field to be locked. */
  disabled_when?: FieldCondition[]
}

/** Common to every tab kind — title + per-mode hide flags + translations + per-tab action
 *  buttons. v2's port of v1's ``ly_dlg_col col_component='InputAction'`` rows — buttons placed
 *  inside a tab (e.g. NOMAJDE Role dialog "Roles" tab carries Import Security + Merge Roles
 *  alongside its nested table). Lives on every tab kind so a nested_form / nested_table can
 *  also carry buttons. */
interface TabCommon {
  id: string
  label?: string | null
  l?: Record<string, string>
  hide_on_add?: boolean
  hide_on_edit?: boolean
  actions?: Action[]
}

/** Plain field-grid tab — the original kind. `cols` wide CSS grid; `type` defaults to "form"
 *  when omitted (matches the backend's discriminator default). */
export interface FormTab extends TabCommon {
  type?: 'form'
  cols?: number | null
  fields: ScreenField[]
}

/** A child-record form embedded inline in this tab (v2's port of v1's "FormsDialog inside a
 *  FormsDialog"). The parent's PK is bound into the nested `read_query` via `param_binds`;
 *  the linked row (if any) populates the fields, and saving fires `update_query` (or
 *  `insert_query` when the row didn't exist yet). All on `connector` (default: parent's). */
export interface NestedFormTab extends TabCommon {
  type: 'nested_form'
  connector?: string | null
  read_query: string
  update_query?: string | null
  insert_query?: string | null
  cols?: number | null
  fields: ScreenField[]
  param_binds?: ParamBind[]
}

/** A related-rows TableView embedded inline in this tab (v1's "FormsTable inside a FormsDialog").
 *  References a v2 screen by id; the nested TableView re-uses that screen's read query +
 *  column hints + dialog + actions, with parent values bound into the read query via
 *  `param_binds`. Used for activity logs, audit trails, sub-collections. */
export interface NestedTableTab extends TabCommon {
  type: 'nested_table'
  screen: string
  connector?: string | null
  param_binds?: ParamBind[]
}

/** Discriminated union: a tab is one of the three kinds. The `type` field discriminates;
 *  TypeScript's narrowing on `tab.type` gives compile-time exhaustiveness checks. */
export type ScreenTab = FormTab | NestedFormTab | NestedTableTab

/** Shared by the three ParamBind-bearing variants (`run_query`, `call_api`, `navigate`) — opts
 *  the action into the *prompt-before-fire* flow. When `prompt_fields` is non-empty the runtime
 *  opens a sub-dialog before this action runs, collects the user's input, and merges those
 *  values into the chain's resolution context — every later ParamBind whose `source` matches
 *  a prompt field's `name` reads from the prompt instead of the parent context. v2's port of
 *  v1's `ly_act_params`. */
interface PromptableAction {
  prompt_fields?: PromptField[]
  prompt_title?: string | null
  prompt_l?: Record<string, string>
  prompt_cols?: number | null
  prompt_submit_label?: string | null
}

/** One action attached to a dialog / screen / row. Discriminated union by `type` — every variant
 *  shares `id`, optional `label`, and `stop_on_error`. ParamBind-bearing variants resolve their
 *  binds at call time against the firing context (dialog form state, selected row, …). */
export type Action =
  | (ActionCommon & PromptableAction & {
      type: 'run_query'
      connector?: string | null
      query: string
      param_binds?: ParamBind[]
    })
  | (ActionCommon & PromptableAction & {
      type: 'call_api'
      connector: string
      endpoint: string
      param_binds?: ParamBind[]
    })
  | (ActionCommon & PromptableAction & {
      type: 'navigate'
      to: string             // target query name on `connector`
      connector?: string | null  // blank → the firing screen's effective connector
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

/** The form shown for add / edit — optional on a screen.
 *
 *  Lifecycle hooks (all optional):
 *   - ``on_load``  — fires after the dialog opens + row data is loaded (edit) or default
 *     values are seeded (add).
 *   - ``on_save``  — fires after the main update/insert succeeds. v1 ``FormsDialog`` evt 1.
 *   - ``on_cancel`` — fires when the user closes without saving (Cancel / click-outside).
 */
export interface ScreenDialog {
  title?: string | null
  tabs: ScreenTab[]
  on_load?: Action[]
  on_save?: Action[]
  on_cancel?: Action[]
}

/** List-view item — what `GET /api/screens` and `GET /api/screens/{app}` return. No dialog /
 *  actions / row_menu body, just enough for the frontend's screen list + the `has_*` flags
 *  that decide whether to fetch the full body. */
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
  /** Phase 3 — kept as a bool for back-compat with frontend code that checks "is this screen
   *  audited"; derived server-side from ``audit_table`` presence. */
  audit: boolean
  /** Phase 3 — the audit table name. Frontend doesn't usually need it (the SQL connector
   *  does the mirror via the route layer), but surfaced for completeness. */
  audit_table?: string | null
  /** Phase 3 — per-screen SELECT row cap. */
  max_rows?: number | null
  /** Phase 3 — result columns that identify a row (used by Excel import match-by-key). */
  key_columns?: string[]
  editable: boolean
  uploadable: boolean
  has_dialog: boolean
  has_row_menu: boolean
  has_actions: boolean
  /** Phase 1 — true when ``Screen.columns`` is non-empty. The TableView fetches the screen detail
   *  whenever any ``has_*`` flag is true; this one lets it pull the body purely for the column
   *  hints on screens that have no dialog / row_menu / actions. */
  has_columns?: boolean
  /** Promoted-from-ctx-menu row-click target. When this screen has no own ``dialog`` and the
   *  user clicks a row, the frontend opens *the named screen's* dialog as a modal — the
   *  ``row_click_binds`` map this row's columns to the target read_query's params, the
   *  resolved row populates the target's dialog. v2's port of v1's "Display Properties"
   *  pattern on ``ly_ctxmenus`` (NOMASX1's security_users → security_users_prop). */
  row_click_screen?: string | null
  row_click_connector?: string | null
  row_click_binds?: ParamBind[]
}

/** Full screen detail — what `GET /api/screens/{app}/{id}` returns: list-view fields + the
 *  dialog body + the toolbar (`actions`) and right-click (`row_menu`) action lists. Each
 *  carries the slice-4 `Action` discriminated union. The runtime fires `row_menu` items on
 *  right-click on a TableView row (slice 6); `actions` (toolbar) wires up in a later slice. */
export interface ScreenDetail extends ScreenListItem {
  dialog?: ScreenDialog | null
  actions?: Action[]
  row_menu?: Action[]
  /** Row-level lifecycle hooks. Fire after a row is mutated — by either path: dialog Save
   *  in the matching mode, *or* the inline grid's Save button. v1 ``FormsTable`` evt 2/3
   *  map to ``on_insert`` / ``on_delete``; ``on_update`` is new in v2 (no v1 source). */
  on_insert?: Action[]
  on_update?: Action[]
  on_delete?: Action[]
  /** Phase 1 — per-screen resolved column hints (label / format / hidden / filter / filter_from /
   *  visible_when / rule / width / align / dd). Same shape :class:`Column` carries on the SQL
   *  endpoint's ``result.columns``, so the TableView can swap this list in transparently. Only
   *  set when ``Screen.columns`` is non-empty; when absent the TableView falls back to the SQL
   *  endpoint's resolved columns from the query level. */
  columns?: Column[]
}

/** `GET /api/screens` reply — apps → list view. */
export interface ScreensByApp {
  screens: Record<string, ScreenListItem[]>
}
