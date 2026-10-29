// The grid's batch edit ("Bulk edit") — owned by ResultTable: the pending edits of existing rows
// (uncontrolled inputs write into ``editsRef``), the new / deleted / selected rows, the clipboard,
// and the Save that writes them all through the same path as the dialog (``saveScreenRow``) then
// fires the row hooks (on_update / on_insert / on_duplicate / on_delete).
import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { QueryResult } from '../../types/connectors'
import type { Action, ColumnGroup, ScreenDetail } from '../../types/screens'
import { ApiError } from '../../api/client'
import { entityKeyOf, forcedDefault } from './dialogHelpers'
import { saveScreenRow, deleteScreenRow } from './saveScreenRow'
import { runChain, type ActionRunnerDeps } from './actionRunner'
import type { DataRow } from './resultColumns'

export function useBatchEdit({
  result, screen, connector, updateQuery, insertQuery, deleteQuery, addSeed, requestPrompt, sharedActions, onSaved,
}: {
  result: QueryResult
  screen?: ScreenDetail | null
  connector: string
  updateQuery?: string | null
  insertQuery?: string | null
  deleteQuery?: string | null
  /** The values a blank "+ Add row" starts with. */
  addSeed?: Record<string, unknown>
  requestPrompt: ActionRunnerDeps['requestPrompt']
  sharedActions: ActionRunnerDeps['sharedActions'] | null
  onSaved?: () => void
}) {
  const { t } = useTranslation()
  const [editMode, setEditMode] = useState(false)
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
  // A copy keeps every value — keys included (F00950: copy, change the role) — except the columns a
  // SEQUENCE fills on insert: left empty, the insert takes a new one (and the 1:1 groups follow it).
  const withoutSequence = useCallback((v: Record<string, unknown>) => {
    const seq = new Set(result.columns.filter((c) => c.sequence).map((c) => c.name.toLowerCase()))
    return seq.size ? Object.fromEntries(Object.entries(v).filter(([k]) => !seq.has(k.toLowerCase()))) : { ...v }
  }, [result.columns])
  const duplicateRow = useCallback((row: DataRow) => { const v = valuesOf(row); prependNewRows([withoutSequence(v)], [v]) }, [prependNewRows, valuesOf, withoutSequence])
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
  // Bulk delete the ticked rows in one go (the per-row × is tedious for many): a NEW row is dropped
  // outright, an existing row is marked for deletion (→ delete_query on Save). Clears the selection.
  const deleteSelected = useCallback(() => {
    const sel = [...selected]
    if (sel.length === 0) return
    const isNew = (r: DataRow) => newRowsRef.current.includes(r)
    const newSel = sel.filter(isNew)
    const existing = sel.filter((r) => !isNew(r))
    if (newSel.length) { setNewRows((p) => p.filter((r) => !newSel.includes(r))); for (const r of newSel) editsRef.current.delete(r) }
    if (existing.length) setDeleted((s) => { const n = new Set(s); for (const r of existing) n.add(r); return n })
    setSelected(new Set())
  }, [selected])
  const copySelected = useCallback(() => setClipboard([...selected].map((r) => valuesOf(r))), [selected, valuesOf])
  // Paste copied rows as new rows — each is a duplicate of the row it was copied from (the clipboard
  // snapshot is the source), so Save fires ``on_duplicate`` for them just like the Copy button.
  const pasteRows = useCallback(() => prependNewRows(clipboard.map((r) => withoutSequence(r)), clipboard.map((r) => ({ ...r }))), [clipboard, prependNewRows, withoutSequence])

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

  return {
    editMode, setEditMode, dirtyRows, setDirtyRows, newRows, deleted, selected, setSelected,
    clipboard, saving, saveErrors, setSaveErrors, editsRef, resetEdit,
    dirtyCount, valuesOf, newRowsRef, editChange, prependNewRows, addRow, duplicateRow,
    toggleDelete, toggleSelected, deleteSelected, copySelected, pasteRows, save,
  }
}
