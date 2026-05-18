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
import { useNavigate } from 'react-router-dom'
import type { ColumnDef, VisibilityState } from '@tanstack/react-table'
import styled from '@emotion/styled'
import * as XLSX from 'xlsx'
import { Check, X, Plus, Copy, ClipboardPaste, Upload, Edit3, Zap } from 'lucide-react'
import type { Column, QueryResult } from '../../types/connectors'
import type { Action, PromptField, ScreenDetail } from '../../types/screens'
import { api, ApiError } from '../../api/client'
import { Banner, Checkbox, SearchSelect } from '../../common'
import { DataTable } from '../../common/DataTable'
import { genericFilterFn, type FilterKind, type FilterMeta } from '../../common/DataTableFilter'
import { enumMap, ruleCell } from '../../services/cells'
import { lookupKey, useLookupTables, type LookupData, type LookupSpec } from '../../services/lookups'
import { colors, fontSize, fonts, radius } from '../../theme'
import { CellSpan } from './styled'
import { ScreenDialog, type DialogMode } from './ScreenDialog'
import { ActionPromptDialog } from './ActionPromptDialog'
import { resolveBindList, type Row as CtxRow } from './dialogHelpers'

/** A ParamBind-bearing action with a non-empty ``prompt_fields`` list — same predicate the
 *  ScreenDialog uses. Pulled out so both row-menu and toolbar action runners hit the same
 *  prompt-before-fire flow when the migrator emits ly_act_params. */
function actionPrompt(a: Action): { fields: PromptField[]; title: string | null; cols: number | null; submitLabel: string | null } | null {
  if (a.type !== 'run_query' && a.type !== 'call_api' && a.type !== 'navigate') return null
  const fields = a.prompt_fields ?? []
  if (fields.length === 0) return null
  return {
    fields,
    title: a.prompt_title ?? a.label ?? null,
    cols: a.prompt_cols ?? null,
    submitLabel: a.prompt_submit_label ?? a.label ?? null,
  }
}

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
function isDateish(fmt: string, typ: string) { return fmt === 'date' || /date|timestamp/.test(typ) }
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
const filterPropsFor = (kind: FilterKind, options?: { value: string; label: string }[], align?: FilterMeta['align']) =>
  kind === 'boolean' || kind === 'enum'
    ? { filterFn: 'equals' as const, meta: { filter: { kind, options }, align } as FilterMeta }
    : { filterFn: genericFilterFn, meta: { filter: { kind }, align } as FilterMeta }

// Send both the as-is keys and UPPERCASE copies: the migrated `_put`/`_post`/`_delete` queries
// use v1's uppercase column names, while Postgres returns the read result's columns lowercased;
// `text()` binds only the `:params` it references, so the extras are harmless.
function withUpper(o: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...o }
  for (const [k, v] of Object.entries(o)) out[k.toUpperCase()] = v
  return out
}
// The row's original (pre-edit) values, keyed `<NAME>_ORIGINAL` — so an `_put` query whose WHERE
// must match a column the user just edited (e.g. a business key) can bind `:<NAME>_ORIGINAL` to the
// old value. The verbatim-migrated v1 `_put` queries don't reference these (their WHERE reuses
// `:<NAME>`, so editing the key matches nothing — same behaviour as v1); they're forward-compat,
// and harmless when unused since `text()` only binds the `:params` the SQL actually mentions.
function originalKeys(row: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(row).map(([k, v]) => [`${k}_ORIGINAL`, v]))
}

