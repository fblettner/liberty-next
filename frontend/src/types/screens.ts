// Runtime shapes for the screens API (liberty/web/screens.py).
// The Settings-builder shapes live in types/config.ts (different concerns: that one mirrors
// the unbridled JSON-schema shape used by `SchemaForm`, this one mirrors the resolved /
// permission-pruned runtime view served by `_list_view` / `_full_view`).

import type { Column } from './connectors'

/** One ParamBind — same shape used by dialog field lookups, actions (slice 4), and row menus
 *  (slice 6). Exactly one of `value` / `source` is set in practice. ``default`` is the
 *  fallback bound when *source mode* resolves to NULL / empty at call time (v2's port of v1's
 *  ``ly_act_tasks_params.map_default``); ignored in value mode. */
export interface ParamBind {
  param: string
  value?: string | null
  source?: string | null
  default?: string | null
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
  | { kind: 'enum'; values: { value: string; label: string }[]; /** enum set's name — dropdown empty-option label */ title?: string }
  | { kind: 'auto_fill'; source: 'current_date' | 'login_user' | string }
  | {
      kind: 'lookup'
      connector: string
      query: string
      value: string
      label: string
      /** the lookup's human description — dropdown empty-option label */
      title?: string
      params?: Record<string, string>
      /** Columns this lookup can flow back on a pick — the menu a column's ``return_binds`` picks
       *  from. No longer auto-mapped by dd; the explicit mapping lives on ``Column.return_binds``. */
      return_params?: string[]
      /** Extra result columns shown beside code + label in the dropdown (display only). */
      display_fields?: string[]
      /** Which display_fields get an in-dropdown facet-chip filter (subset of display_fields). */
      filter_fields?: string[]
      /** Multi-source union sources `{connector, query}` (primary first) — fetched per-connector and
       *  concatenated (UNION ALL, sorted by value). Absent for a single-query lookup. */
      sources?: { connector: string; query: string }[]
      /** The lookup's declared query params double as the **key columns** that disambiguate a
       *  non-unique ``value`` (e.g. USR_ID is only unique per USR_APPS_ID). The grid resolves the
       *  label per row by matching these same-named columns — automatic, no per-column filter_from. */
      key_columns?: string[]
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

/** One field on a dialog tab.
 *
 *  CONFIG shape (what's saved to screens.toml / edited in the Visual Designer) is just
 *  `{ name, colspan }` — a pure reference to one of the screen's columns (the SINGLE source of
 *  truth) plus where it sits on the tab grid. Every behaviour lives on the matching Column.
 *
 *  SERVED shape (what `GET /api/screens/{app}/{id}` ships to the dialog runtime) is this whole
 *  interface: the backend flattens the referenced column's resolved display metadata onto the
 *  field, so FieldRow reads everything from one object without walking the columns. All keys
 *  beyond `name` / `colspan` are server-populated and read-only on the client. */
export interface ScreenField {
  name: string
  colspan?: number | null
  // ── below: SERVER-RESOLVED from the referenced column; never authored in the field config ──
  dd?: string | null
  label?: string | null
  format?: string | null
  hidden?: boolean
  disabled?: boolean
  required?: boolean
  default?: string | null
  /** Server-resolved display rule (BOOLEAN / ENUM / LOOKUP) from the column's rule, or null. */
  rule?: DisplayRule | null
  lookup_param_binds?: ParamBind[]
  /** LOOKUP return → target-field fills: on a pick, write the picked row's ``param`` column into
   *  the form field named ``column``. */
  return_binds?: { param: string; column: string }[]
  /** Conditional forced defaults: when sibling `field` == `value`, this field is set to `default`
   *  and locked. First matching rule wins. Reactive on the live form. */
  default_when?: { field: string; value: string | string[]; default: string; lock?: boolean }[]
  /** Conditional rule overrides: when sibling `field` == `value`, render with `rule` (resolved, or
   *  null → plain) instead of the field's base `rule`. First match wins; reactive on the form. Each
   *  entry carries its OWN `lookup_param_binds` + `return_binds` (independent of the column's base
   *  binds) so two rules on the same discriminator bind different params. */
  rules_when?: {
    field: string; value: string | string[]; rule: DisplayRule | null
    lookup_param_binds?: ParamBind[]
    return_binds?: { param: string; column: string }[]
  }[]
  /** Conditional visibility — evaluated against the form. */
  visible_when?: FieldCondition[]
  /** Conditional required — when non-empty, every predicate must hold for the field to be required. */
  required_when?: FieldCondition[]
  /** Conditional disabled — when non-empty, every predicate must hold for the field to be locked. */
  disabled_when?: FieldCondition[]
  /** Read-only when ADDING a new row (ORed into `disabled` when mode='add'). */
  disable_on_add?: boolean | null
  /** Read-only when EDITING an existing row (ORed into `disabled` when mode='edit'). */
  disable_on_edit?: boolean | null
}

/** Common to every tab kind — title + per-mode hide flags + translations + per-tab action
 *  buttons. v2's port of v1's ``ly_dlg_col col_component='InputAction'`` rows — buttons placed
 *  inside a tab (e.g. NOMAJDE Role dialog "Roles" tab carries Import Security + Merge Roles
 *  alongside its nested table). Lives on every tab kind so a nested_table can also carry buttons. */
interface TabCommon {
  id: string
  label?: string | null
  l?: Record<string, string>
  hide_on_add?: boolean
  hide_on_edit?: boolean
  actions?: Action[]
}

/** Plain field-grid tab — the original kind. `cols` wide CSS grid; `type` defaults to "form"
 *  when omitted (matches the backend's discriminator default). Each field is a pure reference to
 *  one of the screen's columns (see ScreenField). */
export interface FormTab extends TabCommon {
  type?: 'form'
  cols?: number | null
  fields: ScreenField[]
}

/** A child-record FORM embedded inline in this tab, REFERENCE-ONLY — the form-shaped sibling of
 *  NestedTableTab. Reuses an existing screen's form (`form_screen`): that screen owns the
 *  read/update/insert queries AND the fields, so this tab carries only `param_binds` wiring the
 *  parent's values (typically its PK) into the reused form. At runtime the reused form renders
 *  inline and is saved together with the parent. (The old inline mode — own fields/queries on the
 *  tab — was removed: it was a second place to configure columns.) */
export interface NestedFormTab extends TabCommon {
  type: 'nested_form'
  /** Id of the existing screen whose form this tab reuses (its read/update/insert queries + fields). */
  form_screen: string
  connector?: string | null
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

/** Predicate evaluated by an `IfAction`. v2's port of v1's `ly_conditions` — flat single-clause
 *  (a multi-clause AND/OR predicate will land in a later slice). `source` is a dotted-path
 *  reference into the chain context (same syntax `ParamBind.source` accepts inside a `ChainAction`)
 *  or a plain form-field name; `operator` is the comparison; `value` is the literal to compare
 *  against (only used by equality / numeric operators). */
export interface Condition {
  source: string
  operator: 'equals' | 'not_equals' | 'has_rows' | 'no_rows' | 'truthy' | 'falsy' | 'greater_than' | 'less_than'
  value?: string | null
}

/** One action attached to a dialog / screen / row. Discriminated union by `type` — every variant
 *  shares `id`, optional `label`, and `stop_on_error`. ParamBind-bearing variants resolve their
 *  binds at call time against the firing context (dialog form state, selected row, …) — or, when
 *  the action lives inside a `ChainAction`, against the chain's accumulated context.
 *
 *  **Workflow variants** (`chain` / `if` / `loop` / `return`) — slice B (Phase 6 4d). v2's port
 *  of v1's named-action workflow shape; let one button run a sequence of typed steps with shared
 *  context, branching via IF, iterating via LOOP, and writing values back to the caller via
 *  RETURN. See `ChainAction.steps` for the chain-context semantics. */
export type Action =
  | (ActionCommon & PromptableAction & {
      type: 'run_query'
      connector?: string | null
      query: string
      param_binds?: ParamBind[]
      /** When true, store the action's rows in the chain context under this step's `id` as
       *  `{rows, first_row, success}` so later steps can reference them via
       *  `ParamBind {source: '<id>.first_row.<col>'}`. Only meaningful inside a `ChainAction`. */
      bind_result?: boolean
    })
  | (ActionCommon & PromptableAction & {
      type: 'call_api'
      connector: string
      endpoint: string
      param_binds?: ParamBind[]
      bind_result?: boolean
      /** When true AND fired from a change-tracked screen, record this call in the change package
       *  so a promotion bundle re-runs it on the target. Off by default (replay re-fires side
       *  effects + can't be drift-checked). */
      change_replay?: boolean
    })
  | (ActionCommon & PromptableAction & {
      /** Invoke a server-side plugin callable (`module:function` — the same entry points nomaflow
       *  runs as python job steps). `param_binds` become its keyword args (coerced server-side to
       *  each param's annotated type). With `bind_result`, the callable's return lands under this
       *  step's `id` as `{rows, first_row, success}`. */
      type: 'call_plugin'
      callable: string
      param_binds?: ParamBind[]
      bind_result?: boolean
      /** When true AND fired from a change-tracked screen, record this call in the change package
       *  so a promotion bundle re-runs the callable on the target. Off by default. */
      change_replay?: boolean
    })
  | (ActionCommon & PromptableAction & {
      /** Run a SHARED action (defined once in actions.toml) by id. The runner inlines its steps
       *  and runs them against the firing context; `param_binds` seed the shared action's
       *  `INPUT.<param>` so its steps can read caller-provided values. A `prompt_fields` prompt
       *  (like chain/call_api) fires first → its values merge into INPUT for the shared steps. */
      type: 'call_action'
      ref: string
      param_binds?: ParamBind[]
      /** Replay the shared action's call_api/call_plugin steps on apply (its SQL writes always
       *  replay). Set here on the screen action — no need to edit the shared action's steps. */
      change_replay?: boolean
    })
  | (ActionCommon & PromptableAction & {
      type: 'navigate'
      to?: string | null     // target query name on `connector` (unset when `screen` is set)
      connector?: string | null  // blank → the firing screen's effective connector
      screen?: string | null  // target screen id → opens /screen/<connector>/<screen>
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
  | (ActionCommon & PromptableAction & {
      /** One button → N inner steps run sequentially with a shared chain context. Each
       *  `run_query` / `call_api` with `bind_result` lands its rows under its `id` so later
       *  steps reference them. `prompt_fields` ride on the chain (one prompt per fire). */
      type: 'chain'
      steps?: Action[]
    })
  | (ActionCommon & {
      /** Conditional branching. `condition` evaluates against the chain context; true →
       *  `then_steps` runs, false → `else_steps` runs. Either branch may be empty. */
      type: 'if'
      condition: Condition
      then_steps?: Action[]
      else_steps?: Action[]
    })
  | (ActionCommon & {
      /** Iterate over an array source. The current element binds under `loop.<field>` for
       *  the nested `steps` to reference via `ParamBind {source: 'loop.<field>'}`. */
      type: 'loop'
      source: string
      steps?: Action[]
    })
  | (ActionCommon & {
      /** Write values back into the caller's form fields. v1's port of `evt_type='RETURN'`. */
      type: 'return'
      bindings?: Record<string, string>
    })

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
  /** View-only screen — the runtime hides every mutating control: the dialog's Save / Delete /
   *  Duplicate, the grid's Add / Delete-selected + inline editing, paste / import, and the same
   *  controls in nested-table editors. Stronger than `disable_add` (which only blocks inserts). */
  read_only?: boolean
  /** Prevent creating NEW records on this screen even when `insert_query` is set (edit/delete of
   *  existing rows stays allowed). Hides the Add button + dialog Add entry, and disables
   *  add-row / duplicate-row / paste / import in the grid bulk-editor. */
  disable_add?: boolean
  /** Default tanstack-table grouping for this screen — column name(s) the grid groups by on
   *  first open. Operators can ungroup / regroup from the Group control; this only seeds the
   *  initial state. Empty = no default grouping (flat rows). */
  initial_group_by?: string[]
  /** Parent / child hierarchy config — when present, the TableView gains a third view toggle
   *  (Tree) alongside Table / Chart. Walked at render time: each row is a node, ``parent``
   *  column points at another row's ``child`` value, ``label`` is the displayed text. Rows
   *  with an unresolvable parent become roots. */
  treeview?: ScreenTreeview | null
  /** Saved chart id (charts.toml) — when set, the TableView's Chart tab pre-fills its spec
   *  from that chart instead of the empty session default. Null = keep the localStorage
   *  session-seeded behaviour from before Phase F landed. */
  chart_id?: string | null

  has_dialog: boolean
  has_row_menu: boolean
  has_actions: boolean
  /** Phase 1 — true when ``Screen.columns`` is non-empty. The TableView fetches the screen detail
   *  whenever any ``has_*`` flag is true; this one lets it pull the body purely for the column
   *  hints on screens that have no dialog / row_menu / actions. */
  has_columns?: boolean
  /** True when ``Screen.views`` is non-empty — the grid offers shared/named views. Lets the
   *  TableView fetch the body (which carries ``views``) even on a views-only screen. */
  has_views?: boolean
  /** True when ``Screen.summary`` is set — the TableView shows a Summary toggle (server-aggregated
   *  parent rows with expandable detail) and fetches the body for the dimension config. */
  has_summary?: boolean
  /** Server-aggregated summary config — group-by dimensions + count, with lazy-loaded detail.
   *  Surfaced on the list view so the toggle + dimension list are available without the full body. */
  summary?: ScreenSummary | null
  /** Row-level value diff — when set, each row expands to its field-level BEFORE/AFTER, parsed
   *  in flight from the named SQL column. */
  value_diff?: ScreenValueDiff | null
  /** ``dictionary_key → column_name`` map built from the screen's column hints — surfaced on the
   *  list view so dashboard filters can resolve a filter's ``dictionary_key`` to this screen's
   *  matching column name without fetching the full body. Empty / missing when the screen lists
   *  no columns or none carry a ``dd`` — those screens can't be filtered, same as before. */
  dd_map?: Record<string, string>
  /** Promoted-from-ctx-menu row-click target. When this screen has no own ``dialog`` and the
   *  user clicks a row, the frontend opens *the named screen's* dialog as a modal — the
   *  ``row_click_binds`` map this row's columns to the target read_query's params, the
   *  resolved row populates the target's dialog. v2's port of v1's "Display Properties"
   *  pattern on ``ly_ctxmenus`` (NOMASX1's security_users → security_users_prop). */
  row_click_screen?: string | null
  row_click_connector?: string | null
  row_click_binds?: ParamBind[]
  /** SPA route opened on row click instead of a sibling-screen dialog. The escape hatch for
   *  screens that drill into a hand-written React page (live-streamed logs, custom charts,
   *  things SQL can't render). Use ``{column_name}`` placeholders to interpolate the clicked
   *  row's columns; values are URL-encoded. Example: ``/nomaflow/runs/{id}``. Wins over
   *  ``row_click_screen`` when both are set — explicit route is the more specific intent. */
  row_click_route?: string | null
}

/** Full screen detail — what `GET /api/screens/{app}/{id}` returns: list-view fields + the
 *  dialog body + the toolbar (`actions`) and right-click (`row_menu`) action lists. Each
 *  carries the slice-4 `Action` discriminated union. The runtime fires `row_menu` items on
 *  right-click on a TableView row (slice 6); `actions` (toolbar) wires up in a later slice. */
/** One sheet inside a workbook export — runs a query, writes its rows to a tab.
 *
 *  Two layout modes, controlled by ``split_by``:
 *   - Blank → one worksheet, name = ``name`` (with ``{{split_value}}`` substituted to the
 *     workbook's group key).
 *   - Set → the query's rows are partitioned by that column into N worksheets (one per
 *     distinct value, first-seen order). ``name`` may reference ``{{sheet_value}}`` for the
 *     partition value. v2's port of v1's ``tbl_sheet``. */
export interface SheetSpec {
  name: string
  connector?: string | null
  query: string
  split_by?: string | null
  param_binds?: ParamBind[]
}

/** Multi-file / multi-sheet xlsx export config — v2's port of v1's ``tbl_workbook`` /
 *  ``tbl_sheet``. Triggered from the TableView's "Export workbooks" button when set; the
 *  backend ``POST /api/screens/{app}/{id}/export`` streams a single .xlsx or a .zip. */
/** Parent / child hierarchy config for the TableView's Tree mode. Walked at render time:
 *  each row is a node, ``parent`` column value points at another row's ``child`` value,
 *  ``label`` is the displayed text. Rows with unresolvable parents become roots. */
export interface ScreenTreeview {
  parent: string
  child: string
  label: string
  /** Sibling sort order — column name(s), priority order. Empty = alphabetic on ``label``. */
  order_by?: string[]
}

export interface WorkbookExport {
  /** Column on the screen's read query whose distinct values produce one xlsx per group.
   *  Blank → a single xlsx file. */
  split_by?: string | null
  sheets: SheetSpec[]
  /** Template for each xlsx file name. ``{{split_value}}`` + ``{{screen}}`` substitution. */
  file_name_template?: string | null
  /** Name of the .zip when several workbooks are produced. */
  archive_name?: string | null
}

export interface ScreenDetail extends ScreenListItem {
  dialog?: ScreenDialog | null
  actions?: Action[]
  row_menu?: Action[]
  /** Workbook export configuration. When set, the TableView shows an "Export workbooks"
   *  button. */
  export?: WorkbookExport | null
  /** Row-level lifecycle hooks. Fire after a row is mutated — by either path: dialog Save
   *  in the matching mode, *or* the inline grid's Save button. v1 ``FormsTable`` evt 2/3
   *  map to ``on_insert`` / ``on_delete``; ``on_update`` is new in v2 (no v1 source). */
  on_insert?: Action[]
  on_update?: Action[]
  on_delete?: Action[]
  /** Fires when a row is DUPLICATED (the dialog's Duplicate button → Save). Runs instead of
   *  on_insert for the clone; the firing context exposes the source record under ``SOURCE_<col>``
   *  keys so actions can copy related-table rows (roles, menus…) from the original to the new row. */
  on_duplicate?: Action[]
  /** Capture every write on this screen into the connector's change package (Settings → Changes).
   *  Drives whether the dialog tags its write-hook action calls for change capture. */
  change_tracked?: boolean
  /** Display label grouping the screen's changes in the package view (e.g. user / role). */
  change_entity?: string | null
  /** Phase 1 — per-screen resolved column hints (label / format / hidden / filter / filter_from /
   *  visible_when / rule / width / align / dd). Same shape :class:`Column` carries on the SQL
   *  endpoint's ``result.columns``, so the TableView can swap this list in transparently. Only
   *  set when ``Screen.columns`` is non-empty; when absent the TableView falls back to the SQL
   *  endpoint's resolved columns from the query level. */
  columns?: Column[]
  /** Related 1:1 tables whose columns are edited inline on this screen and written back on Save.
   *  A column joins a group via its ``group`` field; the save path splits the row's writes per
   *  table using these definitions. */
  column_groups?: ColumnGroup[]
  /** Named shared grid views (grid formats) for this screen — saved sets of visible columns,
   *  sort, grouping and page size, offered to ALL users from the grid's view picker. One may
   *  be the default. Users layer their own per-user views on top (stored server-side). */
  views?: ScreenView[]
}

/** One sort directive within a shared grid view. */
export interface ScreenViewSort {
  column: string
  desc?: boolean
}

/** A named, shared grid view (grid format) authored in the Screen editor. Available to all
 *  users (read-only). At most one per screen carries ``default``. */
export interface ScreenView {
  name: string
  /** Open the grid with this view by default (only one view per screen sets it). */
  default?: boolean
  /** Visible columns, in display order. Empty = all columns in the screen's column order. */
  columns?: string[]
  /** Default sort — column(s) + direction, applied in order. */
  sort?: ScreenViewSort[]
  /** Default tanstack grouping column(s), nested in order. */
  group_by?: string[]
  /** Rows per page. Null/undefined = the screen / grid default. */
  page_size?: number | null
}

/** One grouping dimension of a screen's summary (aggregate) view. */
export interface ScreenSummaryDimension {
  column: string
  /** Bucket a date/timestamp column before grouping (day = roll a day's changes into one row). */
  bucket?: 'day' | 'month' | 'year' | null
}

/** Server-aggregated summary view — parent rows are GROUP BY <dimensions> + COUNT(*), each
 *  expandable to its underlying rows (lazily fetched). Replaces a materialised rollup table. */
export interface ScreenSummary {
  dimensions: ScreenSummaryDimension[]
  /** Header for the COUNT(*) column. */
  count_label?: string
}

/** Row-level value diff — expand a row to its field-level BEFORE/AFTER, parsed in flight from a
 *  column holding a DML statement (no separate values table needed). */
export interface ScreenValueDiff {
  sql_column: string
  operation_column?: string | null
}

/** A 1:1 related-table write-back target. The main read query JOINs the related table so its
 *  columns render inline (grid + dialog); on Save the columns tagged with this group's id are
 *  written through ``update_query`` / ``insert_query``, linked to the parent row by ``param_binds``.
 *  Update-vs-insert is decided by whether ``key_columns`` came back non-null from the JOIN. */
export interface ColumnGroup {
  id: string
  label?: string | null
  connector?: string | null
  update_query: string
  insert_query?: string | null
  /** Removes the related row when the main row is deleted (FK-safe: child first). Blank → leave it. */
  delete_query?: string | null
  key_columns?: string[]
  param_binds?: ParamBind[]
  /** Insert the related row on every Add even when no field was filled (FK + server defaults
   *  populate it) — for a mandatory 1:1 companion. Off → only insert when a field has a value. */
  insert_on_add?: boolean
}

/** One reusable shared action (actions.toml `[actions.<id>]`) — a named chain referenced by a
 *  screen's `call_action`. The same typed steps a screen uses; served by `GET /api/actions`. */
/** A shared action's declared input — bound at the call site (`call_action.param_binds`), read by
 *  steps as `INPUT.<name>`. The `default` lives here (used when the caller doesn't bind it). */
export interface ActionParam {
  name: string
  label?: string | null
  default?: string | null
  description?: string | null
}

export interface SharedAction {
  id: string
  label?: string | null
  description?: string | null
  params?: ActionParam[]
  prompt_fields?: PromptField[]
  steps?: Action[]
}

/** `GET /api/screens` reply — apps → list view. */
export interface ScreensByApp {
  screens: Record<string, ScreenListItem[]>
}
