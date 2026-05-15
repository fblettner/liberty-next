// Nested-tab content kinds — embedded directly inside a parent ScreenDialog tab. Two
// flavours (matching the discriminated-union types in `types/screens.ts`):
//
//   • NestedFormView — v2's port of v1's "FormsDialog inside a FormsDialog". Loads the
//     linked single-record row (parent's PK is bound into the nested read_query via
//     param_binds) and displays the configured fields. Slice 1: **read-only display** —
//     the empty tabs the user complained about ("JD Edwards", "LDAP" on
//     security_applications) are now populated. Slice 2 will add inline editing + the
//     parent-Save → nested-save coordination (a register/dispatch pattern via a shared
//     ref on the parent ScreenDialog).
//
//   • NestedTableView — v2's port of v1's "FormsTable inside a FormsDialog". References
//     another v2 Screen by id (e.g. parent SETTINGS_APPLICATIONS' "Activity Log" tab →
//     standalone settings_activity_log screen), fetches its rows narrowed by the parent
//     PK, and renders a basic DataTable. Read-only for now (the activity_log / audit_trail
//     case is naturally read-only anyway). Drill into a row, or full TableView pipeline
//     re-use, lands in a follow-up.
//
// Both wait for their bound params to resolve before fetching — a not-yet-saved "add"
// parent (APPS_ID still undefined) shows an inert empty state instead of firing a request
// that would return everything in the table.
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Plus } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, SpinnerRing } from '../../common'
import { DataTable } from '../../common/DataTable'
import type { Column, QueryResult } from '../../types/connectors'
import type { NestedFormTab, NestedTableTab, ScreenDetail } from '../../types/screens'
import { evalConditions, originalKeys, resolveBindList, type Row, valueFor, withUpper } from './dialogHelpers'
import { CellWrap, FieldRow, isPassword } from './FieldRow'
import { colors, fontSize } from '../../theme'
// Circular import: ScreenDialog also imports NestedFormView/NestedTableView. ESM resolves
// these at module-evaluation time but the binding is only *used* at render time (inside
// JSX), so by the time React calls the sub-dialog the import has fully resolved. Same
// pattern ResultTable uses to render ScreenDialog without a circle (ResultTable → ScreenDialog
// is one-way, this one's two-way but both consume the binding lazily through JSX).
import { NestedSaversContext, ScreenDialog, type DialogMode } from './ScreenDialog'

const FieldGrid = styled.div<{ $cols: number }>`
  display: grid; grid-template-columns: repeat(${({ $cols }) => $cols}, 1fr); gap: 12px;
`
const Hint = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 6px 0;
`
const LoadingRow = styled.div`
  display: flex; align-items: center; gap: 8px; color: ${colors.text.muted}; font-size: ${fontSize.sm};
`
const TableWrap = styled.div`
  display: flex; flex-direction: column; min-height: 0; height: 100%;
