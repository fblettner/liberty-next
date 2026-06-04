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
  tab, parentFormValues, parentConnector, app, parentMode,
}: {
  tab: NestedFormTab
  /** Live parent form state — `source` binds read from here at fetch time. */
  parentFormValues: Row
  /** The parent screen's effective connector — used when `tab.connector` is blank. */
  parentConnector: string
  /** The parent screen's app — used to fetch the referenced screen in reference mode
   *  (GET /api/screens/{app}/{form_screen}). */
  app: string
  /** The PARENT dialog's mode. In ``add`` the parent has no PK yet (it's sequence-assigned on
   *  its own insert), so the FK binds can't resolve — but we still render the nested form so the
   *  operator can fill it; the FK is supplied at save time via ``parentExtra``. */
  parentMode: DialogMode
}) {
  const { t } = useTranslation()
  const savers = useContext(NestedSaversContext)

  // Reference mode: when ``tab.form_screen`` is set this tab reuses an existing screen's form —
  // its read/update/insert queries + fields come from that screen, not from the tab. We fetch the
  // referenced screen's detail and derive the effective config below. Inline mode (no form_screen)
  // uses the tab's own queries + fields exactly as before.
  const isRef = !!tab.form_screen
  const [refScreen, setRefScreen] = useState<ScreenDetail | null>(null)
  const [refError, setRefError] = useState<string | null>(null)
  useEffect(() => {
    if (!isRef || !tab.form_screen) { setRefScreen(null); return }
    setRefError(null)
    let cancelled = false
    api.get<ScreenDetail>(`/api/screens/${encodeURIComponent(app)}/${encodeURIComponent(tab.form_screen)}`)
      .then((s) => { if (!cancelled) setRefScreen(s) })
      .catch((e) => { if (!cancelled) setRefError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [isRef, app, tab.form_screen])

  // Effective form config — from the referenced screen (reference mode) or the tab (inline mode).
  // The referenced screen's fields are, in priority order:
  //   1. its dialog form-tab fields, flattened in display order (full field config — a referenced
  //      screen's grouping into sub-tabs collapses into one inline grid here), else
  //   2. its ``columns`` (Phase-3 single-source column hints) mapped to fields — covers screens
  //      that drive their dialog from ``Screen.columns`` rather than explicit dialog fields.
  const refFields = useMemo<NestedFormTab['fields']>(() => {
    if (!refScreen) return []
    const fromDialog: NestedFormTab['fields'] = []
    for (const tb of refScreen.dialog?.tabs ?? []) {
      if (Array.isArray((tb as { fields?: unknown }).fields)) fromDialog.push(...(tb as NestedFormTab).fields)
    }
    if (fromDialog.length) return fromDialog
    return (refScreen.columns ?? []).map((c) => ({
      name: c.name,
      label: c.label,
      format: c.format,
      hidden: c.hidden,
      rule: c.rule,
    }))
  }, [refScreen])
  const inlineConnector = effectiveConnector(tab, parentConnector)
  const connector = isRef ? (refScreen?.connector || inlineConnector) : inlineConnector
  const effRead = (isRef ? refScreen?.read_query : tab.read_query) ?? ''
  const effUpdate = (isRef ? refScreen?.update_query : tab.update_query) ?? null
  const effInsert = (isRef ? refScreen?.insert_query : tab.insert_query) ?? null
  const effFields = isRef ? refFields : tab.fields

  // Resolve bound params against the live parent state. JSON-stringified key drives the
  // fetch effect — re-running the read query when the parent's PK / context changes.
  const bound = useMemo(() => resolveBindList(tab.param_binds, parentFormValues), [tab.param_binds, parentFormValues])
  const boundKey = useMemo(() => JSON.stringify(bound), [bound])
  // Skip the fetch entirely when no binds resolved (a not-yet-saved parent in "add" mode):
  // firing the read_query with no params would return every row in the underlying table.
  // In reference mode we also wait for the referenced screen's read_query to resolve — until then
  // ``effRead`` is '' and firing a query with an empty name would 404.
  const hasBinds = Object.keys(bound).length > 0 && !!effRead

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
    // NEVER read in parent-add mode: the parent row doesn't exist yet, so it has no linked child.
    // Reading here is actively dangerous — if the parent form carries a stale/seeded PK (e.g. the
    // previously-selected row's), the read would load THAT parent's child, mark it "existing", and
    // on Save fire an UPDATE that rebinds the FK to the new parent — moving one record's child onto
    // another. In add mode we always start empty and INSERT, bound to the parent's freshly-assigned
    // PK (via parentExtra) at save time.
    if (!hasBinds || parentMode === 'add') {
      setResult(null); setError(null); isExistingRef.current = false; return
    }
    setLoading(true); setError(null)
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(effRead)}${bindsToQuery(bound)}`)
      .then((r) => setResult(r))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boundKey is the stable serialization of bound
  }, [connector, effRead, boundKey, hasBinds, parentMode])

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
    // Pick the row that MATCHES the bind — never blindly rows[0]. The nested read is meant to
    // narrow to the single linked child via param_binds (parent PK → :param), but a migrated read
    // query may lack the WHERE clause and return every row. Trusting rows[0] would then show /
    // update a DIFFERENT parent's child (e.g. apps_id 1's row while editing apps_id 2). Match each
    // bound value against the row's matching column (case-insensitive); fall back to rows[0] only
    // when there are no binds (legacy single-record forms with no parent key).
    const rows = (result.rows ?? []) as Row[]
    const boundEntries = Object.entries(bound)
    const row = boundEntries.length === 0
      ? (rows[0] ?? null)
      : (rows.find((r) => boundEntries.every(([k, v]) => {
          const rv = valueFor(k, r)
          return rv != null && String(rv) === String(v)
        })) ?? null)
    isExistingRef.current = row != null
    touchedRef.current = false
    const seeded: Row = {}
    const original: Row = {}
    if (row) {
      for (const f of effFields) {
        const col = colByName.get(f.name.toLowerCase()) ?? null
        const v = valueFor(f.name, row)
        if (v !== undefined) original[f.name] = v
        if (isPassword(col)) continue
        if (v !== undefined) seeded[f.name] = v
      }
    } else {
      // Add mode — seed FK columns from bind values + field defaults.
      for (const f of effFields) {
        const fromBind = bound[f.name] ?? bound[f.name.toUpperCase()]
        if (fromBind !== undefined) seeded[f.name] = fromBind
        else if (f.default != null && f.default !== '') seeded[f.name] = f.default
      }
    }
    setFormValues(seeded)
    setSavedRow(original)
  }, [result, effFields, colByName, bound])

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
    const save = async (parentExtra?: Row) => {
      // Add-mode + the user never touched the form: skip insert. Otherwise we'd create
      // empty rows just because the user opened the parent dialog and ignored this tab.
      const isExisting = isExistingRef.current
      if (!isExisting && !touchedRef.current) return

      // Re-resolve the FK binds against the parent state MERGED with ``parentExtra`` — the values
      // the parent write just produced (its server-assigned SEQUENCE PK on an ADD). In add mode
      // the parent's PK wasn't in ``parentFormValues`` when ``bound`` was memoised, so without
      // this the child FK would bind to NULL; with it, the child ties to the parent's new PK.
      const effBound = parentExtra && Object.keys(parentExtra).length
        ? resolveBindList(tab.param_binds, { ...parentFormValues, ...parentExtra })
        : bound

      // Collect values from visible fields only (a hidden field on save isn't written —
      // same convention the top-level dialog uses). Drop empty password values.
      const sent: Row = {}
      for (const f of effFields) {
        if (!fieldStateOf(f).visible) continue
        if (!(f.name in formValues)) continue
        const v = formValues[f.name]
        const col = colByName.get(f.name.toLowerCase()) ?? null
        if (isPassword(col) && (v == null || v === '')) continue
        sent[f.name] = v
      }
      // Bound FK columns (e.g. APPS_ID) are AUTHORITATIVE: the child's parent-key column must be
      // the parent's PK, never whatever value the nested form's own field happens to show. When the
      // FK is also a visible field, its displayed value is stale on an ADD (the parent's real PK is
      // only assigned at save) — so the bind must WIN, not defer. Drop any case-variant the field
      // collection added (APPS_ID vs apps_id), then set the bound value.
      for (const [k, v] of Object.entries(effBound)) {
        for (const sk of Object.keys(sent)) {
          if (sk !== k && sk.toLowerCase() === k.toLowerCase()) delete sent[sk]
        }
        sent[k] = v
      }

      const targetQuery = isExisting ? effUpdate : effInsert
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
  }, [savers, tab.id, effFields, effUpdate, effInsert, formValues, savedRow, bound, colByName, connector, fieldStateOf, t])

  const cols = Math.max(1, tab.cols ?? 2)
  if (refError) return <Banner $tone="error">{refError}</Banner>
  if (isRef && !refScreen) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  // When the PARENT is being added, its PK is assigned (by sequence) only on its own insert, so
  // the FK binds can't resolve yet — but we still render the form so the operator fills it in the
  // same pass; the FK lands at save time from ``parentExtra``. Only show "pending" when the parent
  // already exists (edit) but the binds genuinely don't resolve.
  if (!hasBinds && parentMode !== 'add') return <Banner $tone="info">{t('dialog.nested.pendingBinds')}</Banner>
  if (loading && !result) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  if (error) return <Banner $tone="error">{error}</Banner>
  // Even in add mode (no linked row) we render the form — the user can type values and
  // saving will fire insert_query (binding the FK columns from `parentExtra` at save time).
  // The Hint banner surfaces the "no record yet" state so it's not invisible.
  return (
    <div>
      {!isExistingRef.current && result && <Hint>{t('dialog.nested.noRecord')}</Hint>}
      <FieldGrid $cols={cols}>
        {effFields
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
        {effFields.filter((f) => fieldStateOf(f).visible).length === 0 && (
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
        // ``keyColumns`` is the missing prop that made nested-table inserts fail with an FK
        // violation: without it, every field on the sub-dialog renders any inherited LOOKUP
        // rule (e.g. settings_jde_tv.TV_APPS_ID's ``dd = "APPS_ID"`` inherits the LOOKUP from
        // the dictionary), and the SearchSelect's async options arriving AFTER the seed lands
        // clobbers the bound parent-PK value (`{ TV_APPS_ID: "10" }`) — the form ended up
        // POSTing whatever the SearchSelect picked as fallback, which was usually not the
        // parent's PK and almost always a FK violation. Passing the nested screen's
        // key_columns lets ScreenDialog short-circuit the LOOKUP on add (via suppressLookup)
        // and render the FK column as a plain disabled Input showing the seeded value, which
        // the form then POSTs unchanged — same pattern v1 used.
        <ScreenDialog
          open
          nested
          mode={subDialog.mode}
          screen={nestedScreen}
          columns={result.columns}
          row={subDialog.row}
          connector={nestedConnector}
          keyColumns={nestedScreen.key_columns}
          onClose={() => setSubDialog(null)}
          onSaved={() => { setSubDialog(null); setRefreshTick((n) => n + 1) }}
        />
      )}
    </TableWrap>
  )
}
