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
import type { ColumnDef, VisibilityState } from '@tanstack/react-table'
import styled from '@emotion/styled'
import * as XLSX from 'xlsx'
import { Pencil, Check, X, Plus, Copy, ClipboardPaste, Upload } from 'lucide-react'
import type { Column, QueryResult } from '../../types/connectors'
import { api, ApiError } from '../../api/client'
import { Banner } from '../../common'
import { DataTable } from '../../common/DataTable'
import { genericFilterFn, type FilterKind, type FilterMeta } from '../../common/DataTableFilter'
import { enumMap, ruleCell } from '../../services/cells'
import { lookupKey, useLookupBatch, type LookupSpec } from '../../services/lookups'
import { colors, fontSize, fonts, radius } from '../../theme'
import { CellSpan } from './styled'

type DataRow = Record<string, unknown>
type Align = CSSProperties['textAlign']
type EditCtrl = 'enum' | 'date' | 'number' | 'text'

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
  if (c.rule?.kind === 'enum') return 'enum'
  const fmt = (c.format ?? '').toLowerCase(), typ = (c.type ?? '').toLowerCase()
  if (isDateish(fmt, typ)) return 'date'
  if (isNumericish(fmt, typ)) return 'number'
  return 'text'  // boolean codes & everything else → raw text
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

// ── edit-mode controls ──────────────────────────────────────────────────────
const EditInput = styled.input`
  width: 100%; box-sizing: border-box; background: ${colors.bg.input};
  border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  padding: 2px 6px; outline: none;
  &:focus { border-color: ${colors.blue.border}; box-shadow: 0 0 0 2px ${colors.blue.bg}; }
`
const EditSelect = styled.select`
  width: 100%; box-sizing: border-box; background: ${colors.bg.input};
  border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  padding: 2px 4px; cursor: pointer;
  &:focus { border-color: ${colors.blue.border}; }
  option { background: ${colors.bg.dropdown}; color: ${colors.text.secondary}; }
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

function EditCell({ ctrl, column, defaultText, onChange }: {
  ctrl: EditCtrl; column: Column; defaultText: string; onChange: (v: unknown) => void
}) {
  if (ctrl === 'enum' && column.rule?.kind === 'enum') {
    return (
      <EditSelect defaultValue={defaultText} onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}>
        <option value="">—</option>
        {column.rule.values.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
      </EditSelect>
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
  result, connector, query, updateQuery, insertQuery, deleteQuery, keyColumns, onSaved, runControl, maxRowsControl, activeFilters,
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
}) {
  const { t } = useTranslation()
  const canEdit = !!(updateQuery || insertQuery)
  // the columns to actually show: drop any whose `visible_when` filter doesn't match right now
  // (TableView passes a memoized `activeFilters`, so this stays referentially stable across re-renders).
  const shownColumns = useMemo(() => result.columns.filter((c) => columnVisibleNow(c, activeFilters ?? {})), [result.columns, activeFilters])

  // ── batch-edit state ──
  const [editMode, setEditMode] = useState(false)
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
    if (!newRowsRef.current.includes(row)) setDirtyRows((s) => (s.has(row) ? s : new Set(s).add(row)))
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
    setSaving(false)
    const errs = [...new Set([...localErrs, ...reqErrs])]
    if (errs.length) setSaveErrors(errs)
    else { resetEdit(); onSaved?.() }
  }, [connector, dirtyRows, newRows, deleted, updateQuery, insertQuery, deleteQuery, onSaved, resetEdit, t])

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
  const lookupMaps = useLookupBatch(lookupSpecs)

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
    const editCellFor = (c: Column, info: { row: { original: unknown } }) => {
      const row = info.row.original as DataRow
      const v = cur(row, c.name)
      return <EditCell ctrl={editCtrlOf(c)} column={c} defaultText={v === null || v === undefined ? '' : String(v)} onChange={(nv) => editChange(row, c.name, nv)} />
    }
    const out: ColumnDef<DataRow, unknown>[] = []
    for (const c of shownColumns) {
      const align = cellAlign(c)

      if (c.rule?.kind === 'lookup') {
        const r = c.rule
        const map = lookupMaps.get(lookupKey({ connector: r.connector, query: r.query, value: r.value, label: r.label, params: r.params }))
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
          return span(text, isNull ? 'null' : rk, align, rk === 'enum' && !isNull ? String(v ?? '') : undefined)
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
        toolbar={
          !canEdit ? undefined : !editMode ? (
            <>
              <TbBtn onClick={() => setEditMode(true)} title={t('table.editTip', { q: updateQuery ?? insertQuery ?? '' })}>
                <Pencil size={13} /> {t('table.edit')}
              </TbBtn>
              {insertQuery && (
                <TbBtn onClick={() => fileRef.current?.click()} title={t('table.import')}>
                  <Upload size={13} /> {t('table.import')}
                </TbBtn>
              )}
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
    </>
  )
}

export default ResultTable