// Slice 6 — row context menu action runner: resolve a list of ParamBinds against a row's live
// values. `value` binds are literal; `source` binds read another column on the same row
// (case-insensitive — the read result is lowercased by Postgres, ParamBinds usually carry the
// uppercase v1 column name). Reserved built-ins (`#LOGIN_USER#`/`#SYSDATE#`/…) are skipped here;
// they're wired in a future auth slice. Mirrors ScreenDialog's `resolveBindList` but bound
// against a row dict rather than the dialog's form state — same shape, different context.
function resolveRowBinds(
  binds: ReadonlyArray<{ param: string; value?: string | null; source?: string | null }> | undefined,
  row: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {}
  for (const b of binds ?? []) {
    if (b.value != null && b.value !== '') { out[b.param] = String(b.value); continue }
    if (b.source && !b.source.startsWith('#')) {
      const key = Object.keys(row).find((k) => k.toLowerCase() === b.source!.toLowerCase())
      const v = key != null ? row[key] : undefined
      if (v != null && String(v) !== '') out[b.param] = v
    }
  }
  return out
}

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
  ctrl, column, defaultText, onChange, lookupOptions, lookupRows, onLookupReturnValues, narrowBy,
}: {
  ctrl: EditCtrl; column: Column; defaultText: string; onChange: (v: unknown) => void
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
    let opts: { value: string; label: string; mono: string }[]
    if (ready && active.length > 0 && lookupRows) {
      // Case-insensitive column lookup on the raw rows: Postgres folds unquoted columns
      // to lowercase, the dictionary uses uppercase. Compare as strings (lookups are
      // code-based: "01" matches "01", coercion stays out of the picture).
      const valKey = rule.value
      const labKey = rule.label
      const matches = lookupRows.filter((r) => active.every(({ column: col, value }) => {
        const rv = r[col] ?? r[col.toLowerCase()] ?? r[col.toUpperCase()]
        return rv != null && String(rv) === String(value)
      }))
      opts = matches
        .map((r) => {
          const v = r[valKey] ?? r[valKey.toLowerCase()] ?? r[valKey.toUpperCase()]
          const l = r[labKey] ?? r[labKey.toLowerCase()] ?? r[labKey.toUpperCase()]
          return v == null
            ? null
            : { value: String(v), label: l == null ? String(v) : String(l), mono: String(v) }
        })
        .filter((o): o is { value: string; label: string; mono: string } => o !== null)
        .sort((a, b) => a.label.localeCompare(b.label))
    } else if (ready) {
      opts = [...lookupOptions!.entries()]
        .sort(([, a], [, b]) => a.localeCompare(b))
        .map(([value, label]) => ({ value, label, mono: value }))
    } else {
      opts = []
    }
    // Lookup-pick handler — writes the picked value as usual, then dispatches the rule's
    // ``return_params`` (v1's ly_lkp_params lkp_dir='OUT'): for each return_param dd_id,
    // find the picked row's matching column (case-insensitive) and fire
    // ``onLookupReturnValues`` with the {dd_id: value} map. The parent (ResultTable)
    // maps each dd to a sibling grid column and writes it to the same row.
    const handlePick = (picked: string) => {
      onChange(picked === '' ? null : picked)
      const returnParams = rule.return_params ?? []
      if (!picked || returnParams.length === 0 || !lookupRows || !onLookupReturnValues) return
      const valueCol = rule.value
      const row = lookupRows.find((r) => {
        const rv = r[valueCol] ?? r[valueCol.toLowerCase()] ?? r[valueCol.toUpperCase()]
        return rv != null && String(rv) === picked
      })
      if (!row) return
      const lcRow = new Map(Object.entries(row).map(([k, v]) => [k.toLowerCase(), v]))
      const aux: Record<string, unknown> = {}
      for (const dd of returnParams) {
        const v = lcRow.get(dd.toLowerCase())
        if (v !== undefined) aux[dd] = v
      }
      if (Object.keys(aux).length > 0) onLookupReturnValues(aux)
    }
    return (
      <SearchSelect
        value={defaultText}
        onChange={handlePick}
        options={opts}
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
    const v = activeFilters[field]
    if (v == null || v === '') return true
    return Array.isArray(value) ? value.includes(v) : v === value
  })
}