`

/** Build the query-string for a bound GET — same convention the TableView uses. Empty when
 *  there are no resolved binds (the param form expects narrowing; no narrowing → the URL
 *  carries no params, which we treat as "skip fetch" so a not-yet-saved parent doesn't
 *  trigger a giant SELECT). */
function bindsToQuery(bound: Record<string, string>): string {
  const parts = Object.entries(bound).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

/** Resolve `tab.connector` against the parent screen's effective connector (the dialog's
 *  `connector` prop). Matches the migration / runtime convention: blank → parent's. */
function effectiveConnector(tab: { connector?: string | null }, parentConnector: string): string {
  return (tab.connector && tab.connector.trim()) || parentConnector
}

// ── NestedFormView ──────────────────────────────────────────────────────────
// A child-record form embedded inline in this tab — v2's port of v1's "FormsDialog inside
// FormsDialog". Loads the linked row via the nested `read_query` narrowed by `param_binds`
// resolved against parent form state, then renders the configured fields with FieldRow
// (same widget set as the top-level dialog: BOOLEAN → Checkbox, ENUM/LOOKUP → SearchSelect,
// password → masked input, numeric/date/text). The user edits in place; the save runs
// **after the parent's main update** via the NestedSaversContext — register a save fn on
// mount, the parent's submit() walks the registry and awaits each. A failing nested save
// surfaces on the parent's banner without rolling back the main row (idempotent at the
// nested layer: retrying re-fires update/insert with the current form state).

export function NestedFormView({
  tab, parentFormValues, parentConnector,
}: {
  tab: NestedFormTab
  /** Live parent form state — `source` binds read from here at fetch time. */
  parentFormValues: Row
  /** The parent screen's effective connector — used when `tab.connector` is blank. */
  parentConnector: string
}) {
  const { t } = useTranslation()
  const connector = effectiveConnector(tab, parentConnector)
  const savers = useContext(NestedSaversContext)

  // Resolve bound params against the live parent state. JSON-stringified key drives the
  // fetch effect — re-running the read query when the parent's PK / context changes.
  const bound = useMemo(() => resolveBindList(tab.param_binds, parentFormValues), [tab.param_binds, parentFormValues])
  const boundKey = useMemo(() => JSON.stringify(bound), [bound])
  // Skip the fetch entirely when no binds resolved (a not-yet-saved parent in "add" mode):
  // firing the read_query with no params would return every row in the underlying table.
  const hasBinds = Object.keys(bound).length > 0

  const [result, setResult] = useState<QueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Local form state — separate from the parent's so editing in JD Edwards doesn't pollute
  // the main `formValues`. Each nested form owns its own fields' values.
  const [formValues, setFormValues] = useState<Row>({})
  const [savedRow, setSavedRow] = useState<Row>({})  // pre-edit values keyed by field name (for `:_ORIGINAL` binds)
  // True once a linked row has been loaded (i.e. there's something to UPDATE). Drives
  // update_query vs insert_query selection on save.
  const isExistingRef = useRef(false)
  // User-touched flag — only fire insert when add-mode and the user actually typed
  // something (otherwise navigating to a never-used JD Edwards tab would insert empty rows).
  const touchedRef = useRef(false)

  useEffect(() => {
    if (!hasBinds) {
      setResult(null); setError(null); isExistingRef.current = false; return
    }
    setLoading(true); setError(null)
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(tab.read_query)}${bindsToQuery(bound)}`)
      .then((r) => setResult(r))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boundKey is the stable serialization of bound
  }, [connector, tab.read_query, boundKey, hasBinds])

  const colByName = useMemo(() => {
    const m = new Map<string, Column>()
    for (const c of result?.columns ?? []) m.set(c.name.toLowerCase(), c)
    return m
  }, [result])

  // Seed local form state from the loaded row (edit mode) or from the bind values + field
  // defaults (add mode — no linked record yet). Password fields are NEVER seeded: same rule
  // as the top-level dialog (the stored value is a hash / ENC: blob — leaking it into the
  // form means it gets posted back). `savedRow` keeps the original (sans-password) for
  // `:_ORIGINAL` rebinds.
  useEffect(() => {
    if (!result) return
    const row = result.rows?.[0] ?? null
    isExistingRef.current = row != null
    touchedRef.current = false
    const seeded: Row = {}
    const original: Row = {}
    if (row) {
      for (const f of tab.fields) {
        const col = colByName.get(f.name.toLowerCase()) ?? null
        const v = valueFor(f.name, row)
        if (v !== undefined) original[f.name] = v
        if (isPassword(col)) continue
        if (v !== undefined) seeded[f.name] = v
      }
    } else {
      // Add mode — seed FK columns from bind values + field defaults.
      for (const f of tab.fields) {
        const fromBind = bound[f.name] ?? bound[f.name.toUpperCase()]
        if (fromBind !== undefined) seeded[f.name] = fromBind
        else if (f.default != null && f.default !== '') seeded[f.name] = f.default
      }
    }
    setFormValues(seeded)
    setSavedRow(original)
  }, [result, tab.fields, colByName, bound])

  const onFieldChange = useCallback((name: string, v: unknown) => {
    touchedRef.current = true
    setFormValues((p) => ({ ...p, [name]: v }))
  }, [])

  // Visibility / required / disabled (same rule engine as the top-level dialog) — re-eval
  // every render against the current formValues so a field gating on a sibling reacts live.
  const fieldStateOf = useCallback((f: typeof tab.fields[number]) => {
    const visibleByRule = (f.visible_when?.length ?? 0) > 0
      ? evalConditions(f.visible_when, formValues)
      : !f.hidden
    const requiredByRule = (f.required_when?.length ?? 0) > 0
      ? evalConditions(f.required_when, formValues)
      : !!f.required
    const disabledByRule = (f.disabled_when?.length ?? 0) > 0
      ? evalConditions(f.disabled_when, formValues)
      : !!f.disabled
    return { visible: visibleByRule, required: requiredByRule, disabled: disabledByRule }
  }, [formValues])

  // Register the save fn with the parent ScreenDialog. Re-registers each render so the
  // closure captures the *latest* formValues / mode — the registry is a `Map`, so a
  // re-register simply overwrites the previous entry. Unregister on unmount.
  useEffect(() => {
    if (!savers) return
    const save = async () => {
      // Add-mode + the user never touched the form: skip insert. Otherwise we'd create
      // empty rows just because the user opened the parent dialog and ignored this tab.
      const isExisting = isExistingRef.current
      if (!isExisting && !touchedRef.current) return

      // Collect values from visible fields only (a hidden field on save isn't written —
      // same convention the top-level dialog uses). Drop empty password values.
      const sent: Row = {}
      for (const f of tab.fields) {
        if (!fieldStateOf(f).visible) continue
        if (!(f.name in formValues)) continue
        const v = formValues[f.name]
        const col = colByName.get(f.name.toLowerCase()) ?? null
        if (isPassword(col) && (v == null || v === '')) continue
        sent[f.name] = v
      }
      // Bound FK columns (e.g. APPS_ID) — make sure they're posted so an insert ties the
      // child to its parent and an update's WHERE on a key column has the right value.
      for (const [k, v] of Object.entries(bound)) {
        if (!(k in sent) && !(k.toLowerCase() in sent)) sent[k] = v
      }

      const targetQuery = isExisting ? tab.update_query : tab.insert_query
      if (!targetQuery) {
        throw new Error(t(isExisting ? 'table.editNoUpdate' : 'table.editNoInsert'))
      }
      // For an update, also bind `:<COL>_ORIGINAL` for the migrated _put's WHERE rebind.
      // Same handling as the top-level dialog — skip password originals (we don't have the
      // ciphertext on hand) and skip empty originals.
      const baseSaved: Row = isExisting
        ? Object.fromEntries(Object.entries(savedRow).filter(([k]) => {
            const c = colByName.get(k.toLowerCase()) ?? null
            return !isPassword(c)
          }))
        : {}
      const params = isExisting
        ? { ...baseSaved, ...sent, ...originalKeys(baseSaved) }
        : sent
      await api.post(
        `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(targetQuery)}`,
        { params: withUpper(params) },
      )
    }
    savers.register(tab.id, save)
    return () => savers.unregister(tab.id)
  }, [savers, tab.id, tab.fields, tab.update_query, tab.insert_query, formValues, savedRow, bound, colByName, connector, fieldStateOf, t])

  const cols = Math.max(1, tab.cols ?? 2)
  if (!hasBinds) return <Banner $tone="info">{t('dialog.nested.pendingBinds')}</Banner>
  if (loading && !result) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  if (error) return <Banner $tone="error">{error}</Banner>
  // Even in add mode (no linked row) we render the form — the user can type values and
  // saving will fire insert_query (binding the FK columns from `bound`). The Hint banner
  // surfaces the "no record yet" state so it's not invisible.
  return (
    <div>
      {!isExistingRef.current && result && <Hint>{t('dialog.nested.noRecord')}</Hint>}
      <FieldGrid $cols={cols}>
        {tab.fields
          .filter((f) => fieldStateOf(f).visible)
          .map((f) => {
            const st = fieldStateOf(f)
            return (
              <FieldRow
                key={f.name}
                field={f}
                column={colByName.get(f.name.toLowerCase()) ?? null}
                formValues={formValues}
                onChange={onFieldChange}
                disabled={st.disabled}
                required={st.required}
              />
            )
          })}
        {tab.fields.filter((f) => fieldStateOf(f).visible).length === 0 && (
          <CellWrap $span={cols}>
            <Banner $tone="info">{t('dialog.noVisibleFields')}</Banner>
          </CellWrap>
        )}
      </FieldGrid>
    </div>
  )
}

