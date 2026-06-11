// Custom editor for one Screen — replaces the generic SchemaNavigator with a tabbed view that
// surfaces the dialog structure (tabs / fields / lookup_param_binds) at a glance, addressing the
// "all on one page, hard to understand" feedback after slice 2 landed.
//
// Tabs across the top: General · Queries · Dialog · Actions · Row menu.
// - General / Queries use SchemaForm over picked subsets of the Screen schema, so the same
//   field-level enums, descriptions and validation as the form view kick in.
// - Dialog uses a custom layout: a vertical strip of dialog tabs on the left, the selected tab's
//   properties + a list of fields on the right. Each field row is an expandable accordion (no
//   drill-down breadcrumbs). lookup_param_binds nest inline under the expanded field.
// - Actions / Row menu show a "Coming in slice 4/6" placeholder so the UI shape is stable when
//   those slices land.
//
// All mutations go through `onChange(nextScreen)` — the parent (ScreensBuilder) owns the dirty
// flag and the save call. `screenSchema` carries the full `$defs` map so we can pick out
// `ScreenDialog` / `ScreenTab` / `ScreenField` / `ParamBind` for the per-section sub-schemas.
import { useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, ChevronRight, ChevronDown } from 'lucide-react'
import {
  Banner, Button, Field, FrameworkEnumsContext, Input, Row, SchemaForm, SchemaNavigator, SearchSelect, Select, Stack, useModals,
  type FrameworkEnums, type JsonSchema, type SearchSelectOption,
} from '../../common'
import ParamBindList, { type ParamBind } from './ParamBindList'
import {
  builtinSourceOptions, lookupQueryParamNames, mergeCandidates, screenReadColumnOptions, targetParamOptions,
} from './actionCandidates'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import { pickSchemaProperties } from './connectorTables'
import { EditQueryModal } from './EditQueryModal'
import { EditQueryButton, CloneQueryButton, AddQueryButton } from './EditQueryButton'
import ColumnGroupsEditor from './ColumnGroupsEditor'
import ScreenVisualBuilder from './ScreenVisualBuilder'
import ActionTreeView from './ActionTreeView'
import ActionEditorModal from './ActionEditorModal'
import ExportEditor from './ExportEditor'
import type { ActionPath } from './actionPath'
import type { Column, QueryResult } from '../../types/connectors'
import type { DictionaryDoc } from '../../types/config'
import { api } from '../../api/client'

type Row = Record<string, unknown>

// Which top-level Screen properties belong on which inner tab. ``connector`` + the four CRUD
// query refs are rendered as custom SearchSelects (driven by the workspace's connector list
// and the selected connector's queries) — the rest fall through to SchemaForm for free.
// Phase 3 — ``audit_table`` (str) replaces ``audit`` (bool); ``max_rows`` lives in General.
// ``key_columns`` was here too, but the row's identifying columns are now ticked per-column
// via the Columns tab (``ColumnHint.key``) — the runtime's ``Screen.effective_key_columns()``
// derives the list from those flags. The explicit ``key_columns`` field stays on the schema
// for hand-edited overrides, but it doesn't render here (less duplication, one place to set).
// Row-click fields are NOT in GENERAL_FORM_KEYS — they get their own section below the schema
// form, behind a mode picker. The Pydantic model has four flat fields (row_click_screen +
// _connector + _binds for the "open a sibling Screen as a modal" path, row_click_route for the
// "navigate to a React page" path); rendering them flat confuses operators (you'd see 4 fields
// with no hint that some are mutually exclusive). The picker turns it into one decision per
// screen — "what happens when a row is clicked" — then reveals only the relevant fields.
const GENERAL_FORM_KEYS = [
  'label', 'description', 'audit_table',
  // Change-package capture (Settings → Packages): toggle on to record every write on this
  // screen into the app's active change package, with ``change_entity`` as its grouping label
  // and ``post_apply`` selecting the run-once steps this screen's changes need on promotion.
  'change_tracked', 'change_entity', 'post_apply',
  'max_rows', 'auto_load', 'editable', 'uploadable', 'disable_add',
  // Default tanstack-table grouping — column(s) the grid groups by on first open. Surfaces
  // here as a multi-select bound to SCREEN_COLUMNS (the read query's columns), the same
  // ``x_enum_ref`` mechanism the Columns tab uses to pick column names.
  'initial_group_by',
  // Saved chart id (charts.toml) — pre-fills the Chart tab when this screen opens. Bound
  // to the CHART_IDS framework enum, populated by an effect below that fetches
  // /admin/config/charts/parsed.
  'chart_id',
] as const

// Known SPA-route targets the row-click can drill into. Hand-curated because these are
// React-defined routes (not discoverable via the API), so the dropdown ships with the frontend
// and grows as new ones are added (NOMAFLOW-UI chunk 5 dashboards, future detail pages…).
// ``allowCustom`` on the SearchSelect lets an operator type one that isn't in the list yet —
// the typed value becomes the route verbatim, so a typo'd path won't silently fail at click time
// (the Screen.row_click_route validator already catches missing column placeholders at save).
const ROW_CLICK_ROUTE_OPTIONS: SearchSelectOption[] = [
  { value: '/nomaflow/runs/{id}', label: 'Nomaflow run detail', mono: '/nomaflow/runs/{id}' },
]

type RowClickMode = 'none' | 'dialog' | 'route'
// Phase 3 — Screen.columns drives both grid + dialog display (single source of truth). Edited
// via SchemaNavigator on a dedicated tab.
const COLUMNS_KEYS = ['columns'] as const

type TabKey = 'general' | 'queries' | 'columns' | 'dialog' | 'actions' | 'rowmenu' | 'export'
const TAB_ORDER: TabKey[] = ['general', 'queries', 'columns', 'dialog', 'actions', 'rowmenu', 'export']

// ── styled bits ─────────────────────────────────────────────────────────────
const TabsBar = styled.div`display: flex; gap: 4px; border-bottom: 1px solid ${colors.border}; margin-bottom: 14px;`
const TabBtn = styled.button<{ $active?: boolean }>`
  height: 34px; padding: 0 14px; border: none; border-bottom: 2px solid transparent; background: transparent;
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  border-bottom-color: ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  font-weight: ${({ $active }) => ($active ? 600 : 400)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; }
`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; line-height: 1.5; margin-bottom: 10px;`
const GroupsHr = styled.div`border-top: 1px solid ${colors.border}; margin: 18px 0 14px;`
// Collapsible hook section on the Actions tab — header (chevron · heading · count) + body.
const HookSection = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; margin-bottom: 8px; background: ${colors.bg.input};
  & > .head { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; cursor: pointer;
    background: none; border: none; padding: 10px 12px; color: ${colors.text.primary};
    font-size: ${fontSize.sm}; font-weight: 600; font-family: ${fonts.sans}; }
  & > .head:hover { background: var(--hover-subtle); }
  & > .head .title { flex: 1; }
  & > .head .count { font-size: ${fontSize.micro}; color: ${colors.text.muted}; min-width: 18px; text-align: center; }
  & > .head .count.has { color: ${colors.blue.main}; font-weight: 700; }
  & > .body { padding: 4px 12px 12px; border-top: 1px solid ${colors.border}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`
// Section divider for the row-click block in General — visually separates it from the schema-form
// fields above so the mode picker reads as "a different decision", not "yet another field".
const RowClickHeader = styled.div`
  margin-top: 18px; padding-top: 14px; border-top: 1px solid ${colors.border};
  font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};
  text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 8px;
`
// Muted one-liner under a row-click dialog control (the ``Field`` component has no help slot).
const RowClickHint = styled.div`
  font-size: ${fontSize.micro}; color: ${colors.text.muted}; margin-top: 5px; line-height: 1.4;
`
// Column-placeholder chip row under the row_click_route input. Each chip inserts the
// matching ``{column}`` token into the route at the current cursor position so operators
// don't have to type it (and don't have to remember the case-sensitive name).
const PlaceholderChipRow = styled.div`
  display: flex; flex-wrap: wrap; gap: 6px;
  margin-top: -4px; margin-bottom: 10px;
  align-items: baseline;
`
const PlaceholderHint = styled.span`
  font-size: ${fontSize.sm}; color: ${colors.text.muted};
  margin-right: 4px;
`
const PlaceholderChip = styled.button`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  height: 24px; padding: 0 8px;
  border: 1px solid ${colors.border}; border-radius: 6px;
  background: transparent; color: ${colors.text.secondary}; cursor: pointer;
  &:hover { background: ${colors.bg.card}; color: ${colors.text.primary}; border-color: ${colors.text.muted}; }
`
// (``FieldList`` / ``FieldHeader`` / ``FieldBody`` moved into ``ActionListEditor.tsx`` — the
// shared editor owns its own row look.)

// (Action discriminated union + ``blankActionOfType`` + ``ACTION_*`` constants moved into
// ``ActionListEditor.tsx`` — the shared editor used by both this file and the visual builder's
// Tab Settings panel.)
// (``pickFromDefs`` removed — it was used by the now-deleted schema-mode field/tab editor.
// The visual builder picks its sub-schemas internally.)