export function ResultTable({
  result, connector, query, updateQuery, insertQuery, deleteQuery, keyColumns, onSaved, runControl, maxRowsControl, activeFilters, screen,
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
}) {
  const { t } = useTranslation()
  // Used by the NavigateAction runtime — opens the target TableView via react-router's SPA nav,
  // which keeps the workspace's tab manager in charge (no full page reload).
  const navigate = useNavigate()
  const canEdit = !!(updateQuery || insertQuery)
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
    setDlgRow({}); setDlgMode('add'); setDlgOpen(true)
  }, [])

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

  // Action runner — sequentially runs the picked row-menu action(s); for v1 parity we run *one*
  // selected action per right-click, but the helper is list-based so a future "multi-fire" item
  // can reuse it. ParamBinds resolve against `ctx` (the row); run_query POSTs to /api/sql with
  // bound + uppercased params (falls back to the screen's effective connector); notify is
  // collected; refresh signals the parent. Unimplemented variants (call_api / navigate /
  // set_field / confirm) log a warning and stop the chain unless ``stop_on_error = false`` —
  // same convention the dialog on_save runner uses.
  //
  // **Prompt-before-fire** (v2's port of v1's ``ly_act_params``): an action with non-empty
  // ``prompt_fields`` opens the ActionPromptDialog *before* it runs; the operator's input merges
  // into ``ctx`` so this action's binds (and any cascading nested-context use) can read it.
  // Cancelling the prompt aborts the row action soft — no error surfaced.
  const [menuBusy, setMenuBusy] = useState<string | null>(null)  // action id while it's running
  const [menuError, setMenuError] = useState<string | null>(null)
  const runRowAction = useCallback(async (a: Action, ctx: DataRow) => {
    setMenuBusy(a.id); setMenuError(null)
    // Prompt-before-fire — merge the operator's input into ctx; cancel aborts soft.
    let runCtx: DataRow = ctx
    const prompt = actionPrompt(a)
    if (prompt) {
      const v = await requestPrompt(prompt, a.label || a.id)
      if (v == null) { setMenuBusy(null); closeMenu(); return }
      runCtx = { ...runCtx, ...v }
    }
    try {
      switch (a.type) {
        case 'run_query': {
          const target = a.connector || connector
          const bound = resolveRowBinds(a.param_binds, runCtx)
          await api.post(
            `/api/sql/${encodeURIComponent(target)}/${encodeURIComponent(a.query)}`,
            { params: withUpper(bound) },
          )
          break
        }
        case 'notify': {
          // No global toast surface yet — surface in the menu's status line for now.
          setMenuError(null)
          // eslint-disable-next-line no-console
          console.info('row-menu notify:', a.message)
          break
        }
        case 'refresh': {
          // implied by the onSaved() at the end of the success path
          break
        }
        case 'navigate': {
          // v1's "drill into another table" pattern: open the target TableView with the
          // source row's values bound as URL search params. The destination's TableView reads
          // those on mount and seeds its param form — so the destination opens already
          // filtered to whatever the row carries (e.g. "View this user's roles" → opens the
          // roles screen with USR_ID=<the-clicked-user-id>).
          const targetConnector = a.connector || connector
          const bound = resolveRowBinds(a.param_binds, runCtx)
          const qs = new URLSearchParams()
          for (const [k, v] of Object.entries(bound)) {
            if (v != null && String(v) !== '') qs.set(k, String(v))
          }
          const url =
            `/sql/${encodeURIComponent(targetConnector)}/${encodeURIComponent(a.to)}` +
            (qs.toString() ? `?${qs.toString()}` : '')
          // Close the menu *before* navigating — leaving it open during the route change makes the
          // overlay flicker on the destination page until the document-mousedown listener fires.
          setMenuBusy(null); closeMenu()
          navigate(url)
          return
        }
        case 'call_api':
        case 'set_field':
        case 'confirm': {
          const msg = `row-menu action '${a.id}' (${a.type}) — runtime not implemented yet`
          // eslint-disable-next-line no-console
          console.warn(msg)
          if (a.stop_on_error !== false) {
            setMenuError(msg); setMenuBusy(null); return
          }
          break
        }
      }
      setMenuBusy(null); closeMenu(); onSaved?.()
    } catch (e) {
      setMenuBusy(null)
      setMenuError(`${a.label || a.id}: ${e instanceof ApiError ? e.message : String(e)}`)
    }
  }, [connector, onSaved, closeMenu, navigate, requestPrompt])
  // Screen-level actions — v1's NOMAJDE toolbar buttons ("Create Role" / "Reset Password" / etc.)
  // attach here. Fire with **no row context** (the user uses row_menu for row-bound actions);
  // ParamBinds resolve to literal `value`s only — a `source` bind against an unset form falls
  // through to "no value". For more complex flows the operator wires a `confirm` or a future
  // input-dialog action; the runner uses the same Action shape as on_save / row_menu.
  const screenActions: Action[] = useMemo(() => (screen?.actions ?? []) as Action[], [screen])
  const [actionBusy, setActionBusy] = useState<string | null>(null)
  const [actionStatus, setActionStatus] = useState<{ message: string; tone: 'ok' | 'error' } | null>(null)
  const runScreenAction = useCallback(async (a: Action) => {
    setActionBusy(a.id); setActionStatus(null)
    // Toolbar actions have no row context — but with ``prompt_fields`` the operator supplies
    // the inputs the workflow needs (NOMAJDE "Create Role" / "Reset Password" / …). Merge the
    // entered values into the resolution context so ParamBinds with ``source: "<NAME>"`` pick
    // them up; cancel aborts soft (no error surfaced — operator clicked Cancel).
    let runCtx: DataRow = {}
    const prompt = actionPrompt(a)
    if (prompt) {
      const v = await requestPrompt(prompt, a.label || a.id)
      if (v == null) { setActionBusy(null); return }
      runCtx = v as DataRow
    }
    try {
      switch (a.type) {
        case 'run_query': {
          const target = a.connector || connector
          const bound = resolveRowBinds(a.param_binds, runCtx)
          await api.post(
            `/api/sql/${encodeURIComponent(target)}/${encodeURIComponent(a.query)}`,
            { params: withUpper(bound) },
          )
          setActionStatus({ message: a.label || a.id, tone: 'ok' })
          break
        }
        case 'notify': {
          setActionStatus({ message: a.message, tone: a.tone === 'error' ? 'error' : 'ok' })
          break
        }
        case 'refresh': {
          // The onSaved() below also triggers a refetch; explicit refresh is a no-op here.
          break
        }
        case 'navigate': {
          const targetConnector = a.connector || connector
          // Prompt values feed the URL query string (mirrors row-menu navigate).
          const bound = resolveRowBinds(a.param_binds, runCtx)
          const qs = new URLSearchParams()
          for (const [k, v] of Object.entries(bound)) {
            if (v != null && String(v) !== '') qs.set(k, String(v))
          }
          const url =
            `/sql/${encodeURIComponent(targetConnector)}/${encodeURIComponent(a.to)}` +
            (qs.toString() ? `?${qs.toString()}` : '')
          navigate(url)
          return
        }
        case 'call_api':
        case 'set_field':
        case 'confirm': {
          const msg = `screen action '${a.id}' (${a.type}) — runtime not implemented yet`
          // eslint-disable-next-line no-console
          console.warn(msg)
          setActionStatus({ message: msg, tone: 'error' })
          if (a.stop_on_error !== false) { setActionBusy(null); return }
          break
        }
      }
      setActionBusy(null)
      onSaved?.()
    } catch (e) {
      setActionBusy(null)
      setActionStatus({ message: `${a.label || a.id}: ${e instanceof ApiError ? e.message : String(e)}`, tone: 'error' })
    }
  }, [connector, onSaved, navigate, requestPrompt])

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

  const resetEdit = useCallback(() => {
    setEditMode(false); setDirtyRows(new Set()); setNewRows([]); setDeleted(new Set()); setSelected(new Set()); setSaveErrors([])
    editsRef.current = new Map()
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
    // Always create a fresh Set so React sees a state change on every edit — drives the
    // re-render needed by cascading LOOKUP cells whose dropdown options depend on a
    // same-row sibling's value (v1's bulk-edit cascading via ly_tbl_filters). Without
    // this, the dropdown's options would stay stale until something else triggered a
    // render. The text/number inputs use ``defaultValue`` (uncontrolled) so they keep
    // their typed text + focus across the re-render.
    if (!newRowsRef.current.includes(row)) setDirtyRows((s) => new Set(s).add(row))
  }, [])
  // add new rows at the TOP of the grid (newest first); `seeds` carries each row's initial values
  const prependNewRows = useCallback((seeds: Record<string, unknown>[]) => {
    if (seeds.length === 0) return
    const fresh = seeds.map((s) => {
      const row: DataRow = {}
      editsRef.current.set(row, { ...s })
      return row
    })
    setNewRows((p) => [...fresh, ...p])
    if (!editMode) setEditMode(true)
  }, [editMode])
  const addRow = useCallback(() => prependNewRows([{}]), [prependNewRows])
  const duplicateRow = useCallback((row: DataRow) => prependNewRows([valuesOf(row)]), [prependNewRows, valuesOf])
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
  const pasteRows = useCallback(() => prependNewRows(clipboard.map((r) => ({ ...r }))), [clipboard, prependNewRows])

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
        add(c.name); add(c.label); add(`${c.label ?? c.name} ${idTag}`); add(`${c.name} ${idTag}`)
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
      if (!editMode) setEditMode(true)

      const keys = (keyColumns ?? []).filter(Boolean)
      const newSeeds: Record<string, unknown>[] = []
      const matched: DataRow[] = []
      if (keys.length > 0) {
        const keyOf = (o: Record<string, unknown>) => keys.map((k) => String(o[k] ?? '')).join(' ')
        const byKey = new Map<string, DataRow>()
        for (const row of result.rows) byKey.set(keyOf(row), row)
        for (const seed of seeds) {
          const match = keys.every((k) => k in seed) ? byKey.get(keyOf(seed)) : undefined
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
  }, [prependNewRows, editMode, result.columns, result.rows, keyColumns, t])

  const save = useCallback(async () => {
    setSaving(true); setSaveErrors([])
    const jobs: Promise<unknown>[] = []
    const post = (q: string, params: Record<string, unknown>) =>
      jobs.push(api.post(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(q)}`, { params: withUpper(params) }))
    const localErrs: string[] = []
    // edited existing rows → `_put`: new values for SET, plus `:<NAME>_ORIGINAL` for a key-aware WHERE
    for (const row of dirtyRows) {
      if (deleted.has(row)) continue
      if (!updateQuery) { localErrs.push(t('table.editNoUpdate')); break }
      post(updateQuery, { ...row, ...editsRef.current.get(row), ...originalKeys(row) })
    }
    // new rows → `_post`: just the entered values (nothing existed before)
    for (const row of newRows) {
      if (deleted.has(row)) continue
      if (!insertQuery) { localErrs.push(t('table.editNoInsert')); break }
      post(insertQuery, editsRef.current.get(row) ?? {})
    }
    // rows marked for deletion → `_delete`: the current row identifies it (it wasn't edited if it's deleted)
    for (const row of deleted) {
      if (newRows.includes(row)) continue
      if (!deleteQuery) { localErrs.push(t('table.editNoDelete')); break }
      post(deleteQuery, { ...row })
    }
    const settled = await Promise.allSettled(jobs)
    const reqErrs = settled
      .filter((r): r is PromiseRejectedResult => r.status === 'rejected')
      .map((r) => (r.reason instanceof ApiError ? r.reason.message : String(r.reason)))
    const errs = [...new Set([...localErrs, ...reqErrs])]
    if (errs.length) {
      setSaving(false); setSaveErrors(errs); return
    }

    // Row-level lifecycle hooks (v2 extension of v1's ly_evt_cpt FormsTable events). For each
    // affected row, fire the matching chain — once per row, with that row's values as context.
    // Only ``run_query`` chain steps are supported here; ``notify`` is collected as a console
    // log, other types skip with a warning. Failures append to ``saveErrors`` so the operator
    // sees what went wrong; the row mutation itself already succeeded (the chains run *after*).
    const fireChain = async (actions: Action[] | undefined, ctx: DataRow): Promise<string | null> => {
      if (!actions?.length) return null
      for (const a of actions) {
        try {
          if (a.type === 'run_query') {
            const tgt = a.connector || connector
            const bound = resolveRowBinds(a.param_binds, ctx)
            await api.post(`/api/sql/${encodeURIComponent(tgt)}/${encodeURIComponent(a.query)}`, { params: withUpper(bound) })
          } else if (a.type === 'notify') {
            // eslint-disable-next-line no-console
            console.info('row-hook notify:', a.message)
          } else if (a.type === 'refresh') {
            // implied by the onSaved() below
          } else if (a.stop_on_error !== false) {
            return `${a.label || a.id} (${a.type}) — runtime not implemented yet`
          }
        } catch (e) {
          const msg = `${a.label || a.id}: ${e instanceof ApiError ? e.message : String(e)}`
          if (a.stop_on_error !== false) return msg
        }
      }
      return null
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
      const err = await fireChain(screen?.on_insert, editsRef.current.get(row) ?? {})
      if (err) hookErrs.push(err)
    }
    for (const row of deleted) {
      if (newRows.includes(row)) continue
      const err = await fireChain(screen?.on_delete, row)
      if (err) hookErrs.push(err)
    }
    setSaving(false)
    if (hookErrs.length) setSaveErrors([...new Set(hookErrs)])
    else { resetEdit(); onSaved?.() }
  }, [connector, dirtyRows, newRows, deleted, updateQuery, insertQuery, deleteQuery, onSaved, resetEdit, t, screen])

  // ── rule helpers (memoized per result) ──
  const enumMaps = useMemo(() => {
    const m = new Map<string, Map<string, string>>()
    for (const c of result.columns) if (c.rule?.kind === 'enum') m.set(c.name, enumMap(c.rule))
    return m
  }, [result.columns])
  const lookupSpecs = useMemo<LookupSpec[]>(
    () => result.columns.filter((c) => c.rule?.kind === 'lookup').map((c) => {
      const r = c.rule as Extract<NonNullable<Column['rule']>, { kind: 'lookup' }>
      // Forward the rule's static params (v1 ly_dictionary_filters → DictionaryEntry.lookup_params)
      // so a UDC-style lookup gets its SY/RT and returns the *right* rows. Different param sets
      // cache separately in services/lookups (specKey folds the params in).
      return { connector: r.connector, query: r.query, value: r.value, label: r.label, params: r.params }
    }),
    [result.columns],
  )
  // ``useLookupTables`` returns the full :class:`LookupData` (value→label map *plus* raw rows).
  // Same module cache as the older ``useLookupBatch`` — no extra fetches. The raw rows feed
  // the lookup-pick return-params write-back (v1's ly_lkp_params lkp_dir='OUT'): after a pick,
  // the matching row's other columns flow to sibling grid cells with matching ``dd``.
  const lookupMaps = useLookupTables(lookupSpecs)

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
  const cur = useCallback((row: DataRow, name: string) => {
    const ed = editsRef.current.get(row)
    return ed && name in ed ? ed[name] : row[name]
  }, [])

  const dataCols = useMemo<ColumnDef<DataRow, unknown>[]>(() => {
    const idSuffix = ` ${t('table.idColumnSuffix')}`
    // dd_id → column.name map across the result. Drives the lookup-return-params write-back
    // for inline edit (v1's ly_lkp_params lkp_dir='OUT'): when a picked lookup row exposes
    // extra columns, we write each return_param's value to the sibling grid column whose
    // ``dd`` matches. Case-insensitive (Postgres folds unquoted to lowercase). First wins.
    const ddToColName = new Map<string, string>()
    for (const c of shownColumns) {
      const dd = (c.dd || c.name).toLowerCase()
      if (!ddToColName.has(dd)) ddToColName.set(dd, c.name)
    }
    const editCellFor = (c: Column, info: { row: { original: unknown } }) => {
      const row = info.row.original as DataRow
      const v = cur(row, c.name)
      // For LOOKUP columns, pull the already-fetched ``LookupData`` (value→label *and* raw
      // rows) from ``lookupMaps``. The map drives the dropdown options; the rows let the
      // pick handler resolve return_params.
      const lookupData: LookupData | undefined = c.rule?.kind === 'lookup'
        ? lookupMaps.get(lookupKey({
            connector: c.rule.connector, query: c.rule.query,
            value: c.rule.value, label: c.rule.label, params: c.rule.params,
          }))
        : undefined
      // Lookup-pick return-values dispatcher — writes each return_param value to the
      // sibling grid column whose ``dd`` matches (in the *same* row). v1's "pick FSOBNM,
      // FSSY auto-populates" behaviour.
      const handleLookupReturnValues = (returnValues: Record<string, unknown>) => {
        for (const [dd, val] of Object.entries(returnValues)) {
          const targetCol = ddToColName.get(dd.toLowerCase())
          if (!targetCol) continue
          editChange(row, targetCol, val)
        }
      }
      // Cascading narrowing for LOOKUP cells (v1's ly_tbl_filters in bulk-edit). For each
      // ``filter_from`` dep (sibling column → lookup-row column), read the same row's
      // sibling cell via ``cur()`` (which already folds editsRef on top of the raw row)
      // and emit a {column, value} narrow entry when set. EditCell filters the lookup
      // rows on the fly so the dropdown shows only matching options (DRSY=01 → DRRT only
      // shows the RT codes where SY=01). Empty values mean "no narrowing for that dep".
      const narrowBy: { column: string; value: unknown }[] | undefined =
        c.rule?.kind === 'lookup' && c.filter_from && c.filter_from.length > 0
          ? c.filter_from.map((dep) => ({ column: dep.column, value: cur(row, dep.source) }))
          : undefined
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
        />
      )
    }
    const out: ColumnDef<DataRow, unknown>[] = []
    for (const c of shownColumns) {
      const align = cellAlign(c)

      if (c.rule?.kind === 'lookup') {
        const r = c.rule
        const data = lookupMaps.get(lookupKey({ connector: r.connector, query: r.query, value: r.value, label: r.label, params: r.params }))
        const map = data?.map
        out.push({
          id: c.name,
          header: colHeader(c) + idSuffix,
          accessorFn: (row) => row[c.name],
          size: c.width ?? undefined,
          ...filterPropsFor('text', undefined, align),
          cell: (info) => {
            const g = grouped(info, align); if (g) return g
            if (editMode && !isGroupRow(info)) return editCellFor(c, info)
            const v = cur(info.row.original as DataRow, c.name)
            const { text, isNull } = ruleCell(v, { ...c, rule: undefined }, undefined, undefined)
            return span(isNull ? 'null' : text, isNull ? 'null' : 'plain', align)
          },
        })
        out.push({
          id: `${c.name}__lookup`,
          header: colHeader(c),
          accessorFn: (row) => { const v = row[c.name]; return v === null || v === undefined ? '' : (map?.get(String(v)) ?? String(v)) },
          ...filterPropsFor('text'),
          cell: (info) => {
            const g = grouped(info, align); if (g) return g
            // derived from the "(ID)" column — read-only; reflects the *current* (possibly edited) code
            const raw = cur(info.row.original as DataRow, c.name)
            const { text, kind, isNull } = ruleCell(raw, c, undefined, map)
            return span(text, isNull ? 'null' : kind, align, isNull ? undefined : String(raw ?? ''))
          },
        })
        continue
      }

      const kind = filterKindOf(c)
      const enumMapForCol = enumMaps.get(c.name)
      const enumOptions = c.rule?.kind === 'enum' ? c.rule.values.map((v) => ({ value: v.label, label: v.label })) : undefined
      out.push({
        id: c.name,
        header: colHeader(c),
        accessorFn: (row) => {
          const v = row[c.name]
          if (c.rule?.kind === 'boolean') return v === null || v === undefined ? null : v === c.rule.true_value ? 'true' : 'false'
          if (c.rule?.kind === 'enum' && v != null) return enumMapForCol?.get(String(v)) ?? v
          return v
        },
        size: c.width ?? undefined,
        ...filterPropsFor(kind, enumOptions, align),
        cell: (info) => {
          const g = grouped(info, align); if (g) return g
          if (editMode && !isGroupRow(info)) return editCellFor(c, info)
          const v = cur(info.row.original as DataRow, c.name)
          const { text, kind: rk, isNull } = ruleCell(v, c, enumMapForCol, undefined)
          // Hover title: for ENUM cells, the raw code (the visible text is the label); for
          // BOOLEAN cells (now rendered as a colored bullet), the localized "yes"/"no" so the
          // value stays accessible to keyboard / screen-reader / colorblind users.
          const titleVal = isNull ? undefined
            : rk === 'enum' ? String(v ?? '')
            : rk === 'boolean-true' ? t('common.true')
            : rk === 'boolean-false' ? t('common.false')
            : undefined
          return span(text, isNull ? 'null' : rk, align, titleVal)
        },
      })
    }
    return out
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
              {insertQuery && !isDel && (
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
  }, [editMode, dirtyRows, newRows, deleted, selected, toggleDelete, duplicateRow, toggleSelected, insertQuery, isGroupRow, t])

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
      {insertQuery && (
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
        tableId={`sql:${connector}:${query}`}
        exportFilename={query}
        toolbarAfterSearch={runControl}
        toolbarRight={maxRowsControl}
        initialColumnVisibility={initialVisibility}
        rowClassName={(row) => (deleted.has(row) ? 'dt-row-deleted' : newRows.includes(row) ? 'dt-row-new' : dirtyRows.has(row) ? 'dt-row-dirty' : undefined)}
        // Row click opens the screen dialog (when a dialog exists *and* we're not in batch-edit mode)
        // — same v1 affordance: click a row to edit it via the form.
        onRowClick={
          !editMode
            ? hasDialog
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
          !canEdit && screenActions.length === 0 ? undefined : !editMode ? (
            <>
              {canEdit && hasDialog && screen?.insert_query && (
                <TbBtn $tone="primary" onClick={openDialogForAdd} title={t('dialog.addTooltip')}>
                  <Plus size={13} /> {t('table.addRow')}
                </TbBtn>
              )}
              {canEdit && (
                <TbBtn onClick={() => setEditMode(true)} title={t('table.editTip', { q: updateQuery ?? insertQuery ?? '' })}>
                  <Edit3 size={13} /> {hasDialog ? t('table.bulkEdit') : t('table.edit')}
                </TbBtn>
              )}
              {canEdit && insertQuery && (
                <TbBtn onClick={() => fileRef.current?.click()} title={t('table.import')}>
                  <Upload size={13} /> {t('table.import')}
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
              {insertQuery && (
                <>
                  <TbBtn onClick={addRow} disabled={saving} title={t('table.addRow')}>
                    <Plus size={13} /> {t('table.addRow')}
                  </TbBtn>
                  <TbBtn onClick={() => fileRef.current?.click()} disabled={saving} title={t('table.import')}>
                    <Upload size={13} /> {t('table.import')}
                  </TbBtn>
                </>
              )}
              <TbBtn onClick={copySelected} disabled={saving || selected.size === 0} title={t('table.copyRows')}>
                <Copy size={13} /> {t('table.copyRows')}{selected.size ? ` (${selected.size})` : ''}
              </TbBtn>
              {insertQuery && (
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