// ── NestedTableView ─────────────────────────────────────────────────────────
// A related-rows table embedded inline. References another v2 Screen by id; loads its
// read query narrowed by `param_binds` resolved against parent form state.

export function NestedTableView({
  tab, parentFormValues, parentConnector, app,
}: {
  tab: NestedTableTab
  parentFormValues: Row
  parentConnector: string
  /** The parent screen's app — used to look up the nested screen's detail catalog entry
   *  (GET /api/screens/{app}/{id}). The nested screen lives under the same app since
   *  screens.toml's [screens.<app>.<id>] keys by app. */
  app: string
}) {
  const { t } = useTranslation()
  const connector = effectiveConnector(tab, parentConnector)
  const bound = useMemo(() => resolveBindList(tab.param_binds, parentFormValues), [tab.param_binds, parentFormValues])
  const boundKey = useMemo(() => JSON.stringify(bound), [bound])
  const hasBinds = Object.keys(bound).length > 0

  const [nestedScreen, setNestedScreen] = useState<ScreenDetail | null>(null)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Sub-dialog state — set by row click (edit mode) or by the "Add" button (add mode with the
  // bind values pre-filled on the FK columns so the dialog opens already tied to this parent).
  const [subDialog, setSubDialog] = useState<{ mode: DialogMode; row: Row } | null>(null)
  // A simple counter to force a refetch after the sub-dialog saves.
  const [refreshTick, setRefreshTick] = useState(0)

  // Fetch the nested screen's detail once — we need its read_query name, columns hints, and
  // dialog body to open on row click.
  useEffect(() => {
    setError(null)
    api.get<ScreenDetail>(`/api/screens/${encodeURIComponent(app)}/${encodeURIComponent(tab.screen)}`)
      .then((s) => setNestedScreen(s))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [app, tab.screen])

  useEffect(() => {
    if (!nestedScreen || !hasBinds) { setResult(null); return }
    setLoading(true); setError(null)
    const q = nestedScreen.read_query
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(q)}${bindsToQuery(bound)}`)
      .then((r) => setResult(r))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boundKey serializes bound
  }, [nestedScreen, connector, boundKey, hasBinds, refreshTick])

  // Minimal column defs — one TanStack column per result column, displaying the raw value.
  // No rules (BOOLEAN / ENUM / LOOKUP), no filters, no batch edit yet — that lands when /
  // if the user asks for more. Read display is enough for activity_log / audit_trail and
  // good enough for editable rules tables since clicking a row opens the full ScreenDialog.
  // Password-typed columns are dropped entirely — never display a stored hash / ENC: blob
  // in a grid cell, regardless of whether the migration flagged it ``hidden`` (v1's audit
  // tables didn't always do so). The full ScreenDialog still lets the user *set* a new
  // password through its masked input; the grid is read-only context.
  const columns = useMemo(() => {
    const cols = result?.columns ?? []
    return cols
      .filter((c) => !c.hidden && (c.format ?? '').toLowerCase() !== 'password')
      .map((c) => ({
        id: c.name,
        accessorFn: (r: Row) => r[c.name] ?? r[c.name.toLowerCase()],
        header: c.label ?? c.name,
        cell: ({ getValue }: { getValue: () => unknown }) => {
          const v = getValue()
          return v == null ? '' : String(v)
        },
      }))
  }, [result])

  // Open the sub-dialog in "add" mode with the FK columns pre-filled from the bind values
  // (e.g. parent's APPS_ID = 7 → ACL_APPS_ID = 7 on the activity-log row). The dialog's
  // valueFor() lookup is case-insensitive, so binds named `ACL_APPS_ID` will still be picked
  // up if the read result column comes back lowercased.
  const handleAdd = useCallback(() => {
    if (!nestedScreen?.dialog) return
    const seed: Row = { ...bound }   // bind keys are the v1 uppercase column names
    setSubDialog({ mode: 'add', row: seed })
  }, [nestedScreen, bound])

  const handleRowClick = useCallback((r: Row) => {
    if (!nestedScreen?.dialog) return
    setSubDialog({ mode: 'edit', row: r })
  }, [nestedScreen])

  // The nested screen's connector — falls back to the explicit `tab.connector` and then to
  // the parent's. (The nested screen's own `connector` is sourced from the catalog response.)
  const nestedConnector = nestedScreen?.connector || connector

  if (!hasBinds) return <Banner $tone="info">{t('dialog.nested.pendingBinds')}</Banner>
  if (error) return <Banner $tone="error">{error}</Banner>
  if (loading && !result) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  if (!result) return null

  const canAdd = !!(nestedScreen?.dialog && nestedScreen?.insert_query)
  const canEdit = !!(nestedScreen?.dialog && nestedScreen?.update_query)

  return (
    <TableWrap>
      <DataTable
        tableId={`nested-${app}-${tab.screen}`}
        data={(result.rows as Row[]) ?? []}
        columns={columns}
        toolbar={canAdd ? (
          <Button $size="sm" $variant="primary" onClick={handleAdd}>
            <Plus size={13} /> {t('table.addRow')}
          </Button>
        ) : undefined}
        onRowClick={canEdit ? handleRowClick : undefined}
      />
      {subDialog && nestedScreen && (
        <ScreenDialog
          open
          nested
          mode={subDialog.mode}
          screen={nestedScreen}
          columns={result.columns}
          row={subDialog.row}
          connector={nestedConnector}
          onClose={() => setSubDialog(null)}
          onSaved={() => { setSubDialog(null); setRefreshTick((n) => n + 1) }}
        />
      )}
    </TableWrap>
  )
}