export interface ScreenEditorProps {
  /** Selected screen path — for the optional breadcrumb. */
  app: string
  id: string
  /** The Screen as a JSON-compatible dict (matching the Screen Pydantic shape's fields). */
  value: Row
  /** The Screen JSON schema (the one carrying all the $defs for ScreenDialog/Tab/Field/ParamBind). */
  schema: JsonSchema
  /** Sibling screen IDs in the same app — feeds the row-click "open dialog" target picker.
   *  Empty when the parent doesn't have the list handy; the picker falls back to a text input. */
  siblingScreenIds?: string[]
  /** Called whenever any field changes — the parent (ScreensBuilder) owns the dirty flag. */
  onChange: (next: Row) => void
}

export default function ScreenEditor({ app, id, value, schema, siblingScreenIds = [], onChange }: ScreenEditorProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const [tab, setTab] = useState<TabKey>('general')
  // ``editQuery`` raises ``EditQueryModal`` to let the operator tweak a query's SQL / params
  // / writable flag without leaving the Screen Designer — every query SearchSelect has an
  // adjacent Edit (pencil) button that sets this. Cleared on Save / Cancel inside the modal.
  const [editQuery, setEditQuery] = useState<{ connector: string; queryName: string } | null>(null)

  const defs = (schema.$defs ?? {}) as Record<string, JsonSchema>
  // Workspace connectors carry their query list; we render the connector + query pickers as
  // SearchSelects driven off that list (instead of plain text fields). Fetched once at login;
  // permission-pruned to what the caller can read.
  const { connectors: wsConnectors, findScreenById } = useWorkspace()
  // Workspace context streams the connector list after login; until then it's null. Guard
  // both reads so the pickers show ``loading`` placeholders instead of crashing on first render.
  const effectiveConnector = (typeof value.connector === 'string' && value.connector.trim() ? value.connector : app)
  const connectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? []).filter((c) => c.type === 'sql')
      .map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [wsConnectors],
  )
  const selectedConnectorMeta = useMemo(
    () => (wsConnectors ?? []).find((c) => c.name === effectiveConnector),
    [wsConnectors, effectiveConnector],
  )
  // Queries available on the effective connector — the four CRUD pickers (read / update /
  // insert / delete) all read from this. SQL connectors only; an API connector has endpoints
  // not queries, but a Screen pins to a SQL read query so the picker filters to SQL anyway.
  const queryOptions = useMemo<SearchSelectOption[]>(() => {
    if (!selectedConnectorMeta || selectedConnectorMeta.type !== 'sql') return []
    return selectedConnectorMeta.queries.map((q) => ({
      value: q.name,
      label: q.description || q.label || q.name,
      mono: q.name,
    }))
  }, [selectedConnectorMeta])
  // CRUD tables on the effective connector — drives the "pick a table → fill all four queries"
  // shortcut so operators don't wire read / update / insert / delete one by one. Sorted by name.
  const tableOptions = useMemo<SearchSelectOption[]>(() => {
    if (selectedConnectorMeta?.type !== 'sql' || !selectedConnectorMeta.tables) return []
    return [...selectedConnectorMeta.tables]
      .sort((a, b) => a.name.localeCompare(b.name))
      .map((tbl) => ({ value: tbl.name, label: tbl.description || tbl.label || tbl.name, mono: tbl.name }))
  }, [selectedConnectorMeta])
  // Fetch the read query's columns once we know the effective connector + read query —
  // powers the SCREEN_COLUMNS augmented enum (the column picker for ``initial_group_by``).
  // Same pattern as the Columns tab in ScreenVisualBuilder: ``_limit=0`` returns the column
  // metadata without rows. A failure is silent (the dropdown is just empty); the field stays
  // editable as a free-text array so a hand-typed column name still works.
  const readQueryName = typeof value.read_query === 'string' ? value.read_query : ''
  const [screenColumns, setScreenColumns] = useState<Column[] | null>(null)
  useEffect(() => {
    setScreenColumns(null)
    if (!effectiveConnector || !readQueryName) return
    let cancelled = false
    api.get<QueryResult>(
      `/api/sql/${encodeURIComponent(effectiveConnector)}/${encodeURIComponent(readQueryName)}?_limit=0`,
    )
      .then((r) => { if (!cancelled) setScreenColumns(r.columns) })
      .catch(() => { /* silent — leave dropdown empty, free-text fallback handles it */ })
    return () => { cancelled = true }
  }, [effectiveConnector, readQueryName])

  // Fetch the saved charts catalog — powers the CHART_IDS augmented enum
  // (the picker for Screen.chart_id, which pre-fills the TableView's Chart
  // tab with a saved chart's spec). Best-effort: a missing / empty
  // charts.toml just yields an empty dropdown — operator saves a chart from
  // the TableView's Chart-tab Save button first.
  const [chartsCatalog, setChartsCatalog] = useState<Record<string, { label?: string; description?: string }>>({})
  useEffect(() => {
    let cancelled = false
    api.get<{ charts: Record<string, { label?: string; description?: string }> }>('/admin/config/charts/parsed')
      .then((r) => { if (!cancelled) setChartsCatalog(r.charts) })
      .catch(() => { /* silent — empty dropdown is the right fallback */ })
    return () => { cancelled = true }
  }, [])

  // Fetch the dictionary so the Columns editor's ``rules_values`` field — whose ``x_enum_ref_when``
  // swaps between ENUM_IDS / LOOKUP_IDS / SEQUENCE_IDS based on the chosen ``rules`` — renders a
  // dropdown of the ids that actually exist (shared + this connector's overlay). Without this only
  // BOOLEAN works (its ``BOOLEAN_TRUE_VALUES`` enum is static); SEQUENCE / LOOKUP / ENUM came back
  // empty. Mirrors what DictionaryBuilder does for the same field.
  const [dict, setDict] = useState<DictionaryDoc['dictionary'] | null>(null)
  useEffect(() => {
    let cancelled = false
    api.get<DictionaryDoc>('/admin/config/dictionary/parsed')
      .then((r) => { if (!cancelled) setDict(r.dictionary) })
      .catch(() => { /* silent — dropdowns just stay empty */ })
    return () => { cancelled = true }
  }, [])

  // Fetch the post-apply step library ([changesets] post_apply) — powers the POST_APPLY_STEPS enum
  // for Screen.post_apply (which run-once steps this change-tracked screen's changes require).
  const [postApplySteps, setPostApplySteps] = useState<Array<{ id: string; label?: string | null }>>([])
  useEffect(() => {
    let cancelled = false
    api.get<{ post_apply: Array<{ id: string; label?: string | null }> }>('/admin/changesets/config/post-apply')
      .then((r) => { if (!cancelled) setPostApplySteps(r.post_apply ?? []) })
      .catch(() => { /* silent — empty dropdown (changesets off / none configured) */ })
    return () => { cancelled = true }
  }, [])

  // Augment the parent's framework enums with SCREEN_COLUMNS — the read query's column names.
  // Consumed by Screen fields that declare ``x_enum_ref: "SCREEN_COLUMNS"`` (currently:
  // ``initial_group_by``, ``treeview.parent/child/label/order_by``). Also adds CHART_IDS for
  // the chart_id picker. The parent ScreensBuilder provides the global framework_enums via
  // FrameworkEnumsContext; we LAYER the per-screen enums on top via a nested Provider around
  // the General SchemaForm so the dropdown options match the live state.
  const parentEnums = useContext(FrameworkEnumsContext)
  // The screen's columns for the SCREEN_COLUMNS picker (initial_group_by, treeview, row-click
  // source binds). PREFER the screen's OWN ``Screen.columns`` — the Phase-3 single source of
  // truth, always present and free (no DB round-trip). Fall back to the live read-query
  // introspection only when the screen hasn't defined columns yet. This is what fixes the empty
  // dropdown on screens whose read query takes a required ``:param`` (e.g. ``WHERE APPS_ID =
  // :APPS_ID``): the ``?_limit=0`` introspection of such a query can fail, but we no longer
  // depend on it when the screen already lists its columns.
  const effectiveScreenColumns = useMemo<Column[]>(() => {
    const defined = Array.isArray(value.columns) ? (value.columns as unknown as Column[]) : []
    return defined.length ? defined : (screenColumns ?? [])
  }, [value.columns, screenColumns])
  const augmentedEnums = useMemo<FrameworkEnums>(() => {
    const base: FrameworkEnums = { ...(parentEnums ?? {}) }
    const colValues = effectiveScreenColumns
      .filter((c) => c.name)
      .map((c) => ({ value: c.name, label: c.label ?? c.name, mono: c.name }))
    base.SCREEN_COLUMNS = { label: `Columns — ${readQueryName || '(no read query)'}`, values: colValues }
    // CHART_IDS — every entry in charts.toml. Operators pick from a dropdown
    // of "<label> (id)" rows; the id is what we store. Empty catalog = the
    // field shows as a search box with no options (operator can still type
    // an id thanks to allowCustom on the SearchSelect, but the validator
    // catches it at config-load if the id doesn't exist).
    const chartValues = Object.entries(chartsCatalog).map(([id, c]) => ({
      value: id,
      label: c.label || c.description || id,
      mono: id,
    }))
    base.CHART_IDS = { label: 'Saved charts', values: chartValues }
    // ENUM_IDS / LOOKUP_IDS / SEQUENCE_IDS — the dictionary ids in this screen's connector scope
    // (shared + the connector overlay). Drive the Columns editor's ``rules_values`` dropdown via
    // its ``x_enum_ref_when`` (rules=ENUM → ENUM_IDS, LOOKUP → LOOKUP_IDS, SEQUENCE/NN →
    // SEQUENCE_IDS). Without these the dropdown only worked for BOOLEAN (static enum).
    const mkIdValues = (
      labelKeys: ReadonlyArray<string>,
      ...maps: (Record<string, Record<string, unknown>> | undefined)[]
    ) => {
      const out = new Map<string, string>()
      for (const m of maps) {
        if (!m) continue
        for (const [id, rec] of Object.entries(m)) {
          let lbl = id
          for (const k of labelKeys) {
            const v = (rec as Record<string, unknown>)?.[k]
            if (typeof v === 'string' && v.trim()) { lbl = v; break }
          }
          out.set(id, lbl)
        }
      }
      return [...out.entries()].map(([value, label]) => ({ value, label, mono: value }))
    }
    // Dictionary metadata (enums / lookups / sequences) is scoped to the APP, not the data-pool
    // connector (matches the backend's ``dict_scope = app``) — so a cross-pool screen's
    // rules_values dropdowns list the right ids (e.g. f00950 on jdedwards sees nomajde's lookups).
    const dscope = dict?.connectors?.[app]
    base.ENUM_IDS = { label: 'Enums', values: mkIdValues(['label'], dict?.enums, dscope?.enums) }
    base.LOOKUP_IDS = { label: 'Lookups', values: mkIdValues(['description'], dict?.lookups, dscope?.lookups) }
    base.SEQUENCE_IDS = { label: 'Sequences', values: mkIdValues(['description'], dict?.sequences, dscope?.sequences) }
    // Per-lookup param enums — keyed by lookup id so a nested ``lookup_param_binds.param`` /
    // ``return_binds.param`` resolves (via ``x_enum_ref_ancestor`` on the enclosing column/rule's
    // ``rules_values``) to exactly THAT lookup's query :params / declared return_params. One enum per
    // lookup in scope (shared ∪ app overlay); the query's params come from the workspace connectors
    // (declared ∪ scanned :bind_params). ``key_columns`` are deliberately NOT params (display-only).
    for (const m of [dict?.lookups, dscope?.lookups]) {
      if (!m) continue
      for (const [lkpId, recRaw] of Object.entries(m)) {
        const rec = recRaw as { connector?: unknown; query?: unknown; return_params?: unknown }
        const lkpConn = (typeof rec.connector === 'string' && rec.connector) ? rec.connector : effectiveConnector
        const qName = typeof rec.query === 'string' ? rec.query : ''
        const params = lookupQueryParamNames(wsConnectors, lkpConn, qName)
        base[`LOOKUP_PARAMS__${lkpId}`] = { label: `Params — ${lkpId}`, values: params.map((p) => ({ value: p, label: p, mono: p })) }
        const rps = Array.isArray(rec.return_params) ? rec.return_params.filter((p): p is string => typeof p === 'string' && !!p) : []
        base[`LOOKUP_RETURN_PARAMS__${lkpId}`] = { label: `Return params — ${lkpId}`, values: rps.map((p) => ({ value: p, label: p, mono: p })) }
      }
    }
    // COLUMN_GROUPS — the group ids defined on this screen, for the per-column ``group`` picker.
    // (The group editor itself — connector / query / bind dropdowns — is the dedicated
    // ColumnGroupsEditor, not a schema-enum field, so no WRITABLE_QUERIES enum is needed here.)
    const groups = Array.isArray(value.column_groups) ? (value.column_groups as Record<string, unknown>[]) : []
    base.COLUMN_GROUPS = {
      label: 'Column groups',
      values: groups
        .filter((g) => typeof g?.id === 'string' && g.id)
        .map((g) => ({ value: g.id as string, label: g.label ? `${g.label} (${g.id})` : (g.id as string), mono: g.id as string })),
    }
    // POST_APPLY_STEPS — the configured run-once step ids ([changesets] post_apply), for the
    // Screen.post_apply multi-select (General tab, next to change_tracked).
    base.POST_APPLY_STEPS = {
      label: 'Post-apply steps',
      values: postApplySteps.map((s) => ({ value: s.id, label: s.label ? `${s.label} (${s.id})` : s.id, mono: s.id })),
    }
    return base
  }, [parentEnums, effectiveScreenColumns, readQueryName, chartsCatalog, dict, app, selectedConnectorMeta, value.column_groups, postApplySteps, wsConnectors, effectiveConnector])

  // Pre-pick the per-tab sub-schemas. General/Queries leave connector + the four query fields
  // out (rendered manually as SearchSelects); everything else still goes through SchemaForm so
  // its field-level enums + descriptions kick in.
  const generalSchema = useMemo<JsonSchema>(() => pickSchemaProperties(schema, GENERAL_FORM_KEYS as unknown as string[]), [schema])

  // ── Row-click "open a sibling Screen as a dialog" binds ───────────────────────────────────
  // The binds map THIS screen's clicked-row columns (``source``) → the TARGET screen's
  // read_query params (``param``). Both sides get a dropdown so the operator picks instead of
  // typing: source = this screen's read-query columns (+ the #BUILTIN# paths); param = the
  // target read_query's declared params + scanned ``:bind_params``.
  const rowClickSourceOptions = useMemo<SearchSelectOption[]>(
    () => mergeCandidates(screenReadColumnOptions(effectiveScreenColumns), builtinSourceOptions()),
    [effectiveScreenColumns],
  )
  // Connector picker for the (optional) row-click connector override — SQL connectors only.
  const sqlConnectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? [])
      .filter((c) => c.type === 'sql')
      .map((c) => ({ value: c.name, label: c.name, mono: c.name }))
      .sort((a, b) => a.value.localeCompare(b.value)),
    [wsConnectors],
  )
  // Resolve the target screen → its read_query → the query def carrying its params.
  const rowClickTarget = (typeof value.row_click_screen === 'string' && value.row_click_screen)
    ? findScreenById(app, value.row_click_screen)
    : null
  const rowClickConnector = (typeof value.row_click_connector === 'string' && value.row_click_connector.trim())
    ? value.row_click_connector
    : (rowClickTarget?.connector || effectiveConnector)
  // The target read_query def — ``null`` until we can locate it in the workspace catalog (target
  // screen not picked yet, query renamed, connector not visible…). Distinguishing "not found" from
  // "found with zero params" is what lets the unmatched-param warning fire on a query that declares
  // NO params at all (the user's ``:APPS_ID``-missing case) without false-positiving on an
  // unresolvable target.
  const rowClickTargetQuery = useMemo(() => {
    if (!rowClickTarget?.read_query) return null
    const conn = (wsConnectors ?? []).find((c) => c.name === rowClickConnector)
    if (!conn || conn.type !== 'sql') return null
    return conn.queries.find((q) => q.name === rowClickTarget.read_query) ?? null
  }, [rowClickTarget?.read_query, rowClickConnector, wsConnectors])
  // ``param`` dropdown — reuse ``targetParamOptions`` (declared params ∪ scanned ``:bind_params``)
  // by shaping a synthetic ``navigate`` action whose ``to`` is the target read_query.
  const rowClickParamOptions = useMemo<SearchSelectOption[]>(
    () => (rowClickTarget?.read_query
      ? targetParamOptions(
          { type: 'navigate', to: rowClickTarget.read_query, connector: rowClickConnector },
          wsConnectors,
          rowClickConnector,
        )
      : []),
    [rowClickTarget?.read_query, rowClickConnector, wsConnectors],
  )
  // #2 — warn when a bind targets a ``param`` that isn't an actual ``:placeholder`` on the target
  // read_query: the runtime fetches the row server-side, so a bind with no matching placeholder is
  // silently dropped and the dialog opens unfiltered. Only checked once the target query RESOLVES
  // (``rowClickTargetQuery`` non-null) — an unresolvable target stays silent (we can't judge it).
  const rowClickUnmatchedParams = useMemo<string[]>(() => {
    if (!rowClickTargetQuery) return []
    const known = new Set(rowClickParamOptions.map((o) => o.value))
    const binds = Array.isArray(value.row_click_binds) ? (value.row_click_binds as ParamBind[]) : []
    return binds.map((b) => b?.param).filter((p): p is string => !!p && !known.has(p))
  }, [rowClickTargetQuery, rowClickParamOptions, value.row_click_binds])

  // Phase 3 — the Columns tab edits ``Screen.columns`` via SchemaNavigator. Same ColumnHint
  // shape used elsewhere; carries the full ``$defs`` map so nested ``filter_from`` /
  // ``visible_when`` / ``lookup_param_binds`` drill in via the breadcrumb navigator.
  const columnsSchema = useMemo<JsonSchema>(
    () => ({ ...pickSchemaProperties(schema, COLUMNS_KEYS as unknown as string[]), $defs: defs }),
    [schema, defs],
  )
  // groupId → the columns currently tagged with it (``ColumnHint.group``) — drives the
  // "Columns in this group" chips in the ColumnGroupsEditor so the operator sees the mapping
  // without scanning the whole column list.
  const columnsByGroup = useMemo<Map<string, string[]>>(() => {
    const m = new Map<string, string[]>()
    const cols = Array.isArray(value.columns) ? (value.columns as Record<string, unknown>[]) : []
    for (const c of cols) {
      const g = typeof c.group === 'string' ? c.group : ''
      const name = typeof c.name === 'string' ? c.name : ''
      if (g && name) { const list = m.get(g) ?? []; list.push(name); m.set(g, list) }
    }
    return m
  }, [value.columns])
  // The screen's own columns as picker options — source/key candidates for a column group's FK
  // binds. Used by the ColumnGroupsEditor (which lives on the Queries tab — a group defines a
  // related-table JOIN + its write-back queries, so it's query structure, not display).
  const groupColumnOptions = useMemo<SearchSelectOption[]>(
    () => effectiveScreenColumns.filter((c) => c.name).map((c) => ({ value: c.name, label: c.label ?? c.name, mono: c.name })),
    [effectiveScreenColumns],
  )
  // (Schema-mode sub-schemas + tab/field mutation helpers are gone — the visual builder owns
  // the dialog-tab/field editing experience now. ``setProp`` / ``dialog`` / ``setDialog`` /
  // ``createDialog`` remain since they're used by the Dialog tab's empty-state "Create dialog"
  // affordance and by the lifecycle-hook accessors below.)
  const setProp = (k: string, v: unknown) => {
    const next = { ...value }
    if (v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0)) delete next[k]
    else next[k] = v
    onChange(next)
  }
  // Fill the four CRUD query fields from a chosen table's slots in one shot. A present slot sets
  // its field (read=`_get`, update=`_put`, insert=`_post`, delete=`_delete`); an absent slot
  // clears the matching field — except read_query (required), which we only overwrite when the
  // table has a read. The operator can still fine-tune any field afterwards.
  const applyTable = (base: string) => {
    if (selectedConnectorMeta?.type !== 'sql') return
    const tbl = (selectedConnectorMeta.tables ?? []).find((t) => t.name === base)
    if (!tbl) return
    const slot = (crud: string): string | undefined => tbl.slots.find((s) => s.crud === crud)?.name
    const next = { ...value }
    const set = (key: string, name: string | undefined, keepWhenMissing: boolean) => {
      if (name) next[key] = name
      else if (!keepWhenMissing) delete next[key]
    }
    set('read_query', slot('get'), true)
    set('update_query', slot('put'), false)
    set('insert_query', slot('post'), false)
    set('delete_query', slot('delete'), false)
    onChange(next)
  }
  const dialog = (value.dialog && typeof value.dialog === 'object' ? value.dialog : null) as { title?: string; tabs?: Row[] } | null
  const setDialog = (next: { title?: string; tabs?: Row[] } | null) => {
    if (!next || (!next.title && (!next.tabs || next.tabs.length === 0))) {
      const v = { ...value }; delete v.dialog
      onChange(v)
    } else {
      onChange({ ...value, dialog: next })
    }
  }
  const createDialog = () => {
    setDialog({ tabs: [{ id: 'general', label: 'General', fields: [] }] })
  }

  // --- render ----------------------------------------------------------------
  // Connector picker — SearchSelect over the workspace's accessible SQL connectors. Setting it
  // back to the app's name implicit value is handled by passing an "(use app's connector)"
  // anyLabel: picking it clears ``screen.connector`` so the runtime falls back to the app name.
  const renderGeneral = (): ReactNode => (
    <>
      <Sub>{t('settings.screens.editor.generalHint')}</Sub>
      <Field label={t('settings.screens.editor.connectorLabel')}>
        <SearchSelect
          value={(value.connector as string | undefined) ?? ''}
          options={connectorOptions}
          onChange={(v) => setProp('connector', v && v !== app ? v : null)}
          anyLabel={t('settings.screens.editor.connectorUseApp', { app })}
          placeholder={app}
        />
      </Field>
      {/* Wrap the General SchemaForm so ``initial_group_by`` (declared with
          ``x_enum_ref: "SCREEN_COLUMNS"``) resolves against the per-screen column list we
          fetched above. The parent ScreensBuilder still provides the global framework_enums;
          ``augmentedEnums`` re-bases on that and layers SCREEN_COLUMNS on top. */}
      <FrameworkEnumsContext.Provider value={augmentedEnums}>
        <SchemaForm
          schema={generalSchema}
          defs={defs}
          value={value}
          onChange={(v) => {
            // Apply every GENERAL_FORM_KEYS field in ONE onChange call — calling ``setProp``
            // in a loop loses edits because each call reads ``value`` from the closure (stale
            // within the synchronous event handler), so only the last one wins (and any field
            // that wasn't the last key in the loop gets clobbered). Build the patched next
            // value in one go, then call ``onChange`` once with the full result. Untouched
            // keys outside GENERAL_FORM_KEYS (dialog, actions, row_menu, id, read_query, …)
            // stay verbatim via the ``{...value}`` spread.
            const next: Row = { ...value }
            for (const k of GENERAL_FORM_KEYS) {
              const val = v[k]
              if (val === undefined || val === null || val === '' || (Array.isArray(val) && val.length === 0)) delete next[k]
              else next[k] = val
            }
            onChange(next)
          }}
        />
      </FrameworkEnumsContext.Provider>
      {renderRowClickSection()}
    </>
  )

  // The Pydantic model carries four flat row_click_* fields, but they're really two mutually-
  // exclusive modes: dialog (screen + connector + binds) or route (a SPA path). The picker
  // turns "fill in 4 fields, hope you pick the right ones" into "pick one of three behaviors,
  // see only the fields that apply." Switching modes also clears the other mode's fields so a
  // half-configured dialog doesn't linger after the operator picked route (and vice versa).
  function renderRowClickSection(): ReactNode {
    // Detect mode by KEY EXISTENCE, not truthy value. setMode seeds the
    // newly-picked mode with an empty string so the operator has something to
    // type into; a truthy check (``value.row_click_route ?``) treats that
    // empty string as falsy → currentMode snaps back to 'none' before the
    // dropdown can register the pick. Existence works: setProp wipes the key
    // when the operator clears the field (the natural "clear to fall back to
    // none" path), so 'route' mode sticks while the field has any value
    // including empty, and reverts only when the operator explicitly clears.
    const currentMode: RowClickMode =
      'row_click_route' in value ? 'route'
      : 'row_click_screen' in value ? 'dialog'
      : 'none'

    const setMode = (m: RowClickMode) => {
      const next: Row = { ...value }
      if (m !== 'dialog') {
        delete next.row_click_screen
        delete next.row_click_connector
        delete next.row_click_binds
      }
      if (m !== 'route') delete next.row_click_route
      // Seed a sensible default for the active mode so the new field set has
      // something to bind to AND so the existence check above flips currentMode
      // to the picked mode immediately (previously only ``route`` was seeded —
      // ``dialog`` mode never stuck because no key was created for it).
      if (m === 'route' && !('row_click_route' in next)) next.row_click_route = ''
      if (m === 'dialog' && !('row_click_screen' in next)) next.row_click_screen = ''
      onChange(next)
    }

    return (
      <>
        <RowClickHeader>{t('settings.screens.editor.rowClickHeader', 'When a row is clicked')}</RowClickHeader>
        <Field label={t('settings.screens.editor.rowClickModeLabel', 'Behavior')}>
          <Select
            value={currentMode}
            onChange={(e) => setMode(e.target.value as RowClickMode)}
          >
            <option value="none">{t('settings.screens.editor.rowClickModeNone', 'Do nothing (or fall through to the screen\'s own dialog)')}</option>
            <option value="dialog">{t('settings.screens.editor.rowClickModeDialog', 'Open a sibling Screen as a modal dialog')}</option>
            <option value="route">{t('settings.screens.editor.rowClickModeRoute', 'Open a page route in a new browser tab')}</option>
          </Select>
        </Field>

        {currentMode === 'dialog' && (
          <>
            <Field label={t('settings.screens.editor.rowClickScreenLabel', 'Target screen')}>
              <SearchSelect
                value={(value.row_click_screen as string | undefined) ?? ''}
                options={[...siblingScreenIds].sort((a, b) => a.localeCompare(b)).map((sid) => ({ value: sid, label: sid, mono: sid }))}
                onChange={(v) => setProp('row_click_screen', v || null)}
                placeholder={t('settings.screens.editor.rowClickScreenPlaceholder', 'Pick a sibling screen…')}
              />
            </Field>
            <Field label={t('settings.screens.editor.rowClickConnectorLabel', 'Target connector')}>
              <SearchSelect
                value={(value.row_click_connector as string | undefined) ?? ''}
                options={sqlConnectorOptions}
                onChange={(v) => setProp('row_click_connector', v || null)}
                anyLabel={t('settings.screens.editor.rowClickConnectorInherit', '(inherit — {{conn}})', { conn: rowClickTarget?.connector || effectiveConnector })}
                placeholder={t('settings.screens.editor.rowClickConnectorPlaceholder', 'Inherit from target screen')}
              />
              <RowClickHint>{t('settings.screens.editor.rowClickConnectorHelp', 'Connector hosting the target screen; blank → the target screen’s own (or this screen’s) connector.')}</RowClickHint>
            </Field>
            <Field label={t('settings.screens.editor.rowClickBindsLabel', 'Pass these values to the target')}>
              <ParamBindList
                value={Array.isArray(value.row_click_binds) ? (value.row_click_binds as ParamBind[]) : []}
                onChange={(next) => setProp('row_click_binds', next.length ? next : null)}
                sourceOptions={rowClickSourceOptions}
                paramOptions={rowClickParamOptions}
                paramPlaceholder={t('settings.screens.editor.rowClickParamPlaceholder', 'Target param (:placeholder)')}
              />
              <RowClickHint>{t('settings.screens.editor.rowClickBindsHelp', 'Bind the clicked row’s columns into the target screen’s read_query. “source” reads a column on this screen’s row; “value” is a literal.')}</RowClickHint>
            </Field>
            {rowClickUnmatchedParams.length > 0 && (
              <Banner $tone="warning">
                {t('settings.screens.editor.rowClickUnmatchedWarning',
                  'These binds target a param the read_query “{{q}}” doesn’t declare, so they won’t filter the row: {{params}}. Add the matching “:{{first}}” placeholder to that query (Edit query), or remove the bind.',
                  {
                    q: rowClickTarget?.read_query ?? '',
                    params: rowClickUnmatchedParams.join(', '),
                    first: rowClickUnmatchedParams[0],
                  })}
              </Banner>
            )}
          </>
        )}

        {currentMode === 'route' && (() => {
          // Plain Input (was a SearchSelect — its portaled dropdown mispositioned for this
          // field, and the hardcoded ROW_CLICK_ROUTE_OPTIONS preset with ``{id}`` confused
          // operators on screens whose column isn't literally named "id"). The route is just
          // a string with ``{column_name}`` placeholders; the chip row below inserts the
          // column names at the cursor without operators having to remember the case
          // (validator wants the same case as the column hints — typically UPPERCASE).
          const routeValue = (value.row_click_route as string | undefined) ?? ''
          const insertPlaceholder = (colName: string) => {
            const token = `{${colName}}`
            const el = document.getElementById('row-click-route-input') as HTMLInputElement | null
            if (el) {
              const start = el.selectionStart ?? routeValue.length
              const end = el.selectionEnd ?? routeValue.length
              const next = routeValue.slice(0, start) + token + routeValue.slice(end)
              setProp('row_click_route', next)
              // Restore caret AFTER the inserted token on the next tick.
              queueMicrotask(() => {
                el.focus()
                const caret = start + token.length
                el.setSelectionRange(caret, caret)
              })
            } else {
              setProp('row_click_route', routeValue + token)
            }
          }
          return (
            <>
              <Field label={t('settings.screens.editor.rowClickRouteLabel', 'Page route')}>
                <Input
                  id="row-click-route-input"
                  value={routeValue}
                  onChange={(e) => setProp('row_click_route', e.target.value)}
                  placeholder="/path/{column_name}"
                />
              </Field>
              {ROW_CLICK_ROUTE_OPTIONS.length > 0 && (
                <PlaceholderChipRow>
                  <PlaceholderHint>{t('settings.screens.editor.rowClickRoutePresets', 'Presets:')}</PlaceholderHint>
                  {ROW_CLICK_ROUTE_OPTIONS.map((preset) => (
                    <PlaceholderChip
                      key={preset.value}
                      type="button"
                      onClick={() => setProp('row_click_route', preset.value)}
                      title={preset.label}
                    >
                      {preset.value}
                    </PlaceholderChip>
                  ))}
                </PlaceholderChipRow>
              )}
              {screenColumns && screenColumns.length > 0 && (
                <PlaceholderChipRow>
                  <PlaceholderHint>
                    {t('settings.screens.editor.rowClickRouteColumnHint',
                      'Click a column to insert it as a placeholder:')}
                  </PlaceholderHint>
                  {screenColumns.map((c) => (
                    <PlaceholderChip
                      key={c.name}
                      type="button"
                      onClick={() => insertPlaceholder(c.name)}
                      title={c.label ?? c.name}
                    >
                      {`{${c.name}}`}
                    </PlaceholderChip>
                  ))}
                </PlaceholderChipRow>
              )}
            </>
          )
        })()}
      </>
    )
  }

  // The four CRUD query pickers — each a SearchSelect over the effective connector's queries.
  // read_query is required (validator on the backend); the others are optional and clearing
  // them removes the key. Picker shows the v2 query name (mono) + the query's description /
  // label so operators can find by friendly name. An "Edit" button next to the picker raises
  // ``EditQueryModal`` so the operator can tweak the SQL / params without leaving this screen.
  const renderQueryField = (key: 'read_query' | 'update_query' | 'insert_query' | 'delete_query', required: boolean): ReactNode => {
    const queryName = (value[key] as string | undefined) ?? ''
    return (
      <Field
        key={key}
        label={`${t(`settings.screens.editor.queries.${key}`)}${required ? ' *' : ''}`}
      >
        <Row gap={6} style={{ alignItems: 'center' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <SearchSelect
              value={queryName}
              options={queryOptions}
              onChange={(v) => setProp(key, v || (required ? value[key] ?? '' : null))}
              anyLabel={required ? undefined : t('common.none')}
              placeholder={selectedConnectorMeta ? t('common.pick') : t('settings.screens.editor.queries.pickConnectorFirst')}
              loading={!selectedConnectorMeta}
            />
          </div>
          {/* Edit / Clone / Add trio — same shared component the dictionary + charts
              editors use. Clone + Add need ``existingNames`` to populate the new-name
              validator + suggest non-colliding clone defaults. */}
          <EditQueryButton connector={effectiveConnector} queryName={queryName} />
          <CloneQueryButton connector={effectiveConnector} queryName={queryName}
            existingNames={queryOptions.map((o) => o.value)} />
          <AddQueryButton connector={effectiveConnector}
            existingNames={queryOptions.map((o) => o.value)} />
        </Row>
      </Field>
    )
  }
  const renderQueries = (): ReactNode => (
    <>
      <Sub>{t('settings.screens.editor.queriesHint')}</Sub>
      {tableOptions.length > 0 && (
        <Field label={t('settings.screens.editor.queries.fromTable', 'Fill from table')}>
          <SearchSelect
            value=""
            options={tableOptions}
            onChange={(v) => v && applyTable(v)}
            anyLabel={t('settings.screens.editor.queries.fromTableHint', 'Pick a table to fill all four CRUD queries…')}
            placeholder={t('common.pick')}
            loading={!selectedConnectorMeta}
          />
        </Field>
      )}
      {renderQueryField('read_query', true)}
      {renderQueryField('update_query', false)}
      {renderQueryField('insert_query', false)}
      {renderQueryField('delete_query', false)}
      {/* Seed the Columns tab from the read query's result columns (name + dd) in one click. Lives
          here because it derives from the read query; only adds columns NOT already defined, so
          it's safe to re-run after the query gains a column. */}
      {(() => {
        const cols = Array.isArray(value.columns) ? (value.columns as unknown[]) : []
        const have = new Set((cols as { name?: string }[]).map((c) => (c.name ?? '').toLowerCase()))
        const missing = (screenColumns ?? []).filter((c) => c.name && !have.has(c.name.toLowerCase()))
        return (
          <Button
            $size="sm" $variant="ghost"
            disabled={!screenColumns || missing.length === 0}
            onClick={() => setProp('columns', [
              ...cols,
              // Column names are stored UPPERCASE (write queries + dictionary use v1's uppercase
              // ids; a Postgres read folds them lowercase, so normalize on import).
              ...missing.map((c) => ({ name: c.name.toUpperCase(), ...(c.dd ? { dd: c.dd.toUpperCase() } : {}) })),
            ])}
            style={{ marginTop: 4, marginBottom: 8 }}
          >
            <Plus size={13} />{' '}
            {!screenColumns
              ? t('settings.screens.editor.importColumnsLoading', 'Import from read query…')
              : missing.length === 0
                ? t('settings.screens.editor.importColumnsNone', 'All read-query columns added')
                : t('settings.screens.editor.importColumns', 'Import {{n}} column(s) from the read query', { n: missing.length })}
          </Button>
        )
      })()}
      {/* Column groups — related 1:1 tables JOINed into the read query, each with its own
          write-back update/insert/delete queries. This is query structure (a join + its CRUD), so
          it lives here, not on the Columns tab; columns are tagged into a group via their "Group"
          field in the column list. */}
      <GroupsHr />
      <Sub>{t('settings.screens.editor.columnGroupsHint',
        'Column groups — related 1:1 tables whose columns are edited inline and written back on Save. Define a group here, then tag each related column with it (its “Group” field) on the Columns tab.')}</Sub>
      <ColumnGroupsEditor
        value={Array.isArray(value.column_groups) ? (value.column_groups as Record<string, unknown>[]) : []}
        onChange={(next) => setProp('column_groups', next.length ? next : null)}
        screenConnector={effectiveConnector}
        connectorOptions={connectorOptions}
        columnOptions={groupColumnOptions}
        columnsByGroup={columnsByGroup}
      />
    </>
  )

  // Phase 3 — the Columns tab edits ``Screen.columns`` (single source of truth for per-screen
  // display metadata: label / dd / format / hidden / filter / filter_from / visible_when / width
  // / align / rules / rules_values / default / lookup_param_binds). Same shape drives both the
  // grid editor and the dialog form. SchemaNavigator gives breadcrumb drill-down into each
  // column hint and its nested rules.
  const renderColumns = (): ReactNode => {
    const currentColumns = Array.isArray(value.columns) ? (value.columns as unknown[]) : []
    return (
      <>
        {/* Just the column list + drill. The "import from read query" button lives on the Queries
            tab (it seeds from the read query), and column GROUPS live there too. */}
        {/* Wrap in the augmented enums so a column's ``rules_values`` resolves its dropdown —
            ENUM_IDS / LOOKUP_IDS / SEQUENCE_IDS (from the dictionary) + SCREEN_COLUMNS — plus
            COLUMN_GROUPS for the per-column ``group`` picker. */}
        <FrameworkEnumsContext.Provider value={augmentedEnums}>
          <SchemaNavigator
            root={{
              label: t('settings.screens.editor.columnsCrumb', { id }),
              schema: columnsSchema,
              value: { columns: currentColumns },
              onChange: (v) => {
                const next = Array.isArray(v.columns) && (v.columns as unknown[]).length
                  ? (v.columns as unknown[])
                  : null
                setProp('columns', next)
              },
              // Per-column context for the column form: resolve the column's DATA DICTIONARY rule
              // (from the fetched dictionary) so we can (1) show the inherited default beside the
              // RULES field when it's left blank, and (2) only show the Lookup tab when the column's
              // effective rule is a LOOKUP (override or DD default). Only meaningful at the
              // ColumnHint level — other drilled levels (filter_from, …) get no context.
              deriveContext: (col, sch) => {
                // Only at the ColumnHint level (its schema carries a ``rules`` property) — not the
                // columns-array root or nested filter_from / bind levels.
                if (!sch?.properties || !('rules' in sch.properties)) return {}
                const ddName = typeof col.dd === 'string' ? col.dd : undefined
                const name = typeof col.name === 'string' ? col.name : ''
                const key = ddName === '' ? null : (ddName || name)  // dd="" opts out of the dictionary
                let ddRule: string | undefined
                let ddRuleVal: string | undefined
                if (key) {
                  // Dictionary scope is the screen's APP (where entries/lookups/enums live), NOT
                  // its data-pool connector — matches the backend's ``dict_scope = app`` (a
                  // cross-pool screen like f00950 runs on jdedwards but its DD entries are under
                  // connectors.nomajde). Fall back to the shared top-level entries.
                  const dscope = dict?.connectors?.[app]
                  const entry = (dscope?.entries?.[key] ?? dscope?.entries?.[key.toUpperCase()]
                    ?? dict?.entries?.[key] ?? dict?.entries?.[key.toUpperCase()]) as Record<string, unknown> | undefined
                  ddRule = typeof entry?.rules === 'string' && entry.rules ? entry.rules.toUpperCase() : undefined
                  ddRuleVal = typeof entry?.rules_values === 'string' && entry.rules_values ? entry.rules_values : undefined
                }
                const override = typeof col.rules === 'string' && col.rules ? col.rules.toUpperCase() : undefined
                const effective = override ?? ddRule
                // Can this column ever be a LOOKUP? base override / DD default / any rules_when entry.
                const rw = Array.isArray(col.rules_when) ? (col.rules_when as Record<string, unknown>[]) : []
                const canLookup = effective === 'LOOKUP'
                  || rw.some((e) => typeof e?.rules === 'string' && e.rules.toUpperCase() === 'LOOKUP')
                return {
                  // The Rules tab always shows (it holds the rule); hide the LOOKUP-only fields when
                  // the column can't be a lookup — the declutter the old "Lookup tab only" gave us.
                  hiddenFields: canLookup ? [] : ['hide_label', 'lookup_param_binds', 'return_binds'],
                  fieldNotes: !override && ddRule
                    ? { rules: (
                        <div style={{ fontSize: fontSize.micro, color: colors.text.muted, marginTop: 4 }}>
                          {t('settings.screens.editor.ddDefaultRule', 'Data dictionary default: {{rule}}',
                            { rule: ddRuleVal ? `${ddRule} (${ddRuleVal})` : ddRule })}
                        </div>
                      ) }
                    : undefined,
                }
              },
            }}
          />
        </FrameworkEnumsContext.Provider>
      </>
    )
  }

  // ── action editors (shared component) ──────────────────────────────────────────────
  // ``renderActionList`` + its inline ``renderPromptFields`` / ``renderActionOverrides`` /
  // ``actionVariantSchema`` blocks moved into ``ActionListEditor.tsx`` / ``ActionTreeView.tsx``.
  //
  // Theme A extended to this surface: each hook list (on_load / on_save / on_cancel /
  // screen.actions / on_insert / on_update / on_delete / row_menu) renders as a flat
  // ``<ActionTreeView path={null}>`` list — clicking a row opens ``ActionEditorModal`` with
  // ActionTreeView in editor mode. Without this, expanding one chain in the Actions tab
  // pushed the other six lists off-screen.
  const onEditQueryRaise = (connector: string, queryName: string) =>
    setEditQuery({ connector, queryName })
  // ``openEditor`` identifies WHICH hook list is being edited + the path within it. The modal
  // mounts when this is non-null and reads / writes through the matching list's accessors.
  // A ``listKey`` of ``'on_load'`` / ``'on_save'`` / ``'on_cancel'`` targets the dialog's hook;
  // anything else targets the screen's top-level slot.
  type ListKey = 'on_load' | 'on_save' | 'on_cancel' | 'actions' | 'on_insert' | 'on_update' | 'on_delete' | 'on_duplicate' | 'row_menu'
  const [openEditor, setOpenEditor] = useState<{ listKey: ListKey; path: ActionPath } | null>(null)
  // The Actions tab stacks several hook sections (on_save / on_cancel / screen actions / on_insert /
  // on_update / on_delete); most are empty on a given screen, so each is COLLAPSIBLE and collapsed
  // by default — the header shows the action count so a non-empty hook stands out. (row_menu has its
  // own single-section tab and stays expanded.) Toggle state keyed by listKey.
  const [expandedHooks, setExpandedHooks] = useState<Set<string>>(new Set())
  const toggleHook = (k: string) => setExpandedHooks((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n })


  // Dialog lifecycle hook accessors — same pattern for each. ``setDialog`` strips empty hook
  // lists (we drop the key when the array empties) so the saved TOML stays terse.
  const dialogList = (key: 'on_load' | 'on_save' | 'on_cancel'): Row[] =>
    Array.isArray((dialog as Row | null)?.[key]) ? ((dialog as Row)[key] as Row[]) : []
  const setDialogList = (key: 'on_load' | 'on_save' | 'on_cancel', next: Row[]) => {
    const updated = { ...(dialog ?? {}) } as Row
    if (next.length === 0) delete updated[key]
    else updated[key] = next
    setDialog(updated as { title?: string; tabs?: Row[] })
  }
  const onLoad = useMemo(() => dialogList('on_load'), [dialog])
  const onSave = useMemo(() => dialogList('on_save'), [dialog])
  const onCancel = useMemo(() => dialogList('on_cancel'), [dialog])
  // Each list renders as a flat ``<ActionTreeView path={null}>`` — clicking a row sets
  // ``openEditor`` so the ``ActionEditorModal`` overlays the screen-editor with the focused
  // action's chain editor. The ``listKey`` identifies the slot the modal writes back to.
  // The screen's read-query columns — passed down to ParamBindList so the source dropdown
  // surfaces the firing row's column names. The migration always emits ``Screen.columns``,
  // but a hand-written screens.toml without them still works (empty list = no candidates
  // from this source, only chain-context ones).
  const screenReadColumns: Row[] = Array.isArray((value as Row).columns)
    ? ((value as Row).columns as Row[])
    : []
  // on_duplicate exposes the ORIGINAL record under SOURCE_<col> keys at runtime — surface them as
  // pickable ``source`` options (otherwise the operator has to know the prefix + type it).
  const duplicateSourceOptions: SearchSelectOption[] = screenReadColumns
    .filter((c) => typeof c.name === 'string' && c.name)
    .map((c) => ({ value: `SOURCE_${c.name as string}`, label: `SOURCE_${c.name as string} (original row)`, mono: `SOURCE_${c.name as string}`, group: 'Duplicate source' }))
  const renderHookList = (
    listKey: ListKey,
    actions: Row[],
    onChange: (n: Row[]) => void,
    heading: string,
    hint: string,
    emptyMessage: string,
  ): ReactNode => {
    const tree = (
      <ActionTreeView
        actions={actions}
        onChange={onChange}
        path={null}
        onPathChange={(p) => p && p.length > 0 && setOpenEditor({ listKey, path: p })}
        selectedPath={openEditor?.listKey === listKey ? openEditor.path : null}
        defs={defs}
        effectiveConnector={effectiveConnector}
        onEditQuery={onEditQueryRaise}
        rootLabel={heading}
        // Collapsible sections render the heading in their own header (below); a single-section tab
        // (row_menu) keeps ActionTreeView's internal heading.
        heading={listKey === 'row_menu' ? heading : undefined}
        hint={hint}
        emptyMessage={emptyMessage}
        screenReadColumns={screenReadColumns}
        extraSourceOptions={listKey === 'on_duplicate' ? duplicateSourceOptions : undefined}
      />
    )
    if (listKey === 'row_menu') return tree
    const expanded = expandedHooks.has(listKey)
    return (
      <HookSection>
        <button type="button" className="head" onClick={() => toggleHook(listKey)}>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span className="title">{heading}</span>
          <span className={actions.length ? 'count has' : 'count'}>{actions.length}</span>
        </button>
        {expanded && <div className="body">{tree}</div>}
      </HookSection>
    )
  }
  const renderOnLoad = (): ReactNode => renderHookList(
    'on_load', onLoad, (n) => setDialogList('on_load', n),
    t('settings.screens.onLoad.heading'),
    t('settings.screens.onLoad.hint'),
    t('settings.screens.onLoad.empty'),
  )
  const renderOnSave = (): ReactNode => renderHookList(
    'on_save', onSave, (n) => setDialogList('on_save', n),
    t('settings.screens.action.heading'),
    t('settings.screens.action.hint'),
    t('settings.screens.action.empty'),
  )
  const renderOnCancel = (): ReactNode => renderHookList(
    'on_cancel', onCancel, (n) => setDialogList('on_cancel', n),
    t('settings.screens.onCancel.heading'),
    t('settings.screens.onCancel.hint'),
    t('settings.screens.onCancel.empty'),
  )

  // (Per-tab ``actions`` editor moved to the visual builder — it owns the dialog-tab selection
  // state, so the per-tab actions sit naturally on the selected tab's canvas. The renderActionList
  // editor for them is wired inside ScreenVisualBuilder's tab body.)

  // Screen `actions` — toolbar buttons above the TableView. v1's named workflows (NOMAJDE's
  // "Create Role" / "Reset Password" / etc.) belong here. ParamBinds resolve against the
  // *selected row* (when one is selected) — same Action shape used everywhere.
  const screenActions: Row[] = useMemo(
    () => (Array.isArray((value as Row).actions) ? ((value as Row).actions as Row[]) : []),
    [value],
  )
  const setScreenActions = (next: Row[]) => {
    const v = { ...value }
    if (next.length === 0) delete v.actions
    else v.actions = next
    onChange(v)
  }
  const renderScreenActions = (): ReactNode => renderHookList(
    'actions', screenActions, setScreenActions,
    t('settings.screens.actions.heading'),
    t('settings.screens.actions.hint'),
    t('settings.screens.actions.empty'),
  )

  // Screen-level row lifecycle hooks — v2's port of v1's ``ly_evt_cpt`` FormsTable events
  // (evt 2 = on_insert, evt 3 = on_delete; on_update is v2's own extension). Fire after a row
  // is mutated whether via dialog Save in the matching mode *or* the batch-edit grid Save.
  // ParamBinds resolve against the *new row's* values (insert/update) or the deleted row's
  // values (delete). Edit them in the same "Actions" tab alongside the toolbar buttons —
  // related concept, same Action shape.
  const screenHookList = (key: 'on_insert' | 'on_update' | 'on_delete' | 'on_duplicate'): Row[] =>
    Array.isArray((value as Row)[key]) ? ((value as Row)[key] as Row[]) : []
  const setScreenHook = (key: 'on_insert' | 'on_update' | 'on_delete' | 'on_duplicate', next: Row[]) => {
    const v = { ...value }
    if (next.length === 0) delete v[key]
    else v[key] = next
    onChange(v)
  }
  const renderRowHooks = (): ReactNode => (
    <>
      {renderHookList(
        'on_insert', screenHookList('on_insert'), (n) => setScreenHook('on_insert', n),
        t('settings.screens.onInsert.heading'),
        t('settings.screens.onInsert.hint'),
        t('settings.screens.onInsert.empty'),
      )}
      {renderHookList(
        'on_update', screenHookList('on_update'), (n) => setScreenHook('on_update', n),
        t('settings.screens.onUpdate.heading'),
        t('settings.screens.onUpdate.hint'),
        t('settings.screens.onUpdate.empty'),
      )}
      {renderHookList(
        'on_delete', screenHookList('on_delete'), (n) => setScreenHook('on_delete', n),
        t('settings.screens.onDelete.heading'),
        t('settings.screens.onDelete.hint'),
        t('settings.screens.onDelete.empty'),
      )}
      {renderHookList(
        'on_duplicate', screenHookList('on_duplicate'), (n) => setScreenHook('on_duplicate', n),
        t('settings.screens.onDuplicate.heading', 'On duplicate (row hook)'),
        t('settings.screens.onDuplicate.hint', 'Runs when a row is duplicated (Duplicate → Save), instead of on_insert. The source record is exposed as SOURCE_<col> so actions can copy related-table rows (roles, menus…) from the original to the new row.'),
        t('settings.screens.onDuplicate.empty', 'No on-duplicate actions yet — duplicating just writes the cloned row.'),
      )}
    </>
  )

  // Screen `row_menu` (slice 6) — actions shown when the user right-clicks a row in the TableView.
  // ParamBinds resolve against the clicked row's values (not the dialog form state) — the runtime
  // uses the same Action shape; only the firing context differs.
  const rowMenu: Row[] = useMemo(
    () => (Array.isArray((value as Row).row_menu) ? ((value as Row).row_menu as Row[]) : []),
    [value],
  )
  const setRowMenu = (next: Row[]) => {
    const v = { ...value }
    if (next.length === 0) delete v.row_menu
    else v.row_menu = next
    onChange(v)
  }
  const renderRowMenu = (): ReactNode => renderHookList(
    'row_menu', rowMenu, setRowMenu,
    t('settings.screens.rowmenu.heading'),
    t('settings.screens.rowmenu.hint'),
    t('settings.screens.rowmenu.empty'),
  )

  // Workbook export — Phase 9. ``Screen.export = WorkbookExport`` configures multi-file /
  // multi-sheet xlsx generation. The TableView shows an "Export workbooks" button once this
  // is set; clicking it fires ``POST /api/screens/{app}/{id}/export`` which streams the
  // file(s).
  //
  // Dedicated ExportEditor (since generic SchemaForm couldn't surface connector / query
  // dropdowns or the placeholder helpers): split_by reads from the screen's resolved columns,
  // each sheet's connector + query are SearchSelects over the workspace's connectors, and
  // every template field has a click-to-insert placeholder chip strip ({{screen}},
  // {{split_value}}, {{sheet_value}} where applicable).
  const exportValue = (value as Row).export as Row | undefined
  const setExport = (next: Row | null) => setProp('export', next)
  const createExport = () => setExport({ sheets: [] })
  const deleteExport = async () => {
    const ok = await modals.confirm({
      title: t('settings.screens.export.deleteTitle', 'Delete export config?'),
      message: t('settings.screens.export.deleteMsg', "Remove the screen's workbook export configuration. The Export button disappears from the TableView."),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    setExport(null)
  }
  const renderExport = (): ReactNode => {
    if (!exportValue) {
      return (
        <Stack gap={14}>
          <Sub>{t('settings.screens.export.hint',
            'Configure multi-file / multi-sheet xlsx export. Set ``split_by`` to a column on the read query and the export produces one xlsx per distinct value; each sheet runs its own query with the running ``split_value`` bound through its ParamBinds. Leave ``split_by`` blank for a single xlsx with all sheets.')}</Sub>
          <Empty>
            <Stack gap={12} style={{ alignItems: 'center' }}>
              <div>{t('settings.screens.export.empty', 'No export configured.')}</div>
              <Button $variant="primary" $size="sm" onClick={createExport}>
                <Plus size={13} /> {t('settings.screens.export.create', 'Configure export')}
              </Button>
            </Stack>
          </Empty>
        </Stack>
      )
    }
    const readQueryName = (typeof value.read_query === 'string' ? value.read_query : '') || ''
    const screenColumns = Array.isArray(value.columns) ? (value.columns as Column[]) : undefined
    return (
      <Stack gap={14}>
        <Row gap={8} style={{ justifyContent: 'flex-end' }}>
          <Button $variant="danger" $size="sm" onClick={deleteExport}>
            <Trash2 size={13} /> {t('settings.screens.export.delete', 'Delete export config')}
          </Button>
        </Row>
        <ExportEditor
          value={exportValue}
          onChange={(next) => setExport(next)}
          effectiveConnector={effectiveConnector}
          readQuery={readQueryName}
          columns={screenColumns}
          defs={defs}
        />
      </Stack>
    )
  }

  // Deletes the entire dialog (every tab + field + lookup_param_bind). Confirmed via the
  // shared ConfirmModal — the action is destructive and irreversible inside this designer
  // session (the only way back is to Cancel the whole designer modal, which reverts to the
  // snapshot, OR Reload from disk after committing).
  const deleteDialog = async () => {
    const ok = await modals.confirm({
      title: t('settings.screens.editor.dialogDeleteTitle', 'Delete dialog?'),
      message: t('settings.screens.editor.dialogDeleteMsg', 'Remove the screen\'s dialog (every tab + field + lookup_param_bind). The screen becomes read-only / grid-edit only.'),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    setDialog(null)
  }

  const renderDialog = (): ReactNode => {
    if (!dialog) {
      return (
        <Stack gap={14}>
          <Sub>{t('settings.screens.editor.dialogHint')}</Sub>
          <Empty>
            <Stack gap={12} style={{ alignItems: 'center' }}>
              <div>{t('settings.screens.editor.dialogEmpty')}</div>
              <Button $variant="primary" $size="sm" onClick={createDialog}>
                <Plus size={13} /> {t('settings.screens.editor.dialogCreate')}
              </Button>
            </Stack>
          </Empty>
        </Stack>
      )
    }
    // The Dialog tab IS the visual builder — schema mode and the Visual/Schema toggle are gone.
    // Lifecycle hooks (on_load / on_save / on_cancel) moved to the Actions tab where the rest
    // of the action chains live, grouped by event kind. Per-panel scrolling (palette / canvas /
    // inspector) is handled inside ScreenVisualBuilder; we just give it a flex-fill container.
    // A "Delete dialog" button on the right header line lets the operator wipe it entirely —
    // confirmed via shared modal so an accidental click doesn't nuke the work.
    return (
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <Row gap={8} style={{ justifyContent: 'flex-end', marginBottom: 8 }}>
          <Button $variant="danger" $size="sm" onClick={deleteDialog}>
            <Trash2 size={13} /> {t('settings.screens.editor.dialogDelete', 'Delete dialog')}
          </Button>
        </Row>
        <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
          <ScreenVisualBuilder app={app} id={id} value={value} schema={schema} onChange={onChange} />
        </div>
      </div>
    )
  }

  const renderTab = (): ReactNode => {
    switch (tab) {
      case 'general': return renderGeneral()
      case 'queries': return renderQueries()
      case 'columns': return renderColumns()
      case 'dialog':  return renderDialog()
      case 'actions': return (
        // All action attachment points consolidated. Grouped visually by *when* they fire:
        // - **Dialog hooks** (on_load / on_save / on_cancel) — fire while the dialog is open
        // - **Toolbar** (Screen.actions) — the buttons above the table
        // - **Row hooks** (on_insert / on_update / on_delete) — fire after a row mutation,
        //   either via dialog Save or the inline grid's batch save
        // Each section is rendered through the same ``renderActionList`` editor.
        <Stack gap={20}>
          <div>
            <Sub style={{ margin: 0, marginBottom: 8 }}>{t('settings.screens.actionsTab.dialogHooksGroup')}</Sub>
            {renderOnLoad()}
            {renderOnSave()}
            {renderOnCancel()}
          </div>
          <div>
            <Sub style={{ margin: 0, marginBottom: 8 }}>{t('settings.screens.actionsTab.toolbarGroup')}</Sub>
            {renderScreenActions()}
          </div>
          <div>
            <Sub style={{ margin: 0, marginBottom: 8 }}>{t('settings.screens.actionsTab.rowHooksGroup')}</Sub>
            {renderRowHooks()}
          </div>
        </Stack>
      )
      case 'rowmenu': return renderRowMenu()
      case 'export':  return renderExport()
    }
  }

  // Active tab content lives inside a scroll-managing wrapper. Most tabs (General / Queries /
  // Actions / Row menu) get an ``overflow-y: auto`` wrapper so long forms scroll inside the
  // modal body without making the body itself scroll. The Dialog tab is special — the visual
  // builder owns its own per-panel scrolling (palette / canvas / inspector all scroll on their
  // own); wrapping it would create a redundant outer scrollbar. Detect via the active tab key.
  const isDialogTab = tab === 'dialog'
  return (
    <Stack gap={10} style={{ flex: 1, minHeight: 0 }}>
      <TabsBar>
        {TAB_ORDER.map((k) => (
          <TabBtn key={k} type="button" $active={tab === k} onClick={() => setTab(k)}>
            {t(`settings.screens.editor.tabs.${k}`)}
          </TabBtn>
        ))}
      </TabsBar>
      <div style={{
        flex: 1,
        minHeight: 0,
        overflowY: isDialogTab ? 'hidden' : 'auto',
        display: 'flex',
        flexDirection: 'column',
        paddingRight: isDialogTab ? 0 : 4,
      }}>
        {renderTab()}
      </div>
      {/* Per-query Edit modal — opened from any query SearchSelect's pencil button. The modal
          fetches its own connectors copy + PUTs it back on Save (independent of the Screen
          Designer's edit cycle, since query edits live in connectors.toml). */}
      {editQuery && (
        <EditQueryModal
          connector={editQuery.connector}
          queryName={editQuery.queryName}
          onClose={() => setEditQuery(null)}
        />
      )}
      {/* Per-hook chain editor modal — opens when the operator clicks an action row in any
          of the Actions / Row menu tab lists. The modal hosts ``ActionTreeView`` in editor
          mode with its own breadcrumb; ``openEditor.path`` updates as the operator dives in /
          pops back. Closing the modal (``onClose`` / breadcrumb root / Esc / click-outside)
          clears ``openEditor``; the underlying tab is left intact. The ``listKey`` resolves to
          the matching list + setter so writes flow back to the right slot. */}
      {openEditor && (() => {
        const lookup: Record<ListKey, { list: Row[]; setList: (n: Row[]) => void; label: string }> = {
          on_load:   { list: onLoad,    setList: (n) => setDialogList('on_load', n),  label: t('settings.screens.onLoad.heading') },
          on_save:   { list: onSave,    setList: (n) => setDialogList('on_save', n),  label: t('settings.screens.action.heading') },
          on_cancel: { list: onCancel,  setList: (n) => setDialogList('on_cancel', n),label: t('settings.screens.onCancel.heading') },
          actions:   { list: screenActions, setList: setScreenActions,                label: t('settings.screens.actions.heading') },
          on_insert: { list: screenHookList('on_insert'), setList: (n) => setScreenHook('on_insert', n), label: t('settings.screens.onInsert.heading') },
          on_update: { list: screenHookList('on_update'), setList: (n) => setScreenHook('on_update', n), label: t('settings.screens.onUpdate.heading') },
          on_delete: { list: screenHookList('on_delete'), setList: (n) => setScreenHook('on_delete', n), label: t('settings.screens.onDelete.heading') },
          on_duplicate: { list: screenHookList('on_duplicate'), setList: (n) => setScreenHook('on_duplicate', n), label: t('settings.screens.onDuplicate.heading', 'On duplicate (row hook)') },
          row_menu:  { list: rowMenu,   setList: setRowMenu,                          label: t('settings.screens.rowmenu.heading') },
        }
        const cfg = lookup[openEditor.listKey]
        return (
          <ActionEditorModal
            actions={cfg.list}
            onChange={cfg.setList}
            path={openEditor.path}
            onPathChange={(p) => setOpenEditor(p && p.length > 0 ? { listKey: openEditor.listKey, path: p } : null)}
            defs={defs}
            effectiveConnector={effectiveConnector}
            onEditQuery={onEditQueryRaise}
            rootLabel={cfg.label}
            screenReadColumns={screenReadColumns}
            extraSourceOptions={openEditor.listKey === 'on_duplicate' ? duplicateSourceOptions : undefined}
            onClose={() => setOpenEditor(null)}
          />
        )
      })()}
    </Stack>
  )
}
