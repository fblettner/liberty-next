// Builds the column definitions for a SELECT result and hands them to the shared
// DataTable (sort / filter / search / group / hide-reorder / resize / export / paginate),
// plus owns the **batch edit mode** when the query has writable companions:
//   Edit → the whole grid becomes editable; "+ Add row" appends a blank row; a per-row ×
//   marks an existing row for deletion (or removes a new one); Save commits *all* changes —
//   each edited existing row via `update_query` (`_put`), each new row via `insert_query`
//   (`_post`), each deleted row via `delete_query` (`_delete`) — then refetches; Cancel discards.
//
// Per-column display rules (BOOLEAN / ENUM / LOOKUP — the v2 form of v1's dd_rules) are
// applied per cell via services/cells.ruleCell; a LOOKUP column is split into "<label> (ID)"
// (the raw code, editable) + "<label>" (the resolved label, derived & read-only); lookup
// queries are fetched once via services/lookups.useLookupBatch. Each column also declares its
// filter kind so DataTable's per-column filter row shows the right control.
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import { useLocation, useNavigate } from 'react-router-dom'
import type { ColumnDef, VisibilityState } from '@tanstack/react-table'
import styled from '@emotion/styled'
import * as XLSX from 'xlsx'
import { Check, X, Plus, Copy, ClipboardPaste, Upload, Edit3, Zap, FileSpreadsheet } from 'lucide-react'
import type { Column, QueryResult } from '../../types/connectors'
import type { Action, ColumnGroup, PromptField, ScreenDetail } from '../../types/screens'
import { api, ApiError, authHeaders } from '../../api/client'
import { Banner, Checkbox, SearchSelect } from '../../common'
import { DataTable } from '../../common/DataTable'
import { genericFilterFn, selectFilterFn, type FilterKind, type FilterMeta } from '../../common/DataTableFilter'
import { enumMap, ruleCell } from '../../services/cells'
import { lookupKey, useLookupTables, lookupCellColumns, type LookupData, type LookupSpec } from '../../services/lookups'
import { useTabs } from '../../tabs/TabsContext'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import { CellSpan } from './styled'
import { ScreenDialog, type DialogMode } from './ScreenDialog'
import { ActionPromptDialog } from './ActionPromptDialog'
import { entityKeyOf, resolveBindList, forcedDefault, type Row as CtxRow } from './dialogHelpers'
import { saveScreenRow, deleteScreenRow } from './saveScreenRow'
import { runChain } from './actionRunner'

type DataRow = Record<string, unknown>
type Align = CSSProperties['textAlign']
type EditCtrl = 'enum' | 'lookup' | 'boolean' | 'date' | 'number' | 'text'

function colHeader(c: Column): string { return c.label ?? c.name }
// Column alignment: an explicit `align` hint wins; otherwise the natural default — booleans
// (✓/✗) centered, numbers right-aligned (like every spreadsheet), everything else left.
function cellAlign(c: Column): 'left' | 'right' | 'center' | undefined {
  if (c.align === 'left' || c.align === 'right' || c.align === 'center') return c.align
  if (c.rule?.kind === 'boolean') return 'center'
  if (isNumericish((c.format ?? '').toLowerCase(), (c.type ?? '').toLowerCase())) return 'right'
  return undefined
}
function isNumericish(fmt: string, typ: string) { return fmt === 'number' || fmt === 'integer' || /int|numeric|decimal|float|double|real/.test(typ) }
// jdedate = JDE Julian date: integer SQL type, but read as ISO YYYY-MM-DD and re-encoded to Julian
// on Save — so it edits with a calendar, not as a number. Keep in sync with FieldRow.isDateish.
function isDateish(fmt: string, typ: string) { return fmt === 'date' || fmt === 'jdedate' || /date|timestamp/.test(typ) }
function filterKindOf(c: Column): FilterKind {
  if (c.rule?.kind === 'boolean') return 'boolean'
  if (c.rule?.kind === 'enum') return 'enum'
  const fmt = (c.format ?? '').toLowerCase(), typ = (c.type ?? '').toLowerCase()
  if (isDateish(fmt, typ)) return 'date'
  if (isNumericish(fmt, typ)) return 'number'
  return 'text'
}
function editCtrlOf(c: Column): EditCtrl {
  // Rule-driven widgets first — match the dialog's FieldRow conventions so inline-edit
  // and dialog-edit feel identical (v1 parity: every form-control surface used dropdowns
  // for ENUM / LOOKUP and a checkbox for BOOLEAN, never a plain text input).
  if (c.rule?.kind === 'enum') return 'enum'
  if (c.rule?.kind === 'lookup') return 'lookup'
  if (c.rule?.kind === 'boolean') return 'boolean'
  const fmt = (c.format ?? '').toLowerCase(), typ = (c.type ?? '').toLowerCase()
  if (isDateish(fmt, typ)) return 'date'
  if (isNumericish(fmt, typ)) return 'number'
  return 'text'
}
const filterPropsFor = (
  kind: FilterKind,
  options?: { value: string; label: string; mono?: string; cells?: string[] }[],
  align?: FilterMeta['align'],
  cellColumns?: { label: string; filterable?: boolean }[],
) =>
  // enum / lookup compare a trimmed code against the (possibly CHAR-padded) raw value → trim-tolerant
  // ``selectFilterFn``. Boolean keeps the built-in strict ``equals``.
  kind === 'enum' || kind === 'lookup'
    ? { filterFn: selectFilterFn, meta: { filter: { kind, options, cellColumns }, align } as FilterMeta }
    : kind === 'boolean'
      ? { filterFn: 'equals' as const, meta: { filter: { kind, options }, align } as FilterMeta }
      : { filterFn: genericFilterFn, meta: { filter: { kind }, align } as FilterMeta }

// (``withUpper`` / ``originalKeys`` — the UPPERCASE-key duplication + the pre-edit
// ``<NAME>_ORIGINAL`` WHERE binds — now live in ``saveScreenRow`` / ``dialogHelpers``, the single
// write/delete path shared with the dialog.)

// Slice 6 — row context menu action runner: resolve a list of ParamBinds against a row's live
// values. `value` binds are literal; `source` binds read another column on the same row
// (``resolveRowBinds`` lived here in the slice-6 row-menu runner — replaced by the shared
// :func:`runChain` from ``actionRunner.ts`` in Slice B. The chain runner's ``resolveBinds`` /
// ``resolveSource`` handle the same case-insensitive form-field fallback *and* the new
// dotted-path chain-context refs (`<step_id>.first_row.<col>` / `INPUT.<X>` / `loop.<field>`).)

// ── edit-mode controls ──────────────────────────────────────────────────────
const EditInput = styled.input`
  width: 100%; box-sizing: border-box; background: ${colors.bg.input};
  border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  padding: 2px 6px; outline: none;
  &:focus { border-color: ${colors.blue.border}; box-shadow: 0 0 0 2px ${colors.blue.bg}; }
`
const CheckBox = styled.span<{ $on: boolean }>`
  width: 14px; height: 14px; flex-shrink: 0; border-radius: 3px;
  border: 1.5px solid ${({ $on }) => ($on ? colors.blue.main : colors.border)};
  background: ${({ $on }) => ($on ? colors.blue.main : 'transparent')};
  display: inline-flex; align-items: center; justify-content: center; cursor: pointer; color: #fff;
  transition: background 0.12s, border-color 0.12s;
`
const StatusCell = styled.div`display: inline-flex; align-items: center; gap: 5px; font-size: ${fontSize.micro};`
const StatusMark = styled.span<{ $tone: 'new' | 'dirty' | 'deleted' }>`
  font-weight: 700;
  color: ${({ $tone }) => ($tone === 'new' ? colors.green.main : $tone === 'deleted' ? colors.red.main : colors.orange.main)};
`
const RowXBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: transparent;
  color: ${colors.text.muted}; cursor: pointer; padding: 0;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; }
`
const TbBtn = styled.button<{ $tone?: 'primary' }>`
  display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 10px; border-radius: ${radius.md};
  border: 1px solid ${({ $tone }) => ($tone === 'primary' ? colors.blue.border : colors.border)};
  background: ${({ $tone }) => ($tone === 'primary' ? colors.blue.bg : colors.bg.input)};
  color: ${({ $tone }) => ($tone === 'primary' ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer; white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  &:hover:not(:disabled) { background: var(--hover-subtle); color: ${colors.text.primary}; }
  &:disabled { opacity: 0.4; cursor: default; }
`

// Row context menu (slice 6) — a small floating panel anchored at the right-click coords.
// `position: fixed` so the page scroll doesn't drift the menu out of place mid-action; sized to
// the content with a sane min/max so a single-action menu doesn't shrink to a sliver.
const RowMenuBox = styled.div`
  position: fixed; z-index: 500; min-width: 200px; max-width: 320px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.dropdown}; color: ${colors.text.primary};
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.32);
  display: flex; flex-direction: column; padding: 4px 0;
`
const RowMenuItem = styled.button<{ $busy?: boolean }>`
  display: flex; align-items: center; gap: 8px; padding: 7px 14px; border: none; background: transparent;
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  cursor: ${({ $busy }) => ($busy ? 'default' : 'pointer')};
  opacity: ${({ $busy }) => ($busy ? 0.5 : 1)};
  text-align: left;
  & .id { font-family: ${fonts.mono}; color: ${colors.text.muted}; font-size: ${fontSize.micro}; margin-left: auto; }
  &:hover { background: var(--hover-subtle); }
  &:disabled { opacity: 0.4; cursor: default; }
`
const RowMenuErr = styled.div`
  padding: 6px 14px 8px; color: ${colors.red.main};
  font-size: ${fontSize.micro}; font-family: ${fonts.sans}; max-width: 320px; word-break: break-word;
`

function EditCell({
  ctrl, column, defaultText, onChange, lookupOptions, lookupRows, onLookupReturnValues, narrowBy, displayFields, cellColumns,
}: {
  ctrl: EditCtrl; column: Column; defaultText: string; onChange: (v: unknown) => void
  /** The lookup's ``display_fields`` — extra row columns appended (dimmed, " · ") after each
   *  option's label so similar codes are distinguishable, same as the dialog dropdown. */
  displayFields?: string[]
  /** Per-cell-column metadata (label + filterable) for the dropdown's facet-chip filter. */
  cellColumns?: { label: string; filterable?: boolean }[]
  /** ``{value: label}`` map for LOOKUP columns — already resolved by the surrounding
   *  ``lookupMaps`` (one fetch per unique spec; v2 mirrors v1's "fetch the lookup once,
   *  populate every cell from the same set"). Undefined → not yet loaded → render a
   *  loading state so the user sees the dropdown is coming. */
  lookupOptions?: Map<string, string>
  /** Raw rows from the lookup query — used to resolve ``return_params`` (v1's
   *  ly_lkp_params lkp_dir='OUT'): after a pick, the matching row's other columns flow
   *  back through ``onLookupReturnValues``. Same cache as ``lookupOptions``. */
  lookupRows?: Record<string, unknown>[]
  /** Hook fired on a LOOKUP pick when the rule has ``return_params`` — receives a
   *  ``{dd_id: value}`` map of the picked row's matching columns. The parent
   *  (ResultTable) maps each dd to a sibling grid column and writes them via
   *  ``editChange`` in the same row. */
  onLookupReturnValues?: (returnValues: Record<string, unknown>) => void
  /** Cascading filter for LOOKUP cells (v1's ly_tbl_filters / Column.filter_from). When
   *  the cell's column declares ``filter_from = [{source: "SY_COL", column: "SY"}, …]``
   *  and the same row's ``SY_COL`` cell has a value, this carries
   *  ``[{column: "SY", value: <SY_COL's row value>}, …]`` — the lookup options narrow to
   *  the rows whose ``SY`` matches. v1 had this for bulk-edit DROPDOWNS too (e.g.
   *  DRRT depends on DRSY); v2 wires it through here so the per-row dropdowns cascade
   *  exactly like the form FieldRow's lookup_param_binds do, but client-side over the
   *  once-fetched rows (no per-row fetches). */
  narrowBy?: { column: string; value: unknown }[]
}) {
  // ENUM and LOOKUP use the same searchable dropdown as the dialog's FieldRow —
  // ``SearchSelect`` from common. v1's lookups for UDC / role-id-style tables had
  // hundreds of entries; a native ``<select>`` is unusable at that size (no search,
  // no keyboard filter beyond first-char jump). SearchSelect portals out of the
  // grid cell so the panel isn't clipped by the table's overflow. Both branches are
  // *controlled* — ``value={defaultText}`` reads the current edits-aware value on
  // every TanStack re-render, so a programmatic edit (paste, copy, fill-down) flows
  // through. ``mono`` shows the raw code beside the label so codes stay scannable.
  if (ctrl === 'enum' && column.rule?.kind === 'enum') {
    const opts = column.rule.values.map((v) => ({ value: v.value, label: v.label, mono: v.value }))
    return (
      <SearchSelect
        value={defaultText}
        onChange={(v) => onChange(v === '' ? null : v)}
        options={opts}
        anyLabel="—"
        placeholder=""
      />
    )
  }
  if (ctrl === 'lookup' && column.rule?.kind === 'lookup') {
    const rule = column.rule
    const ready = !!lookupOptions
    // When the cell has active cascading deps (filter_from + same-row sibling values),
    // build options from the *filtered* lookup rows so the dropdown narrows. Otherwise
    // use the pre-projected value→label map directly — faster, no per-row scan.
    const active = (narrowBy ?? []).filter((n) => n.value != null && n.value !== '')
    // ``display_fields`` → code: [cell, cell] map, built once from the raw rows; each becomes an
    // extra table column in the dropdown (same as the dialog picker). Consistent length so columns
    // line up across options.
    const cellsByCode = (() => {
      if (!displayFields?.length || !lookupRows) return undefined
      const valKey = rule.value
      const m = new Map<string, string[]>()
      for (const r of lookupRows) {
        const v = r[valKey] ?? r[valKey.toLowerCase()] ?? r[valKey.toUpperCase()]
        if (v == null) continue
        m.set(String(v), displayFields.map((f) => {
          const x = r[f] ?? r[f.toLowerCase()] ?? r[f.toUpperCase()]
          return x == null ? '' : String(x).trim()
        }))
      }
      return m
    })()
    type Opt = { value: string; label: string; mono: string; cells?: string[] }
    let opts: Opt[]
    if (ready && active.length > 0 && lookupRows) {
      // Case-insensitive column lookup on the raw rows: Postgres folds unquoted columns
      // to lowercase, the dictionary uses uppercase. Compare as strings (lookups are
      // code-based: "01" matches "01", coercion stays out of the picture).
      const valKey = rule.value
      const labKey = rule.label
      const matches = lookupRows.filter((r) => active.every(({ column: col, value }) => {
        const rv = r[col] ?? r[col.toLowerCase()] ?? r[col.toUpperCase()]
        // Trim-tolerant: JDE pads / right-justifies UDC codes, so the dependent column and the
        // sibling cell value can differ in padding for the same logical code (matches the
        // filter-panel cascade fix in services/lookups.ts).
        return rv != null && String(rv).trim() === String(value).trim()
      }))
      opts = matches
        .map((r): Opt | null => {
          const v = r[valKey] ?? r[valKey.toLowerCase()] ?? r[valKey.toUpperCase()]
          const l = r[labKey] ?? r[labKey.toLowerCase()] ?? r[labKey.toUpperCase()]
          return v == null
            ? null
            : { value: String(v), label: l == null ? String(v) : String(l), mono: String(v), cells: cellsByCode?.get(String(v)) }
        })
        .filter((o): o is Opt => o !== null)
        .sort((a, b) => a.label.localeCompare(b.label))
    } else if (ready) {
      opts = [...lookupOptions!.entries()]
        .sort(([, a], [, b]) => a.localeCompare(b))
        .map(([value, label]) => ({ value, label, mono: value, cells: cellsByCode?.get(value) }))
    } else {
      opts = []
    }
    // Lookup-pick handler — writes the picked value, then applies the column's ``return_binds``:
    // for each {param, column}, read the picked row's ``param`` column (case-insensitive) and fire
    // ``onLookupReturnValues`` with a ``{targetColumn: value}`` map. The parent (ResultTable)
    // writes each to the sibling cell on the same row. Explicit replacement for v1's auto-by-dd.
    const handlePick = (picked: string) => {
      onChange(picked === '' ? null : picked)
      const binds = column.return_binds ?? []
      if (!picked || binds.length === 0 || !lookupRows || !onLookupReturnValues) return
      const valueCol = rule.value
      const row = lookupRows.find((r) => {
        const rv = r[valueCol] ?? r[valueCol.toLowerCase()] ?? r[valueCol.toUpperCase()]
        return rv != null && String(rv) === picked
      })
      if (!row) return
      const lcRow = new Map(Object.entries(row).map(([k, v]) => [k.toLowerCase(), v]))
      const aux: Record<string, unknown> = {}
      for (const b of binds) {
        const v = lcRow.get(b.param.toLowerCase())
        if (v !== undefined) aux[b.column] = v
      }
      if (Object.keys(aux).length > 0) onLookupReturnValues(aux)
    }
    return (
      <SearchSelect
        value={defaultText}
        onChange={handlePick}
        options={opts}
        cellColumns={cellColumns}
        anyLabel="—"
        loading={!ready}
        placeholder=""
      />
    )
  }
  if (ctrl === 'boolean' && column.rule?.kind === 'boolean') {
    // v1 parity: boolean columns render as a checkbox in edit mode. The rule's
    // ``true_value`` / ``false_value`` (explicit or inferred via DictionaryFile) drive
    // what we actually send: NOMASX1's CSI_STATUS goes "Y" / "N"; user status goes
    // "01" / null. Mirrors the dialog's FieldRow boolean branch.
    const trueV = column.rule.true_value
    const falseV = column.rule.false_value ?? null
    const checked = defaultText === trueV
    return (
      <Checkbox
        checked={checked}
        onChange={(v) => onChange(v ? trueV : falseV)}
        label={checked ? trueV : (falseV ?? '—')}
      />
    )
  }
  const type = ctrl === 'date' ? 'date' : ctrl === 'number' ? 'number' : 'text'
  const dv = ctrl === 'date' && defaultText ? defaultText.slice(0, 10) : defaultText
  return (
    <EditInput type={type} defaultValue={dv} onChange={(e) => {
      const raw = e.target.value
      if (raw === '') onChange(null)
      else if (ctrl === 'number') onChange(Number(raw))
      else onChange(raw)
    }} />
  )
}

// A column with a `visible_when` hint (v1's cdn_*) drops out of the grid when a server-filter is
// set to a value outside its allowed set. `visible_when` is a list of `{field, value}` conditions,
// ALL of which must hold; a condition holds when its `field` filter is unset OR its value is the
// (one allowed) `value` / one of the `value` list — i.e. it only ever *hides* on an explicit
// mismatch, never on "no filter". (Accepts a bare `{field, value}` too — treated as a one-item list.)
function columnVisibleNow(c: Column, activeFilters: Record<string, string>): boolean {
  const vw = c.visible_when
  if (!vw) return true
  const conds = Array.isArray(vw) ? vw : [vw]
  return conds.every(({ field, value }) => {
    const raw = activeFilters[field]
    if (raw == null || raw === '') return true
    // Trim both sides: the active filter value can come from a JDE UDC lookup whose code reads
    // back space-padded ("1         "), while the visible_when allowed values are clean config
    // ("1"). Without trimming, a padded filter hides every conditional column (the bug after
    // moving the security-type lookup to read JDE directly).
    const v = String(raw).trim()
    return Array.isArray(value) ? value.some((x) => String(x).trim() === v) : String(value).trim() === v
  })
}

export function ResultTable({
  result, connector, query, updateQuery, insertQuery, deleteQuery, keyColumns, onSaved, runControl, maxRowsControl, activeFilters, screen, addSeed, nestedDialog,
}: {
  result: QueryResult
  connector: string
  query: string
  updateQuery?: string | null
  insertQuery?: string | null
  deleteQuery?: string | null
  keyColumns?: string[]  // result columns that identify a row (v1's col_key) — used by the Excel import to match
  onSaved?: () => void
  runControl?: React.ReactNode    // the Run button — sits just right of the grid's search box
  maxRowsControl?: React.ReactNode  // the Max-rows input — sits at the far right, before the Filters button
  activeFilters?: Record<string, string>  // current server-filter values — drives `visible_when` columns
  /** Screen detail (with dialog body) for this (connector, query) — when present, the toolbar
   *  shows Add Row / Edit Row buttons that open the ScreenDialog form instead of the inline
   *  grid editor. When null/missing the existing inline batch-edit flow is the only path. */
  screen?: ScreenDetail | null
  /** Default values to pre-fill the Add dialog with — used by a nested table to seed the parent's
   *  FK (e.g. {APPS_ID: 7}) so a new child row opens already tied to its parent. */
  addSeed?: Record<string, unknown>
  /** Render this grid's Add/Edit ScreenDialog as a NESTED sub-dialog (smaller, raised z-index) —
   *  set when ResultTable is embedded inside a parent dialog's nested-table tab. */
  nestedDialog?: boolean
}) {
  const { t } = useTranslation()
  // Used by the NavigateAction runtime — opens the target TableView via react-router's SPA nav,
  // which keeps the workspace's tab manager in charge (no full page reload).
  const navigate = useNavigate()
  // Drill-source bookkeeping — when the operator follows a row_click_route, we pass the
  // active tab's id + the source path through location.state so the destination's
  // BackButton can close the drill tab + return in one click ("same tab" feel, see UI 3).
  const location = useLocation()
  const { activeId } = useTabs()
  const { sharedActions } = useWorkspace()
  const canEdit = !!(updateQuery || insertQuery)
  // Whether NEW records may be created here: an insert query exists AND the screen doesn't opt out
  // via ``disable_add``. Gates every creation affordance (Add button, add-row / duplicate / paste /
  // import) so an edit-only screen can keep its insert_query for the save path yet hide all the
  // "make a new record" entry points. Edit / delete of existing rows is unaffected.
  const canInsert = !!insertQuery && !screen?.disable_add
  // ``Screen.editable`` (default true) gates INLINE GRID editing — the bulk-edit mode. False → the
  // grid is view-only (a dialog, if any, still edits row-by-row). ``Screen.uploadable`` (default
  // false) gates the Excel/CSV Import button. Undefined screen (ad-hoc SQL run) keeps the old
  // behaviour: editable, not uploadable. Both were dormant before — honoured here so a screen
  // migrated as view-only / no-import actually is.
  const canBulkEdit = canEdit && screen?.editable !== false
  const canImport = canInsert && !!screen?.uploadable
  const hasDialog = !!(screen?.dialog && (screen.update_query || screen.insert_query))
  // Dialog state — opens on Add / Edit-row when the screen has a `dialog`. `dlgRow` is the
  // initial values; `mode='edit'` also drives the `:<COL>_ORIGINAL` binds inside ScreenDialog.
  const [dlgOpen, setDlgOpen] = useState(false)
  const [dlgMode, setDlgMode] = useState<DialogMode>('edit')
  const [dlgRow, setDlgRow] = useState<Record<string, unknown>>({})
  const openDialogForRow = useCallback((row: Record<string, unknown>) => {
    setDlgRow(row); setDlgMode('edit'); setDlgOpen(true)
  }, [])
  const openDialogForAdd = useCallback(() => {
    // Seed the Add dialog with any defaults (a nested table passes the parent FK so the new child
    // row opens already linked to its parent); empty for a normal screen.
    setDlgRow({ ...(addSeed ?? {}) }); setDlgMode('add'); setDlgOpen(true)
  }, [addSeed])

  // Row-click → sibling-screen dialog (v1's "Display Properties" pattern, promoted at migration
  // time on screens without their own dialog). When ``screen.row_click_screen`` is set and the
  // user clicks a row, we:
  //   1. fetch the target screen's detail (so we know its dialog / read_query / CRUD names)
  //   2. fetch the target row by binding the clicked row's columns into the target read_query
  //   3. open the target's ScreenDialog (in edit mode if a row came back, add mode otherwise)
  // The target screen's own connector is used for the queries (falls back to the parent's, then
  // app name). The proxy state lives here, independent of the main ``dlgOpen`` flow above.
  const [proxyScreen, setProxyScreen] = useState<ScreenDetail | null>(null)
  const [proxyColumns, setProxyColumns] = useState<Column[]>([])
  const [proxyRow, setProxyRow] = useState<Record<string, unknown>>({})
  const [proxyMode, setProxyMode] = useState<DialogMode>('edit')
  const [proxyOpen, setProxyOpen] = useState(false)
  const [proxyLoading, setProxyLoading] = useState(false)
  const [proxyError, setProxyError] = useState<string | null>(null)

  const hasRowClickProxy = !hasDialog && !!screen?.row_click_screen
  // Row-click → SPA route. The escape hatch for screens that drill into a hand-written React
  // page rather than another Screen dialog — needed when the destination renders things SQL
  // can't (live log streaming, custom charts). The route template uses ``{column_name}``
  // placeholders that resolve against the clicked row; values are URL-encoded. Wins over
  // hasDialog / hasRowClickProxy when set — opting into a route is the more specific intent.
  //
  // Opens as a **workspace tab** (the in-app tab strip), not a browser tab — same UX as
  // clicking a query in the connector tree. The source list stays open as its own tab; the
  // destination joins the strip and stays active until closed. ``/nomaflow/runs/<id>`` maps
  // to the ``nomaflow_run`` tab kind (TabsContext.tabPath ↔ App's TabRoute marker); routes
  // outside that allow-list fall back to plain SPA navigation.
  const rowClickRoute = screen?.row_click_route ?? null
  const openRouteForRow = useCallback((row: Record<string, unknown>) => {
    if (!rowClickRoute) return
    // Replace every ``{name}`` with the row's column value (URL-encoded so columns containing
    // ``/`` / ``?`` / ``#`` don't break the route). An unknown column produces an empty segment
    // — the screen's validator caught that at config-load time, this is the runtime safety net.
    //
    // Case-insensitive fallback (matches ``cur`` for cell rendering): the screen's column
    // hints often declare names in operator-friendly UPPERCASE (``CLA_RUN_ID``) while the actual
    // row keys returned by the query are lowercase (Postgres folds unquoted identifiers). A
    // strict exact-key lookup would substitute empty strings here — the placeholder validator
    // accepted the route because the hint matched, but the runtime would render
    // ``/nomaflow/runs/`` with an empty id. Match the hint case first, then fall back through
    // the row's own keys looking for a case-insensitive hit.
    const url = rowClickRoute.replace(/\{([^{}]+)\}/g, (_, name: string) => {
      let v = row[name]
      if (v === undefined) {
        const lk = name.toLowerCase()
        for (const k of Object.keys(row)) {
          if (k.toLowerCase() === lk) { v = row[k]; break }
        }
      }
      return v == null ? '' : encodeURIComponent(String(v))
    })
    // Plain SPA navigation — the destination URL's TabRoute marker (App.tsx) auto-creates
    // the workspace tab if needed (no need to pre-create here). We thread the source
    // pathname + active tab id through location.state so the destination's BackButton can
    // (a) navigate back and (b) close the drill tab in one click — that's the "same tab"
    // feel the operator wants without re-architecting URL↔tab coupling.
    navigate(url, { state: { from: location.pathname, fromTabId: activeId } })
  }, [rowClickRoute, navigate, location.pathname, activeId])
  const openProxyForRow = useCallback(async (row: Record<string, unknown>) => {
    if (!screen?.row_click_screen) return
    setProxyOpen(true); setProxyLoading(true); setProxyError(null)
    try {
      // Effective connector + app of the target screen. ``row_click_connector`` is only set
      // when it differs from the parent — same convention the migrator uses.
      const targetConn = screen.row_click_connector || screen.connector || connector
      const detail = await api.get<ScreenDetail>(
        `/api/screens/${encodeURIComponent(screen.app)}/${encodeURIComponent(screen.row_click_screen)}`,
      )
      // Resolve the row_click binds against the clicked row. The dialog needs both the row
      // (matching its own columns) AND the binds to set up :ORIGINAL keys on save.
      const bound = resolveBindList(screen.row_click_binds, row)
      const qs = Object.entries(bound)
        .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
        .join('&')
      const url = `/api/sql/${encodeURIComponent(targetConn)}/${encodeURIComponent(detail.read_query)}${qs ? `?${qs}` : ''}`
      const res = await api.get<QueryResult>(url)
      setProxyScreen(detail)
      setProxyColumns(res.columns)
      // 1 row → edit; 0 rows → add (seed the FK columns from the binds so insert ties to parent)
      if (res.rows.length > 0) {
        setProxyRow(res.rows[0] as Record<string, unknown>)
        setProxyMode('edit')
      } else {
        setProxyRow({ ...bound })
        setProxyMode('add')
      }
    } catch (e) {
      setProxyError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setProxyLoading(false)
    }
  }, [screen, connector])
  // Row context menu (slice 6) — fires `Screen.row_menu` actions on right-click. The menu is a
  // floating overlay anchored at the click coords. We store the row + position; the menu component
  // closes on click-outside / Escape. Each item's ParamBinds resolve against the row's values
  // (the v2 form of v1's row-level action context — same Action shape used by dialog on_save).
  const rowMenu: Action[] = useMemo(
    () => (Array.isArray(screen?.row_menu) ? (screen!.row_menu as Action[]) : []),
    [screen],
  )
  const [menuState, setMenuState] = useState<{ row: DataRow; x: number; y: number } | null>(null)
  const closeMenu = useCallback(() => setMenuState(null), [])
  // `editMode` is declared further down; we read it via a ref so the openRowMenu callback can sit
  // here (next to its peers) without a TDZ issue. The ref is kept in sync below via a small effect.
  const editModeRef = useRef(false)
  const openRowMenu = useCallback((row: DataRow, e: React.MouseEvent<HTMLTableRowElement>) => {
    if (rowMenu.length === 0 || editModeRef.current) return
    setMenuState({ row, x: e.clientX, y: e.clientY })
  }, [rowMenu.length])
  // Prompt-before-fire plumbing — shared by ``runRowAction`` and ``runScreenAction``. Same
  // imperative-from-async pattern the ScreenDialog uses: the action's ``prompt_fields`` opens a
  // sub-dialog, the resolver returns the entered values (or null on cancel), and the runner
  // merges them into the resolution context before the action's ParamBinds run.
  const [pendingPrompt, setPendingPrompt] = useState<
    | { fields: PromptField[]; title: string; cols: number | null; submitLabel: string | null }
    | null
  >(null)
  const promptResolveRef = useRef<((v: CtxRow | null) => void) | null>(null)
  const requestPrompt = useCallback((spec: { fields: PromptField[]; title: string | null; cols: number | null; submitLabel: string | null }, fallbackTitle: string): Promise<CtxRow | null> => {
    return new Promise<CtxRow | null>((resolve) => {
      promptResolveRef.current = resolve
      setPendingPrompt({
        fields: spec.fields,
        title: spec.title || fallbackTitle,
        cols: spec.cols,
        submitLabel: spec.submitLabel,
      })
    })
  }, [])
  const handlePromptSubmit = useCallback((v: CtxRow) => {
    const r = promptResolveRef.current; promptResolveRef.current = null
    setPendingPrompt(null)
    r?.(v)
  }, [])
  const handlePromptCancel = useCallback(() => {
    const r = promptResolveRef.current; promptResolveRef.current = null
    setPendingPrompt(null)
    r?.(null)
  }, [])

  // Row-menu action runner — delegates to :func:`runChain` from ``actionRunner.ts`` (Slice B).
  // The runner handles ChainAction recursion, IfAction branching, LoopAction iteration, and
  // dotted-path ``ParamBind.source`` resolution against the chain context — so a migrated
  // multi-step workflow (NOMAJDE's F00926 "Import Security" landed on a row-menu, hypothetically)
  // executes end-to-end. ``formCtx = row`` so plain ``source: 'USR_ID'`` references still read
  // from the clicked row (the existing v2 / hand-written row-menu semantics). ``deps.navigate``
  // wires the v1 "drill into another table" pattern — opens ``/sql/<connector>/<to>`` with the
  // resolved binds as URL params; the destination's TableView seeds its param form from those.
  const [menuBusy, setMenuBusy] = useState<string | null>(null)  // action id while it's running
  const [menuError, setMenuError] = useState<string | null>(null)
  const runRowAction = useCallback(async (a: Action, ctx: DataRow) => {
    setMenuBusy(a.id); setMenuError(null)
    const result = await runChain([a], {}, ctx, {
      defaultConnector: connector,
      requestPrompt,
      sharedActions: sharedActions ?? undefined,
      navigate: (to, conn, params, screen) => {
        const qs = new URLSearchParams()
        for (const [k, v] of Object.entries(params)) {
          if (v != null && v !== '') qs.set(k, String(v))
        }
        // A pinned screen opens the dedicated /screen/<conn>/<id> route (that exact screen's
        // columns / filters / dialog apply); otherwise the query route, resolved by read query.
        const base = screen
          ? `/screen/${encodeURIComponent(conn)}/${encodeURIComponent(screen)}`
          : `/sql/${encodeURIComponent(conn)}/${encodeURIComponent(to)}`
        const url = base + (qs.toString() ? `?${qs.toString()}` : '')
        // Close the menu *before* navigating — leaving it open during the route change makes the
        // overlay flicker on the destination page until the document-mousedown listener fires.
        setMenuBusy(null); closeMenu()
        navigate(url)
      },
    })
    setMenuBusy(null)
    if (!result.ok) { setMenuError(result.error || (a.label || a.id)); return }
    if (result.cancelled) { closeMenu(); return }   // soft cancel from a prompt — no error
    closeMenu(); onSaved?.()
    // Notify-action messages collect on ``result.warnings``; surface them in the menu's
    // error/status slot (no global toast yet — same convention as before).
    if (result.warnings.length > 0) {
      // eslint-disable-next-line no-console
      console.info('row-menu warnings:', result.warnings)
    }
  }, [connector, onSaved, closeMenu, navigate, requestPrompt, sharedActions])
  // Screen-level actions — v1's NOMAJDE toolbar buttons ("Create Role" / "Reset Password" / etc.)
  // attach here. Fire with **no row context** (the user uses row_menu for row-bound actions);
  // ParamBinds resolve to literal `value`s only — a `source` bind against an unset form falls
  // through to "no value". For more complex flows the operator wires a `confirm` or a future
  // input-dialog action; the runner uses the same Action shape as on_save / row_menu.
  const screenActions: Action[] = useMemo(() => (screen?.actions ?? []) as Action[], [screen])
  const [actionBusy, setActionBusy] = useState<string | null>(null)
  const [actionStatus, setActionStatus] = useState<{ message: string; tone: 'ok' | 'error' } | null>(null)

  // Workbook export — fire ``POST /api/screens/{app}/{id}/export``, read the streamed bytes,
  // hand the browser an anchor with a blob URL so it downloads with the response's
  // Content-Disposition filename. Surfaced on the toolbar only when ``screen.export`` is set.
  const [exportBusy, setExportBusy] = useState(false)
  const runWorkbookExport = useCallback(async () => {
    if (!screen) return
    setExportBusy(true); setActionStatus(null)
    try {
      const resp = await fetch(
        `/api/screens/${encodeURIComponent(screen.app)}/${encodeURIComponent(screen.id)}/export`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({}),
        },
      )
      if (!resp.ok) {
        const detail = await resp.text().catch(() => '')
        throw new Error(`HTTP ${resp.status} ${resp.statusText} — ${detail.slice(0, 200)}`)
      }
      // Pull the filename out of the Content-Disposition (the backend names it ``screen.xlsx``
      // / ``screen.zip`` / a per-group ``screen_<value>.xlsx``); fall back to a sensible default.
      const dispo = resp.headers.get('Content-Disposition') || ''
      const m = /filename="([^"]+)"/.exec(dispo)
      const filename = m?.[1] || `${screen.id}.xlsx`
      const blob = await resp.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = filename
      document.body.appendChild(a); a.click()
      document.body.removeChild(a); URL.revokeObjectURL(url)
    } catch (e) {
      setActionStatus({ message: (e as Error).message, tone: 'error' })
    } finally {
      setExportBusy(false)
    }
  }, [screen])
  const runScreenAction = useCallback(async (a: Action) => {
    setActionBusy(a.id); setActionStatus(null)
    // Toolbar actions have no row context — ``formCtx = {}``. Prompt-collected values land
    // under ``INPUT.<name>`` in the chain context (the runner orchestrates the prompt-before-
    // fire). Same ``runChain`` recursion the row-menu uses → ChainAction / IfAction / LoopAction
    // workflows fire correctly from a toolbar button too (NOMAJDE's "Create Role" etc.).
    const result = await runChain([a], {}, {}, {
      defaultConnector: connector,
      requestPrompt,
      sharedActions: sharedActions ?? undefined,
      navigate: (to, conn, params, screen) => {
        const qs = new URLSearchParams()
        for (const [k, v] of Object.entries(params)) {
          if (v != null && v !== '') qs.set(k, String(v))
        }
        const base = screen
          ? `/screen/${encodeURIComponent(conn)}/${encodeURIComponent(screen)}`
          : `/sql/${encodeURIComponent(conn)}/${encodeURIComponent(to)}`
        navigate(base + (qs.toString() ? `?${qs.toString()}` : ''))
      },
    })
    setActionBusy(null)
    if (!result.ok) {
      setActionStatus({ message: result.error || (a.label || a.id), tone: 'error' })
      return
    }
    if (result.cancelled) return   // soft cancel — no banner
    // Intentional notify messages → status banner. Otherwise a clean "<label> · done" — a
    // successful run reports success, NOT a per-step dump (0-row notices are console diagnostics).
    const msg = result.warnings.length > 0 ? result.warnings.join(' · ') : `${a.label || a.id} · ${t('common.done')}`
    setActionStatus({ message: msg, tone: 'ok' })
    onSaved?.()
  }, [connector, onSaved, navigate, requestPrompt, sharedActions])

  // the columns to actually show: drop any whose `visible_when` filter doesn't match right now
  // (TableView passes a memoized `activeFilters`, so this stays referentially stable across
  // re-renders). Also drop password-typed columns globally — a stored hash / ENC: blob should
  // never appear as a cleartext cell in any grid (the audit table on settings_applications
  // exposed this — v1 marked it visible because the audit log records the ENC value, but in
  // v2 we render it as "•••" everywhere it'd otherwise show). The ScreenDialog still lets the
  // user *set* a new password via its masked input — table is read-only context only.
  const shownColumns = useMemo(
    () => result.columns.filter(
      (c) => columnVisibleNow(c, activeFilters ?? {}) && (c.format ?? '').toLowerCase() !== 'password',
    ),
    [result.columns, activeFilters],
  )

  // ── batch-edit state ──
  const [editMode, setEditMode] = useState(false)
  // keep the row-menu-side editMode ref in sync (declared earlier so openRowMenu can read it
  // without a TDZ on `editMode`).
  useEffect(() => { editModeRef.current = editMode }, [editMode])
  // Close the row context menu on Escape / click-outside. The overlay itself stops propagation
  // (see the menu render below), so clicking *inside* the menu doesn't close it.
  useEffect(() => {
    if (!menuState) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeMenu() }
    const onClick = () => closeMenu()
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [menuState, closeMenu])
  const [dirtyRows, setDirtyRows] = useState<Set<DataRow>>(new Set())   // existing rows that were edited (markers + Save count)
  const [newRows, setNewRows] = useState<DataRow[]>([])                  // rows to insert — shown at the top, newest first
  const [deleted, setDeleted] = useState<Set<DataRow>>(new Set())        // existing rows marked for deletion
  const [selected, setSelected] = useState<Set<DataRow>>(new Set())      // rows ticked for copy
  const [clipboard, setClipboard] = useState<Record<string, unknown>[]>([])  // copied row snapshots (current values)
  const [saving, setSaving] = useState(false)
  const [saveErrors, setSaveErrors] = useState<string[]>([])
  const editsRef = useRef<Map<DataRow, Record<string, unknown>>>(new Map())  // row → edited fields (uncontrolled inputs write here)
  // New rows born from a Copy/duplicate or Paste carry the ORIGINAL row's values here. On Save such
  // a row fires ``on_duplicate`` (with the source exposed as ``SOURCE_<col>``) instead of
  // ``on_insert`` — a copy/paste in the grid IS a duplicate, same as the dialog's Duplicate button.
  // A blank "+ Add row" / imported row has no entry → plain ``on_insert``.
  const newRowSourceRef = useRef<Map<DataRow, Record<string, unknown>>>(new Map())
  // ``editTick`` increments on every edit to force a parent re-render — needed for *new* rows
  // where ``setDirtyRows`` doesn't fire (new rows aren't in the dirty set, they're tracked
  // separately as inserts). The cell functions read live values via ``cur(row, …)``, so a
  // re-render alone is enough — we deliberately do NOT include this in ``dataCols`` deps to
  // keep the cell ``columnDef.cell`` references stable: React reconciles same-type / same-
  // position elements in-place, so the uncontrolled ``<input defaultValue>`` keeps its DOM
  // value (and the user's cursor) across edits. Including the tick in the deps would rebuild
  // the column defs on every keystroke → TanStack rebuilds rows → text inputs lose focus.
  const [editTick, setEditTick] = useState(0)
  // Silence unused-var lint — the value is *read* implicitly via React's re-render scheduling.
  void editTick

  const resetEdit = useCallback(() => {
    setEditMode(false); setDirtyRows(new Set()); setNewRows([]); setDeleted(new Set()); setSelected(new Set()); setSaveErrors([])
    editsRef.current = new Map()
    newRowSourceRef.current = new Map()
    setEditTick(0)
  }, [])
  // a refetch (or a query change) ends any in-progress batch edit (the clipboard survives — it's just data)
  useEffect(() => { resetEdit() }, [result, resetEdit])

  const dirtyCount = dirtyRows.size + newRows.length + [...deleted].filter((r) => !newRows.includes(r)).length
  const fileRef = useRef<HTMLInputElement>(null)

  // current values of a row (its original fields overlaid with any pending edits)
  const valuesOf = useCallback((row: DataRow): Record<string, unknown> => ({ ...row, ...editsRef.current.get(row) }), [])
  // `editChange` must be referentially stable so the data-column `cell` functions don't change on
  // every keystroke (a new cell fn → flexRender remounts the cell → the <input> loses focus). It
  // reads `newRows` via a ref so it isn't a dependency.
  const newRowsRef = useRef<DataRow[]>([])
  useEffect(() => { newRowsRef.current = newRows }, [newRows])
  const editChange = useCallback((row: DataRow, name: string, v: unknown) => {
    let ed = editsRef.current.get(row)
    if (!ed) { ed = {}; editsRef.current.set(row, ed) }
    ed[name] = v
    // Tick on every edit so the dataCols memo recomputes → TanStack re-renders cells →
    // dropdowns reflect the new value and cascading LOOKUP options re-narrow. Text/number
    // inputs use ``defaultValue`` (uncontrolled) so they keep their typed text + focus across
    // the re-render. Separately, mark *existing* rows as dirty for the Save flow (newRows
    // already track inserts; we don't conflate them here).
    setEditTick((t) => t + 1)
    if (!newRowsRef.current.includes(row)) setDirtyRows((s) => new Set(s).add(row))
  }, [])
  // add new rows at the TOP of the grid (newest first); `seeds` carries each row's initial values.
  // `sources` (parallel to `seeds`, optional) marks a row as a DUPLICATE of an existing one — its
  // source values, recorded so Save can fire ``on_duplicate`` with ``SOURCE_<col>`` instead of
  // ``on_insert``. Omit it (Add row / import) for a plain insert.
  const prependNewRows = useCallback((seeds: Record<string, unknown>[], sources?: Record<string, unknown>[]) => {
    if (seeds.length === 0) return
    const fresh = seeds.map((s, i) => {
      const row: DataRow = {}
      editsRef.current.set(row, { ...s })
      const src = sources?.[i]
      if (src) newRowSourceRef.current.set(row, { ...src })
      return row
    })
    setNewRows((p) => [...fresh, ...p])
    if (!editMode) setEditMode(true)
  }, [editMode])
  const addRow = useCallback(() => prependNewRows([{ ...(addSeed ?? {}) }]), [prependNewRows, addSeed])
  // Copy/duplicate a row → a new row seeded from it, with the original recorded as its duplicate source.
  const duplicateRow = useCallback((row: DataRow) => { const v = valuesOf(row); prependNewRows([v], [v]) }, [prependNewRows, valuesOf])
  const toggleDelete = useCallback((row: DataRow, isNew: boolean) => {
    if (isNew) {
      setNewRows((p) => p.filter((r) => r !== row)); editsRef.current.delete(row)
      setSelected((s) => { if (!s.has(row)) return s; const n = new Set(s); n.delete(row); return n })
      return
    }
    setDeleted((s) => { const n = new Set(s); n.has(row) ? n.delete(row) : n.add(row); return n })
  }, [])
  const toggleSelected = useCallback((row: DataRow) => {
    setSelected((s) => { const n = new Set(s); n.has(row) ? n.delete(row) : n.add(row); return n })
  }, [])
  const copySelected = useCallback(() => setClipboard([...selected].map((r) => valuesOf(r))), [selected, valuesOf])
  // Paste copied rows as new rows — each is a duplicate of the row it was copied from (the clipboard
  // snapshot is the source), so Save fires ``on_duplicate`` for them just like the Copy button.
  const pasteRows = useCallback(() => prependNewRows(clipboard.map((r) => ({ ...r })), clipboard.map((r) => ({ ...r }))), [clipboard, prependNewRows])

  const lookupSpecs = useMemo<LookupSpec[]>(
    () => result.columns.filter((c) => c.rule?.kind === 'lookup').map((c) => {
      const r = c.rule as Extract<NonNullable<Column['rule']>, { kind: 'lookup' }>
      // Forward the rule's static params (v1 ly_dictionary_filters → DictionaryEntry.lookup_params)
      // so a UDC-style lookup gets its SY/RT and returns the *right* rows. Different param sets
      // cache separately in services/lookups (specKey folds the params in).
      return { connector: r.connector, query: r.query, value: r.value, label: r.label, params: r.params, sources: r.sources }
    }),
    [result.columns],
  )
  // ``useLookupTables`` returns the full :class:`LookupData` (value→label map *plus* raw rows).
  // Same module cache as the older ``useLookupBatch`` — no extra fetches. The raw rows feed the
  // lookup-pick return-fill (``return_binds``) in the grid + on import, and the dropdown extras.
  const lookupMaps = useLookupTables(lookupSpecs)

  // Import rows from an Excel/CSV file. Matching is by *header text*, not column position, so the
  // sheet's columns can be in any order and extra columns are ignored: we build `byHeader` =
  // {normalized header text → result column name}, registering for each result column its `name`,
  // its display `label`, and the "(ID)" suffixed forms (a lookup column shows as "<label> (ID)" in
  // the grid, so that's a natural header to round-trip). `sheet_to_json` already gives each row as
  // {headerText → value} (the first sheet row is the header), so we just look each header up.
  //
  // Then, if the query has `keyColumns`, each imported row is matched against the *loaded* rows on
  // those columns: a match becomes an **edit** of that row (→ `update_query` on Save), the rest are
  // **new** rows (→ `insert_query`). That's the v2 replacement for v1's MERGE/UPSERT `_post` queries:
  // update-or-insert is decided here, in the batch-edit model, instead of in one SQL statement.
  const importFile = useCallback(async (file: File) => {
    setSaveErrors([])
    try {
      const wb = XLSX.read(await file.arrayBuffer(), { type: 'array' })
      const ws = wb.Sheets[wb.SheetNames[0]]
      if (!ws) return
      const rows = XLSX.utils.sheet_to_json<Record<string, unknown>>(ws, { defval: null })
      const idTag = t('table.idColumnSuffix')
      const byHeader = new Map<string, string>() // normalized header text → result column name
      for (const c of result.columns) {
        const add = (s: string | null | undefined) => { if (s) byHeader.set(s.trim().toLowerCase(), c.name) }
        add(c.name)
        // A coded column (LOOKUP or ENUM) resolves by its CODE only — never by its resolved
        // description. The description isn't unique (two codes can share one), and both kinds now
        // carry their code in the "(ID)" column the export emits. So DON'T map the bare label header
        // (the export emits both "X (ID)" and "X"; the latter holds the description and was
        // overwriting the code on import). Only the code-bearing headers ("(ID)" + raw name) map.
        if (c.rule?.kind !== 'lookup' && c.rule?.kind !== 'enum') add(c.label)
        add(`${c.label ?? c.name} ${idTag}`); add(`${c.name} ${idTag}`)
      }
      const seeds = rows.map((r) => {
        const out: Record<string, unknown> = {}
        for (const [header, v] of Object.entries(r)) {
          const col = byHeader.get(header.trim().toLowerCase())  // header text → which result column
          if (col) out[col] = v
        }
        return out
      }).filter((s) => Object.keys(s).length > 0)  // skip rows whose headers matched nothing
      if (seeds.length === 0) { setSaveErrors([t('table.importNoMatch', { file: file.name })]); return }

      // Resolve lookup ``return_binds`` on import: a coded column (e.g. OBNM) with return_binds
      // fills its mapped target columns (e.g. SY) from the matching lookup row — same effect as a
      // dialog / grid pick, but driven by the imported code. Fill-if-empty: an explicit value in the
      // sheet wins; we only fill a target the sheet left blank. Skips silently if the lookup data
      // isn't loaded yet (the grid loads it when the column renders).
      const returnFillCols = result.columns.filter((c) => c.rule?.kind === 'lookup' && (c.return_binds?.length ?? 0) > 0)
      if (returnFillCols.length > 0) {
        for (const seed of seeds) {
          for (const c of returnFillCols) {
            const rule = c.rule
            if (rule?.kind !== 'lookup') continue
            const code = seed[c.name]
            if (code == null || String(code).trim() === '') continue
            const data = lookupMaps.get(lookupKey({ connector: rule.connector, query: rule.query, value: rule.value, label: rule.label, params: rule.params, sources: rule.sources }))
            if (!data?.rows) continue
            const valKey = rule.value
            const want = String(code).trim()
            const row = data.rows.find((r) => {
              const rv = r[valKey] ?? r[valKey.toLowerCase()] ?? r[valKey.toUpperCase()]
              return rv != null && String(rv).trim() === want
            })
            if (!row) continue
            const lcRow = new Map(Object.entries(row).map(([k, v]) => [k.toLowerCase(), v]))
            for (const b of c.return_binds ?? []) {
              const target = result.columns.find((rc) => rc.name.toLowerCase() === b.column.toLowerCase())?.name ?? b.column
              const cur = seed[target]
              if (cur != null && String(cur).trim() !== '') continue  // explicit imported value wins
              const v = lcRow.get(b.param.toLowerCase())
              if (v !== undefined) seed[target] = v
            }
          }
        }
      }
      if (!editMode) setEditMode(true)

      // Effective key columns come from the per-column ``key`` flags resolved onto the RESULT
      // columns (the Columns-tab "key"), not ``screen.key_columns`` — that's the legacy explicit
      // override and is usually EMPTY even when the row has a key (F00950 keys via per-column flags).
      // An empty list meant every imported row looked new → re-import failed on duplicate-PK insert.
      // Using the result columns also guarantees the names match the seed keys' case.
      const keyColsFromResult = result.columns.filter((c) => c.key).map((c) => c.name)
      const keys = (keyColsFromResult.length ? keyColsFromResult : (keyColumns ?? [])).filter(Boolean)
      const newSeeds: Record<string, unknown>[] = []
      const matched: DataRow[] = []
      // Match on the key columns ACTUALLY PRESENT in the sheet. A hidden / derived key column (e.g.
      // JDE FSSY, auto-populated from the object) isn't exported, so requiring ALL keys would make
      // every imported row look new — and re-importing an existing row would then try to INSERT it
      // and fail on a duplicate PK. Compare TRIMMED (JDE pads CHAR columns; the read trims them).
      const norm = (v: unknown) => String(v ?? '').trim()
      const matchKeys = keys.filter((k) => seeds.some((s) => k in s))
      if (matchKeys.length > 0) {
        const keyOf = (o: Record<string, unknown>) => matchKeys.map((k) => norm(o[k])).join(' ')
        const byKey = new Map<string, DataRow>()
        for (const row of result.rows) byKey.set(keyOf(row), row)
        for (const seed of seeds) {
          const match = byKey.get(keyOf(seed))  // every matchKey is present in the seed by construction
          if (match) {  // overlay the imported fields onto the existing row as pending edits
            editsRef.current.set(match, { ...editsRef.current.get(match), ...seed })
            matched.push(match)
          } else {
            newSeeds.push(seed)
          }
        }
      } else {
        newSeeds.push(...seeds)
      }
      if (matched.length) setDirtyRows((s) => { const n = new Set(s); for (const r of matched) n.add(r); return n })
      if (newSeeds.length) prependNewRows(newSeeds)
    } catch (e) {
      setSaveErrors([e instanceof Error ? e.message : String(e)])
    }
  }, [prependNewRows, editMode, result.columns, result.rows, keyColumns, lookupMaps, t])

  const save = useCallback(async () => {
    setSaving(true); setSaveErrors([])
    const jobs: Promise<unknown>[] = []
    const localErrs: string[] = []
    // Same write path as the dialog Save: ``saveScreenRow`` splits each row across the main table
    // and any ``column_groups`` (related 1:1 tables) by each column's ``group``, so an inline grid
    // edit to a JOINed column lands in its related table. New-row inserts capture the
    // server-assigned PK (``resolvedBinds``) so the ``on_insert`` hook + FK binds see it.
    const columnGroups = (screen?.column_groups ?? []) as ColumnGroup[]
    const newBinds = new Map<DataRow, Record<string, unknown>>()
    const writeRow = (mode: 'add' | 'edit', sent: Record<string, unknown>, savedRow: DataRow) =>
      saveScreenRow({
        connector, mode, sent, savedRow, columns: result.columns, columnGroups,
        mainUpdateQuery: updateQuery, mainInsertQuery: insertQuery,
      })
    // The values to write for a row = its edits, plus any conditional forced default (default_when)
    // overlaid — so a system-determined value is persisted even though the cell was read-only and
    // never "edited". UI-only enforcement lives here (and in the dialog), not the backend.
    const sentWithForced = (row: DataRow): Record<string, unknown> => {
      const sent: Record<string, unknown> = { ...(editsRef.current.get(row) ?? {}) }
      const vals = valuesOf(row)
      for (const c of result.columns) {
        const f = forcedDefault(c.default_when, vals)
        if (f !== undefined) sent[c.name] = f
      }
      return sent
    }
    // edited existing rows → main `_put` (+ each group's update/insert), key-aware WHERE via originals
    for (const row of dirtyRows) {
      if (deleted.has(row)) continue
      if (!updateQuery) { localErrs.push(t('table.editNoUpdate')); break }
      jobs.push(writeRow('edit', sentWithForced(row), row))
    }
    // new rows → main `_post` (+ each group's insert): just the entered values (nothing existed before)
    for (const row of newRows) {
      if (deleted.has(row)) continue
      if (!insertQuery) { localErrs.push(t('table.editNoInsert')); break }
      jobs.push(writeRow('add', sentWithForced(row), {}).then((r) => { newBinds.set(row, r.resolvedBinds) }))
    }
    // rows marked for deletion → main `_delete` (+ each group's delete first, FK-safe): the
    // current row identifies it (it wasn't edited if it's deleted)
    for (const row of deleted) {
      if (newRows.includes(row)) continue
      if (!deleteQuery) { localErrs.push(t('table.editNoDelete')); break }
      jobs.push(deleteScreenRow({ connector, row, columnGroups, mainDeleteQuery: deleteQuery }))
    }
    const settled = await Promise.allSettled(jobs)
    const reqErrs = settled
      .filter((r): r is PromiseRejectedResult => r.status === 'rejected')
      .map((r) => (r.reason instanceof ApiError ? r.reason.message : String(r.reason)))
    const errs = [...new Set([...localErrs, ...reqErrs])]
    if (errs.length) {
      setSaving(false); setSaveErrors(errs); return
    }

    // Row-level lifecycle hooks (v2 extension of v1's ly_evt_cpt FormsTable events). Each
    // affected row fires the matching chain once with that row's values as the firing context
    // — :func:`runChain` handles ChainAction / IfAction / LoopAction recursion + dotted-path
    // ``ParamBind.source`` resolution. ``notify`` messages collect on the result's warnings.
    // Failures append to ``saveErrors`` so the operator sees what went wrong; the row mutation
    // itself already succeeded (the chains run *after*).
    // Tag hook action calls for change capture on a change-tracked screen — same as the dialog's
    // Save (a grid edit/insert/duplicate must land in the package identically to a form one). The
    // main row write is already captured via screen-match in _run_sql; this carries the HOOK
    // actions (run_query auto, call_api/plugin on their change_replay opt-in) into the same package.
    const fireChain = async (actions: Action[] | undefined, ctx: DataRow): Promise<string | null> => {
      if (!actions?.length) return null
      // Per-ROW changeContext: stamp THIS row's natural key (entity_key) so each row's action
      // writes group under that record — fireChain runs once per affected row, so it can't share
      // a single context the way the dialog (one row) does.
      const changeContext = screen?.change_tracked
        ? { app: screen.app, screen: screen.id, entity_key: entityKeyOf(screen.columns, ctx) }
        : undefined
      const result = await runChain(actions, {}, ctx, {
        defaultConnector: connector,
        requestPrompt,   // batch-save hooks rarely prompt, but a migrated chain might carry one
        sharedActions: sharedActions ?? undefined,
        changeContext,
      })
      if (result.warnings.length > 0) {
        // eslint-disable-next-line no-console
        console.info('row-hook warnings:', result.warnings)
      }
      return result.ok ? null : (result.error ?? null)
    }
    const hookErrs: string[] = []
    for (const row of dirtyRows) {
      if (deleted.has(row)) continue
      const ctx = { ...row, ...editsRef.current.get(row) }
      const err = await fireChain(screen?.on_update, ctx as DataRow)
      if (err) hookErrs.push(err)
    }
    for (const row of newRows) {
      if (deleted.has(row)) continue
      const base = { ...editsRef.current.get(row), ...newBinds.get(row) }
      // A copy/paste/duplicate row fires on_duplicate (instead of on_insert), with the original
      // exposed as SOURCE_<col> — identical to the dialog's Duplicate path so a chain that copies
      // related-table rows works the same whether duplicated in the form or in the grid.
      const src = newRowSourceRef.current.get(row)
      if (src) {
        const ctx: DataRow = { ...base }
        for (const [k, v] of Object.entries(src)) ctx[`SOURCE_${k}`] = v
        const err = await fireChain(screen?.on_duplicate, ctx)
        if (err) hookErrs.push(err)
      } else {
        const err = await fireChain(screen?.on_insert, base)
        if (err) hookErrs.push(err)
      }
    }
    for (const row of deleted) {
      if (newRows.includes(row)) continue
      const err = await fireChain(screen?.on_delete, row)
      if (err) hookErrs.push(err)
    }
    setSaving(false)
    if (hookErrs.length) setSaveErrors([...new Set(hookErrs)])
    else { resetEdit(); onSaved?.() }
  }, [connector, dirtyRows, newRows, deleted, updateQuery, insertQuery, deleteQuery, onSaved, resetEdit, t, screen, sharedActions])

  // ── rule helpers (memoized per result) ──
  const enumMaps = useMemo(() => {
    const m = new Map<string, Map<string, string>>()
    for (const c of result.columns) if (c.rule?.kind === 'enum') m.set(c.name, enumMap(c.rule))
    return m
  }, [result.columns])

  // new rows show at the TOP of the grid (newest first — `newRows` already keeps that order)
  const data = useMemo<DataRow[]>(() => (editMode ? [...newRows, ...result.rows] : result.rows), [result.rows, editMode, newRows])

  // ── column definitions ──
  // `dataCols` is the part that must stay referentially stable across edit-state churn (typing,
  // selecting, deleting) — its `cell` functions only close over `editMode`, `editChange` (stable)
  // and the per-result maps, so the data cells (incl. the edit <input>s) keep their identity →
  // focus is preserved while typing. Per-row state (dirty/new/deleted/selected) lives entirely
  // in the `editCols` (the leftmost select + status columns), which may rebuild freely.
  const span = useMemo(
    () => (text: string, kind: ReturnType<typeof ruleCell>['kind'], align: Align, titleVal?: string) => (
      <CellSpan className={kind === 'plain' ? undefined : kind} style={align ? { textAlign: align } : undefined} title={titleVal}>{text}</CellSpan>
    ),
    [],
  )
  const grouped = useCallback(
    (info: { cell: { getIsGrouped: () => boolean }; getValue: () => unknown }, align: Align) =>
      info.cell.getIsGrouped() ? span(String(info.getValue() ?? ''), 'plain', align) : null,
    [span],
  )
  const isGroupRow = useCallback((info: { row: { getIsGrouped?: () => boolean } }) => !!info.row.getIsGrouped?.(), [])
  // Reads the cell's current value (original row overlaid with any pending edits).
  // Case-insensitive lookup of *name* on both maps — v1's column hints / filter_from /
  // ParamBinds carry uppercase column names, while a Postgres result lowercases its
  // identifiers (Oracle preserves uppercase). Without the fold, a cascading
  // ``filter_from = {source: "SEC_TYPE", column: "FS"}`` on a Postgres screen would
  // read ``ed["SEC_TYPE"]`` while the edit was stored at ``ed["sec_type"]`` → narrowing
  // stays disabled. Same lookup walks ed then row so a pending edit beats the original.
  const cur = useCallback((row: DataRow, name: string) => {
    const ed = editsRef.current.get(row)
    if (ed) {
      if (name in ed) return ed[name]
      const lk = name.toLowerCase()
      for (const k of Object.keys(ed)) if (k.toLowerCase() === lk) return ed[k]
    }
    if (name in row) return row[name]
    const lk = name.toLowerCase()
    for (const k of Object.keys(row)) if (k.toLowerCase() === lk) return row[k]
    return undefined
  }, [])

  const dataCols = useMemo<ColumnDef<DataRow, unknown>[]>(() => {
    const idSuffix = ` ${t('table.idColumnSuffix')}`
    // column-name → actual column.name map (case-insensitive). Resolves a lookup ``return_binds``
    // target (normalised uppercase) to the grid's actual column name for the return-fill write.
    const nameToColName = new Map<string, string>()
    for (const c of shownColumns) {
      const k = c.name.toLowerCase()
      if (!nameToColName.has(k)) nameToColName.set(k, c.name)
    }
    const editCellFor = (c: Column, info: { row: { original: unknown } }) => {
      const row = info.row.original as DataRow
      // Conditional forced default (default_when): when a sibling column's value triggers a rule,
      // the cell is system-determined → render its forced value READ-ONLY (no editor). Reactive:
      // editing the discriminator re-ticks dataCols, so this re-evaluates. Save writes it too.
      const forced = forcedDefault(c.default_when, valuesOf(row))
      if (forced !== undefined) return span(String(forced), 'plain', cellAlign(c))
      const v = cur(row, c.name)
      // For LOOKUP columns, pull the already-fetched ``LookupData`` (value→label *and* raw
      // rows) from ``lookupMaps``. The map drives the dropdown options; the rows let the
      // pick handler resolve return_params.
      const lookupData: LookupData | undefined = c.rule?.kind === 'lookup'
        ? lookupMaps.get(lookupKey({
            connector: c.rule.connector, query: c.rule.query,
            value: c.rule.value, label: c.rule.label, params: c.rule.params, sources: c.rule.sources,
          }))
        : undefined
      // Lookup-pick return-fill dispatcher — the picked column's ``return_binds`` already produced
      // a ``{targetColumn: value}`` map; write each to the sibling cell on the same row (resolving
      // the target to the grid's actual column name). v1's "pick OBNM, SY auto-populates", explicit.
      const handleLookupReturnValues = (returnValues: Record<string, unknown>) => {
        for (const [col, val] of Object.entries(returnValues)) {
          editChange(row, nameToColName.get(col.toLowerCase()) ?? col, val)
        }
      }
      // Cascading narrowing for LOOKUP cells (v1's ly_tbl_filters in bulk-edit). For each
      // ``filter_from`` dep (sibling column → lookup-row column), read the same row's
      // sibling cell via ``cur()`` (which already folds editsRef on top of the raw row)
      // and emit a {column, value} narrow entry when set. EditCell filters the lookup
      // rows on the fly so the dropdown shows only matching options (DRSY=01 → DRRT only
      // shows the RT codes where SY=01). Empty values mean "no narrowing for that dep".
      // Prefer the screen's explicit ``filter_from``; else fall back to the lookup's own declared
      // ``key_columns`` (same-named row column → lookup column) so the edit dropdown auto-narrows
      // to the right rows for a non-unique value, matching the grid's display disambiguation.
      const narrowBy: { column: string; value: unknown }[] | undefined =
        c.rule?.kind !== 'lookup'
          ? undefined
          : c.filter_from && c.filter_from.length > 0
            ? c.filter_from.map((dep) => ({ column: dep.column, value: cur(row, dep.source) }))
            : (c.rule.key_columns && c.rule.key_columns.length > 0
                ? c.rule.key_columns.map((kc) => ({ column: kc, value: cur(row, kc) }))
                : undefined)
      return (
        <EditCell
          ctrl={editCtrlOf(c)}
          column={c}
          defaultText={v === null || v === undefined ? '' : String(v)}
          onChange={(nv) => editChange(row, c.name, nv)}
          lookupOptions={lookupData?.map}
          lookupRows={lookupData?.rows}
          onLookupReturnValues={handleLookupReturnValues}
          narrowBy={narrowBy}
          displayFields={c.rule?.kind === 'lookup' ? c.rule.display_fields : undefined}
          cellColumns={c.rule?.kind === 'lookup' ? lookupCellColumns(c.rule) : undefined}
        />
      )
    }
    // Per-row-mode edit lock (ColumnHint.disable_on_add / disable_on_edit): a new row (in newRows)
    // checks disable_on_add, an existing row checks disable_on_edit. Locked → the cell falls through
    // to its read-only display (same as non-edit mode), so the grid bulk-edit honours the SAME
    // column setting the dialog's fieldStateOf does. Reads the live newRows via the ref — the cell
    // renderers re-run each render, so this stays current without adding newRows to the memo deps.
    const cellLocked = (c: Column, rowOriginal: DataRow) =>
      newRowsRef.current.includes(rowOriginal) ? !!c.disable_on_add : !!c.disable_on_edit
    const out: ColumnDef<DataRow, unknown>[] = []
    for (const c of shownColumns) {
      const align = cellAlign(c)

      if (c.rule?.kind === 'lookup') {
        const r = c.rule
        const data = lookupMaps.get(lookupKey({ connector: r.connector, query: r.query, value: r.value, label: r.label, params: r.params, sources: r.sources }))
        const map = data?.map
        // Per-row label disambiguation. A lookup ``value`` need not be globally unique — e.g.
        // USR_ID repeats across apps and is only unique per USR_APPS_ID — so the value→label
        // ``map`` collides (it keeps whichever row landed last). Resolve the label using the
        // row's *key columns*: explicit ``filter_from`` if the screen set it, else the lookup's
        // own declared ``key_columns`` (automatic, no per-column config). Build a composite
        // ``<key…>\0<value>`` → label map ONCE per lookup column (O(rows)), then resolve each
        // cell in O(1). Falls back to the plain ``map`` when no key columns / no composite hit.
        const keyDeps: { source: string; column: string }[] =
          c.filter_from && c.filter_from.length > 0
            ? c.filter_from.map((d) => ({ source: d.source, column: d.column }))
            : (r.key_columns ?? []).map((kc) => ({ source: kc, column: kc }))
        const norm = (x: unknown) => String(x ?? '').trim()
        const compositeMap = (() => {
          if (!keyDeps.length || !data?.rows) return undefined
          const m = new Map<string, string>()
          for (const lr of data.rows) {
            const key = [
              ...keyDeps.map((d) => norm(lr[data.byColLower.get(d.column.toLowerCase()) ?? d.column])),
              norm(lr[data.vKey]),
            ].join(' ')
            m.set(key, lr[data.lKey] == null ? '' : String(lr[data.lKey]))
          }
          return m
        })()
        // Trim-tolerant value->label map for display resolution only. ``data.map`` is keyed on the
        // RAW value because EditCell uses it for the edit dropdown's option values + return-param
        // row matching, which must stay exact (and write-back must keep the value's padding). So we
        // build a SEPARATE trimmed-key map here, used only to resolve the displayed label — never
        // written back. Comparison-only, like the server-side filter-bind trim.
        const trimmedMap = (() => {
          if (!data?.rows) return undefined
          const m = new Map<string, string>()
          for (const lr of data.rows) {
            const v = lr[data.vKey]
            if (v == null) continue
            m.set(norm(v), lr[data.lKey] == null ? String(v) : String(lr[data.lKey]))
          }
          return m
        })()
        const resolveLabel = (row: DataRow, value: unknown): string | undefined => {
          const raw = value == null ? '' : String(value)
          if (compositeMap) {
            const key = [...keyDeps.map((d) => norm(cur(row, d.source))), norm(value)].join(' ')
            const hit = compositeMap.get(key)
            if (hit != null && hit !== '') return hit
          }
          // Exact match first (fast, padding-preserving), then trim-tolerant (JDE pads / right-
          // justifies codes, so the cell's raw value and the lookup's value column can differ in
          // padding for the same logical code). Resolution-only — never written back.
          return map?.get(raw) ?? trimmedMap?.get(norm(value))
        }
        // Lookup-filter options for both the (ID) and the resolved-label columns: the ID
        // column filters by code, the label column filters by label. Each picker shows
        // "<code> — <label>" so the operator sees both. Empty until the lookup table loads.
        // Use ``data.vKey`` / ``data.lKey`` (the *actual-case* row keys — Postgres folds
        // identifiers to lowercase, so the dictionary's uppercase ``r.value`` / ``r.label``
        // don't match row keys directly).
        // The lookup's ``display_fields`` → extra table columns in the filter dropdown too (it uses
        // the same SearchSelect as the dialog/grid). Resolve the keys once; emit one cell per field.
        const dispKeys = data ? (r.display_fields ?? []).map((f) => data.byColLower.get(f.toLowerCase()) ?? f) : []
        const cellsOf = (row: Record<string, unknown>) =>
          dispKeys.length ? dispKeys.map((k) => { const x = row[k]; return x == null ? '' : String(x).trim() }) : undefined
        // Same option shape as the dialog/grid picker — mono CODE column, the description as label,
        // then the display_fields cells — so the filter dropdown renders the identical table (was a
        // single concatenated "code — label" line before).
        const lookupOptsByCode = data && data.rows
          ? data.rows.map((row) => {
              const code = row[data.vKey] == null ? '' : String(row[data.vKey])
              const label = row[data.lKey] == null ? '' : String(row[data.lKey])
              return { value: code, label: label || code, mono: code, cells: cellsOf(row) }
            })
          : undefined
        const lookupOptsByLabel = data && data.rows
          ? data.rows.map((row) => {
              const code = row[data.vKey] == null ? '' : String(row[data.vKey])
              const label = row[data.lKey] == null ? '' : String(row[data.lKey])
              return { value: label || code, label: label || code, mono: code, cells: cellsOf(row) }
            })
          : undefined
        out.push({
          id: c.name,
          // hide_label → this is the ONLY column for the field, so drop the "(ID)" suffix.
          header: c.hide_label ? colHeader(c) : colHeader(c) + idSuffix,
          accessorFn: (row) => row[c.name],
          size: c.width ?? undefined,
          ...filterPropsFor('lookup', lookupOptsByCode, align, lookupCellColumns(r)),
          cell: (info) => {
            const g = grouped(info, align); if (g) return g
            if (editMode && !isGroupRow(info) && !cellLocked(c, info.row.original as DataRow)) return editCellFor(c, info)
            const v = cur(info.row.original as DataRow, c.name)
            const { text, isNull } = ruleCell(v, { ...c, rule: undefined }, undefined, undefined)
            return span(isNull ? 'null' : text, isNull ? 'null' : 'plain', align)
          },
        })
        if (!c.hide_label) out.push({
          id: `${c.name}__lookup`,
          header: colHeader(c),
          accessorFn: (row) => { const v = row[c.name]; return v === null || v === undefined ? '' : (resolveLabel(row as DataRow, v) ?? String(v)) },
          ...filterPropsFor('lookup', lookupOptsByLabel, undefined, lookupCellColumns(r)),
          cell: (info) => {
            const g = grouped(info, align); if (g) return g
            // derived from the "(ID)" column — read-only; reflects the *current* (possibly edited) code
            const raw = cur(info.row.original as DataRow, c.name)
            const resolved = resolveLabel(info.row.original as DataRow, raw)
            const { text, kind, isNull } = ruleCell(raw, c, undefined, map)
            // ``resolved`` (composite/key-column hit) wins over the value-only ``map`` so the right
            // label shows when the same code maps to different labels across key-column values.
            return span(resolved ?? text, isNull ? 'null' : (resolved ? 'lookup' : kind), align, isNull ? undefined : String(raw ?? ''))
          },
        })
        continue
      }

      if (c.rule?.kind === 'enum') {
        // Same shape as a LOOKUP: a "(ID)" column carrying the raw CODE (editable / exported /
        // imported) plus a derived label column (the description — read-only). The grid otherwise
        // never shows the code, so an export couldn't round-trip; this makes ENUM behave exactly
        // like LOOKUP — resolved by code, never by the (non-unique) description.
        const emap = enumMaps.get(c.name)   // code → label
        // mono CODE column + the label, matching the dialog/grid enum picker (was concatenated).
        const optsByCode = c.rule.values.map((v) => ({ value: v.value, label: v.label || v.value, mono: v.value }))
        const optsByLabel = c.rule.values.map((v) => ({ value: v.label || v.value, label: v.label || v.value, mono: v.value }))
        out.push({
          id: c.name,
          header: c.hide_label ? colHeader(c) : colHeader(c) + idSuffix,
          accessorFn: (row) => row[c.name],
          size: c.width ?? undefined,
          ...filterPropsFor('enum', optsByCode, align),
          cell: (info) => {
            const g = grouped(info, align); if (g) return g
            if (editMode && !isGroupRow(info) && !cellLocked(c, info.row.original as DataRow)) return editCellFor(c, info)
            const v = cur(info.row.original as DataRow, c.name)
            const { text, isNull } = ruleCell(v, { ...c, rule: undefined }, undefined, undefined)
            return span(isNull ? 'null' : text, isNull ? 'null' : 'plain', align)
          },
        })
        if (!c.hide_label) out.push({
          id: `${c.name}__enum`,
          header: colHeader(c),
          accessorFn: (row) => { const v = row[c.name]; return v === null || v === undefined ? '' : (emap?.get(String(v)) ?? String(v)) },
          ...filterPropsFor('enum', optsByLabel),
          cell: (info) => {
            const g = grouped(info, align); if (g) return g
            // Derived from the "(ID)" column — read-only; reflects the current (possibly edited) code.
            const v = cur(info.row.original as DataRow, c.name)
            const { text, kind, isNull } = ruleCell(v, c, emap, undefined)
            return span(text, isNull ? 'null' : kind, align, isNull ? undefined : String(v ?? ''))
          },
        })
        continue
      }

      const kind = filterKindOf(c)
      out.push({
        id: c.name,
        header: colHeader(c),
        accessorFn: (row) => {
          const v = row[c.name]
          if (c.rule?.kind === 'boolean') return v === null || v === undefined ? null : v === c.rule.true_value ? 'true' : 'false'
          return v
        },
        size: c.width ?? undefined,
        ...filterPropsFor(kind, undefined, align),
        cell: (info) => {
          const g = grouped(info, align); if (g) return g
          if (editMode && !isGroupRow(info)) return editCellFor(c, info)
          const v = cur(info.row.original as DataRow, c.name)
          // Non-enum / non-lookup here (those are split out above), so no enum map is needed.
          const { text, kind: rk, isNull } = ruleCell(v, c, undefined, undefined)
          // Hover title: BOOLEAN cells (rendered as a colored bullet) get the localized "yes"/"no"
          // so the value stays accessible to keyboard / screen-reader / colorblind users.
          const titleVal = isNull ? undefined
            : rk === 'boolean-true' ? t('common.true')
            : rk === 'boolean-false' ? t('common.false')
            : undefined
          return span(text, isNull ? 'null' : rk, align, titleVal)
        },
      })
    }
    return out
    // ``editTick`` is deliberately NOT in the dep list — including it would rebuild the column
    // defs on every keystroke, which makes TanStack re-instantiate row cells and the uncontrolled
    // text inputs lose focus mid-typing. The cell functions read live row + edit values via
    // ``cur(row, …)`` (a stable callback over a ref), so a parent re-render (triggered by
    // ``setEditTick`` on every edit) is enough — flexRender is called fresh and the new
    // ``defaultText`` flows to the SearchSelect / Checkbox / EditInput. Uncontrolled inputs
    // ignore ``defaultValue`` changes after mount, so the DOM keeps the user's typed text.
  }, [shownColumns, enumMaps, lookupMaps, t, editMode, editChange, cur, grouped, isGroupRow, span])

  // the leftmost select + status columns — rebuild freely on edit-state changes; they hold no
  // <input>, only checkboxes/markers/buttons, so remounting them is harmless.
  const editCols = useMemo<ColumnDef<DataRow, unknown>[]>(() => {
    if (!editMode) return []
    return [
      {
        id: '__select',
        header: ({ table }) => {
          // "select all" = every row matching the current filter (across all pages), not just this page
          const rows = table.getFilteredRowModel().rows.filter((r) => !r.getIsGrouped?.()).map((r) => r.original as DataRow)
          const allOn = rows.length > 0 && rows.every((r) => selected.has(r))
          return (
            <CheckBox
              $on={allOn}
              onClick={() => setSelected(() => (allOn ? new Set() : new Set(rows)))}
              title={allOn ? t('table.selectNone') : t('table.selectAll')}
            >{allOn && <Check size={9} />}</CheckBox>
          )
        },
        size: 34, minSize: 34,
        enableSorting: false, enableHiding: false, enableColumnFilter: false, enableGrouping: false, enableResizing: false,
        meta: { internal: true, align: 'center' },
        cell: (info) => {
          if (isGroupRow(info)) return null
          const row = info.row.original as DataRow
          return <CheckBox $on={selected.has(row)} onClick={() => toggleSelected(row)} title={t('table.selectRow')}>{selected.has(row) && <Check size={9} />}</CheckBox>
        },
      },
      {
        id: '__status',
        header: () => null,
        size: 62, minSize: 62,
        enableSorting: false, enableHiding: false, enableColumnFilter: false, enableGrouping: false, enableResizing: false,
        meta: { internal: true, align: 'center' },
        cell: (info) => {
          if (isGroupRow(info)) return null
          const row = info.row.original as DataRow
          const isNew = newRows.includes(row)
          const isDel = deleted.has(row)
          const isDirty = dirtyRows.has(row)
          return (
            <StatusCell>
              {isNew ? <StatusMark $tone="new" title={t('table.rowNew')}>+</StatusMark>
                : isDel ? <StatusMark $tone="deleted" title={t('table.rowDeleted')}>−</StatusMark>
                : isDirty ? <StatusMark $tone="dirty" title={t('table.rowEdited')}>●</StatusMark>
                : <span style={{ width: 7 }} />}
              {canInsert && !isDel && (
                <RowXBtn onClick={() => duplicateRow(row)} title={t('table.duplicateRow')}><Copy size={11} /></RowXBtn>
              )}
              <RowXBtn onClick={() => toggleDelete(row, isNew)} title={isNew ? t('common.cancel') : isDel ? t('common.undo') : t('table.deleteRow')}>
                <X size={11} />
              </RowXBtn>
            </StatusCell>
          )
        },
      },
    ]
  }, [editMode, dirtyRows, newRows, deleted, selected, toggleDelete, duplicateRow, toggleSelected, canInsert, isGroupRow, t])

  const columns = useMemo<ColumnDef<DataRow, unknown>[]>(() => [...editCols, ...dataCols], [editCols, dataCols])

  const initialVisibility = useMemo<VisibilityState>(() => {
    const v: VisibilityState = {}
    for (const c of result.columns) if (c.hidden) { v[c.name] = false; if (c.rule?.kind === 'lookup') v[`${c.name}__lookup`] = false }
    return v
  }, [result.columns])

  return (
    <>
      {saveErrors.length > 0 && <Banner $tone="error">{saveErrors.join(' · ')}</Banner>}
      {editMode && saveErrors.length === 0 && <Banner $tone="info">{t('table.editingHint')}</Banner>}
      {canImport && (
        <input
          ref={fileRef}
          type="file"
          accept=".xlsx,.xls,.csv"
          style={{ display: 'none' }}
          onChange={(e) => {
            const f = e.target.files?.[0]
            if (f) void importFile(f)
            e.target.value = '' // allow re-importing the same file
          }}
        />
      )}
      <DataTable<DataRow>
        columns={columns}
        data={data}
        // Persist column visibility/order PER SCREEN, not per query: several screens can share one
        // read_query (a copy with different hidden columns), so a query-only key made them collide
        // — hiding a column on one bled into the others. Fall back to the query when there's no
        // screen (an ad-hoc query run).
        tableId={screen ? `screen:${screen.app}:${screen.id}` : `sql:${connector}:${query}`}
        exportFilename={query}
        toolbarAfterSearch={runControl}
        toolbarRight={maxRowsControl}
        initialColumnVisibility={initialVisibility}
        initialGrouping={screen?.initial_group_by ?? []}
        rowClassName={(row) => (deleted.has(row) ? 'dt-row-deleted' : newRows.includes(row) ? 'dt-row-new' : dirtyRows.has(row) ? 'dt-row-dirty' : undefined)}
        // Row click opens, in priority order: a SPA route (row_click_route — explicit operator
        // opt-in); the screen's own dialog (the v1 "edit a row by clicking it" affordance); a
        // sibling-screen dialog proxy (row_click_screen — the v1 ctx-menu "Display Properties"
        // promotion). Batch-edit mode disables all of them — the row controls are the actions.
        onRowClick={
          !editMode
            ? rowClickRoute
              ? (row) => openRouteForRow(row)
              : hasDialog
                ? (row) => openDialogForRow(row)
                : hasRowClickProxy
                  ? (row) => { void openProxyForRow(row) }
                  : undefined
            : undefined
        }
        // Right-click → row context menu when the screen carries any `row_menu` actions and
        // we're not in batch-edit mode (in batch mode the row controls are the actions).
        onRowContextMenu={rowMenu.length > 0 && !editMode ? openRowMenu : undefined}
        toolbar={
          !(canBulkEdit || (canInsert && hasDialog) || canImport || screen?.export || screenActions.length > 0) ? undefined : !editMode ? (
            <>
              {hasDialog && canInsert && (
                <TbBtn $tone="primary" onClick={openDialogForAdd} title={t('dialog.addTooltip')}>
                  <Plus size={13} /> {t('table.addRow')}
                </TbBtn>
              )}
              {canBulkEdit && (
                <TbBtn onClick={() => setEditMode(true)} title={t('table.editTip', { q: updateQuery ?? insertQuery ?? '' })}>
                  <Edit3 size={13} /> {hasDialog ? t('table.bulkEdit') : t('table.edit')}
                </TbBtn>
              )}
              {canImport && (
                <TbBtn onClick={() => fileRef.current?.click()} title={t('table.import')}>
                  <Upload size={13} /> {t('table.import')}
                </TbBtn>
              )}
              {/* Workbook export — visible when ``screen.export`` is configured. Fires
                  ``POST /api/screens/{app}/{id}/export``; the backend streams a single
                  .xlsx or a .zip of several. The download uses the Content-Disposition
                  filename, so the browser writes the file the operator expects. */}
              {screen?.export && (
                <TbBtn
                  onClick={() => { void runWorkbookExport() }}
                  disabled={exportBusy}
                  title={t('table.exportTip', { defaultValue: 'Export configured workbook(s) — one xlsx per split value when split_by is set, a single xlsx otherwise.' })}
                >
                  <FileSpreadsheet size={13} /> {exportBusy ? t('table.exporting', { defaultValue: 'Exporting…' }) : t('table.exportWorkbooks', { defaultValue: 'Export workbooks' })}
                </TbBtn>
              )}
              {/* Screen-level action buttons (v1 NOMAJDE "Create Role" / "Reset Password" / etc.).
                  Always rendered when present — even on read-only screens — since the user may
                  want to fire side-effect workflows independent of the table edit flow. */}
              {screenActions.map((a) => (
                <TbBtn
                  key={a.id}
                  onClick={() => { void runScreenAction(a) }}
                  disabled={actionBusy != null}
                  title={a.id}
                >
                  <Zap size={13} /> {a.label || a.id}{actionBusy === a.id ? ' …' : ''}
                </TbBtn>
              ))}
            </>
          ) : (
            <>
              <TbBtn $tone="primary" onClick={save} disabled={saving || dirtyCount === 0} title={t('common.save')}>
                <Check size={13} /> {t('common.save')}{dirtyCount ? ` (${dirtyCount})` : ''}
              </TbBtn>
              <TbBtn onClick={resetEdit} disabled={saving} title={t('common.cancel')}>
                <X size={13} /> {t('common.cancel')}
              </TbBtn>
              {canInsert && (
                <TbBtn onClick={addRow} disabled={saving} title={t('table.addRow')}>
                  <Plus size={13} /> {t('table.addRow')}
                </TbBtn>
              )}
              {canImport && (
                <TbBtn onClick={() => fileRef.current?.click()} disabled={saving} title={t('table.import')}>
                  <Upload size={13} /> {t('table.import')}
                </TbBtn>
              )}
              <TbBtn onClick={copySelected} disabled={saving || selected.size === 0} title={t('table.copyRows')}>
                <Copy size={13} /> {t('table.copyRows')}{selected.size ? ` (${selected.size})` : ''}
              </TbBtn>
              {canInsert && (
                <TbBtn onClick={pasteRows} disabled={saving || clipboard.length === 0} title={t('table.pasteRows')}>
                  <ClipboardPaste size={13} /> {t('table.pasteRows')}{clipboard.length ? ` (${clipboard.length})` : ''}
                </TbBtn>
              )}
            </>
          )
        }
      />
      {/* Screen-action feedback — a thin dismissible banner that surfaces the latest action's
          outcome (success message for run_query / refresh; the explicit text for notify; the
          error message on failure). Sits between the grid and the dialog mount so it's visible
          without crowding the toolbar. */}
      {actionStatus && (
        <Banner $tone={actionStatus.tone}>
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ flex: 1 }}>{actionStatus.message}</span>
            <button
              type="button"
              onClick={() => setActionStatus(null)}
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'inherit', padding: 0, display: 'inline-flex' }}
              aria-label={t('common.cancel')}
            >
              <X size={12} />
            </button>
          </span>
        </Banner>
      )}
      {/* Mount the dialog only when needed — keeps lookups from firing on screens without a form. */}
      {hasDialog && screen && dlgOpen && (
        <ScreenDialog
          open={dlgOpen}
          nested={nestedDialog}
          mode={dlgMode}
          screen={screen}
          columns={result.columns}
          row={dlgRow}
          connector={connector}
          keyColumns={keyColumns}
          onClose={() => setDlgOpen(false)}
          onSaved={() => { setDlgOpen(false); onSaved?.() }}
        />
      )}
      {/* Proxy row-click → sibling-screen dialog (v1's "Display Properties" promotion). Renders
          a ScreenDialog backed by the *target* screen's catalog + the bind-fetched row. While
          the GETs are in flight we keep the dialog mounted (it'll be empty briefly); on error,
          a small overlay banner explains the failure. ``nested`` styles the modal smaller so
          it visually sits "on top" of the parent screen's TableView. */}
      {proxyOpen && !proxyLoading && !proxyError && proxyScreen && (
        <ScreenDialog
          open
          nested
          mode={proxyMode}
          screen={proxyScreen}
          columns={proxyColumns}
          row={proxyRow}
          connector={proxyScreen.connector || connector}
          // Phase 9 record lock: the proxy dialog's lock identity comes from the
          // *target* screen's key_columns (the screen whose dialog we're actually
          // showing), not the parent screen. Without this prop the lock effect
          // builds a null payload and no lock is acquired.
          keyColumns={proxyScreen.key_columns}
          onClose={() => { setProxyOpen(false); setProxyScreen(null) }}
          onSaved={() => { setProxyOpen(false); setProxyScreen(null); onSaved?.() }}
        />
      )}
      {proxyOpen && proxyError && (
        <Banner $tone="error">{proxyError}</Banner>
      )}
      {/* Action-prompt sub-dialog (v2's port of v1's ly_act_params). Mounted at the page level so
          it floats above both the row menu and the toolbar; the runner awaits the resolver, the
          dialog returns the entered values on Confirm and null on Cancel (soft abort). */}
      {pendingPrompt && (
        <ActionPromptDialog
          open
          title={pendingPrompt.title}
          fields={pendingPrompt.fields}
          cols={pendingPrompt.cols}
          submitLabel={pendingPrompt.submitLabel}
          onSubmit={handlePromptSubmit}
          onCancel={handlePromptCancel}
        />
      )}
      {/* Row context menu (slice 6) — a floating panel anchored at the right-click coords. Each
          item runs its action against the picked row; `mousedown` inside the menu is stopped from
          bubbling so the document-level click-outside listener doesn't close it underneath us. */}
      {menuState && (
        <RowMenuBox
          style={{ top: menuState.y, left: menuState.x }}
          onMouseDown={(e) => e.stopPropagation()}
          role="menu"
        >
          {rowMenu.map((a) => (
            <RowMenuItem
              key={a.id}
              role="menuitem"
              disabled={menuBusy != null && menuBusy !== a.id}
              $busy={menuBusy === a.id}
              onClick={() => runRowAction(a, menuState.row)}
              title={a.id}
            >
              <span>{a.label || a.id}</span>
              <span className="id">{a.type}</span>
            </RowMenuItem>
          ))}
          {menuError && <RowMenuErr>{menuError}</RowMenuErr>}
        </RowMenuBox>
      )}
    </>
  )
}

export default ResultTable
