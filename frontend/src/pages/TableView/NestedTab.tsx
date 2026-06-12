// Nested-tab content embedded directly inside a parent ScreenDialog tab. Two flavours (matching
// the discriminated-union types in `types/screens.ts`), both REFERENCE-ONLY — each points at an
// existing screen by id and only wires param_binds from the parent; neither configures its own
// fields/queries (that dual-config layer was removed):
//
//   • NestedFormView — reuse another screen's FORM inline. Fetches the referenced screen, renders
//     ITS fields (resolved from ITS columns), narrowed by param_binds, and saves the child through
//     the referenced screen's own update/insert queries on the parent's Save (via NestedSaversContext).
//
//   • NestedTableView — v2's port of v1's "FormsTable inside a FormsDialog". References another v2
//     Screen by id (e.g. parent SETTINGS_APPLICATIONS' "Activity Log" tab → standalone
//     settings_activity_log screen), fetches its rows narrowed by the parent PK, and renders the
//     full ResultTable grid (edit / row-click dialog / filters / export).
//
// Both wait for their bound params to resolve before fetching — a not-yet-saved "add" parent
// (APPS_ID still undefined) shows an inert empty state instead of firing a request that would
// return everything in the table.
import { useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, SpinnerRing } from '../../common'
import type { Column, QueryResult } from '../../types/connectors'
import type { NestedFormTab, NestedTableTab, ScreenDetail, ScreenField } from '../../types/screens'
import { evalConditions, originalKeys, resolveBindList, type Row, valueFor, withUpper } from './dialogHelpers'
import { CellWrap, FieldRow, isPassword } from './FieldRow'
import { colors, fontSize } from '../../theme'
// Circular import: ResultTable → ScreenDialog → (this module's) NestedFormView/NestedTableView,
// and NestedTableView → ResultTable. ESM resolves the bindings at module-eval; they're only *used*
// at render time (inside JSX), so the cycle resolves fully before React renders — the same lazy-
// binding pattern the ScreenDialog ↔ nested-tab cycle already relied on.
import { ResultTable } from './ResultTable'
import { NestedSaversContext, type DialogMode } from './ScreenDialog'

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
 *  trigger a giant SELECT).
 *
 *  A nested bind is a foreign-KEY relationship (parent column → child param), so it must match
 *  EXACTLY. We send a ``<param>_op=equals`` companion for each: without it a text/CHAR column
 *  filter defaults to ``contains`` (LIKE ``%value%``), so binding ``RLTOROLE=DEMO`` would wrongly
 *  pull DEMO **and** DEMO2 into the child table. */
function bindsToQuery(bound: Record<string, string>): string {
  const parts = Object.entries(bound).flatMap(([k, v]) => [
    `${encodeURIComponent(k)}=${encodeURIComponent(v)}`,
    `${encodeURIComponent(`${k}_op`)}=equals`,
  ])
  return parts.length ? `?${parts.join('&')}` : ''
}

/** Resolve `tab.connector` against the parent screen's effective connector (the dialog's
 *  `connector` prop). Matches the migration / runtime convention: blank → parent's. */
function effectiveConnector(tab: { connector?: string | null }, parentConnector: string): string {
  return (tab.connector && tab.connector.trim()) || parentConnector
}

// ── NestedFormView ──────────────────────────────────────────────────────────
// REFERENCE-ONLY: reuse another screen's form inline. Fetches the referenced screen, renders ITS
// fields (server-resolved from ITS columns), loads the linked row via the referenced screen's
// read_query narrowed by this tab's `param_binds` (resolved against parent form state), and lets the
// operator edit in place. The save runs **after the parent's main update** via NestedSaversContext —
// register a save fn on mount; the parent's submit() walks the registry and awaits each. A failing
// nested save surfaces on the parent's banner without rolling back the main row.

export function NestedFormView({
  tab, parentFormValues, parentConnector, app, parentMode, parentDuplicating = false, parentReadOnly = false,
}: {
  tab: NestedFormTab
  /** Live parent form state — `source` binds read from here at fetch time. */
  parentFormValues: Row
  /** The parent screen's effective connector — used when `tab.connector` (and the reused screen's) is blank. */
  parentConnector: string
  /** The parent screen's app — used to fetch the reused screen (GET /api/screens/{app}/{form_screen}). */
  app: string
  /** The PARENT dialog's mode. In ``add`` the parent has no PK yet (sequence-assigned on its own
   *  insert), so the FK binds can't resolve — but we still render the form so the operator can fill
   *  it; the FK is supplied at save time via ``parentExtra``. */
  parentMode: DialogMode
  /** The parent is being DUPLICATED: keep the loaded child values but write them as a NEW child tied
   *  to the parent's new PK (insert, not update; saver fires even without a retype). */
  parentDuplicating?: boolean
  /** The parent screen / dialog is read-only — render every field disabled and skip the saver so
   *  this nested form can't write (matches the dialog hiding its own Save). */
  parentReadOnly?: boolean
}) {
  const { t } = useTranslation()
  const savers = useContext(NestedSaversContext)

  // Fetch the reused screen's detail — its read/update/insert queries + fields drive this form.
  const [refScreen, setRefScreen] = useState<ScreenDetail | null>(null)
  const [refError, setRefError] = useState<string | null>(null)
  useEffect(() => {
    if (!tab.form_screen) { setRefScreen(null); return }
    setRefError(null)
    let cancelled = false
    api.get<ScreenDetail>(`/api/screens/${encodeURIComponent(app)}/${encodeURIComponent(tab.form_screen)}`)
      .then((s) => { if (!cancelled) setRefScreen(s) })
      .catch((e) => { if (!cancelled) setRefError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [app, tab.form_screen])

  // Effective fields from the reused screen, in priority order:
  //   1. its dialog form-tab fields, flattened in display order (server-resolved with rule / label /
  //      format — a reused screen's grouping into sub-tabs collapses into one inline grid here), else
  //   2. its ``columns`` mapped to fields — covers screens that drive their dialog from columns.
  const effFields = useMemo<ScreenField[]>(() => {
    if (!refScreen) return []
    const fromDialog: ScreenField[] = []
    for (const tb of refScreen.dialog?.tabs ?? []) {
      if (Array.isArray((tb as { fields?: unknown }).fields)) fromDialog.push(...((tb as { fields: ScreenField[] }).fields))
    }
    if (fromDialog.length) return fromDialog
    return (refScreen.columns ?? []).map((c) => ({
      name: c.name, label: c.label, format: c.format, hidden: c.hidden, rule: c.rule,
    })) as ScreenField[]
  }, [refScreen])
  // Grid width — use the reused screen's own form-tab ``cols`` (the first form tab that carries
  // fields), so the inline form lays out exactly as the standalone screen does. Falls back to 2
  // when the reused screen drives its dialog from columns (no tab cols) or sets none.
  const cols = useMemo(() => {
    for (const tb of refScreen?.dialog?.tabs ?? []) {
      const t = tb as { fields?: unknown; cols?: number | null }
      if (Array.isArray(t.fields) && t.fields.length && t.cols) return Math.max(1, t.cols)
    }
    return 2
  }, [refScreen])
  const connector = refScreen?.connector || effectiveConnector(tab, parentConnector)
  const effRead = refScreen?.read_query ?? ''
  const effUpdate = refScreen?.update_query ?? null
  const effInsert = refScreen?.insert_query ?? null

  // Resolve bound params against the live parent state. JSON-stringified key drives the
  // fetch effect — re-running the read query when the parent's PK / context changes.
  const bound = useMemo(() => resolveBindList(tab.param_binds, parentFormValues), [tab.param_binds, parentFormValues])
  const boundKey = useMemo(() => JSON.stringify(bound), [bound])
  // Skip the fetch when no binds resolved (a not-yet-saved parent in "add" mode) or until the reused
  // screen's read_query has loaded (firing a query with an empty name would 404).
  const hasBinds = Object.keys(bound).length > 0 && !!effRead

  const [result, setResult] = useState<QueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Local form state — separate from the parent's so editing here doesn't pollute the main `formValues`.
  const [formValues, setFormValues] = useState<Row>({})
  const [savedRow, setSavedRow] = useState<Row>({})  // pre-edit values keyed by field name (for `:_ORIGINAL` binds)
  const isExistingRef = useRef(false)   // true once a linked row loaded → UPDATE vs INSERT on save
  const touchedRef = useRef(false)      // only insert when add-mode AND the user actually typed something

  useEffect(() => {
    if (parentDuplicating) { isExistingRef.current = false; touchedRef.current = true }
  }, [parentDuplicating])

  useEffect(() => {
    // NEVER read in parent-add mode: the parent row doesn't exist yet, so it has no linked child.
    // A stale/seeded parent PK would otherwise load THAT parent's child and, on Save, UPDATE it onto
    // the new parent. In add mode we always start empty and INSERT, bound to the parent's freshly-
    // assigned PK (via parentExtra) at save time.
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

  // Seed local form state from the loaded row (edit) or from bind values + field defaults (add).
  // Password fields are NEVER seeded (the stored value is a hash / ENC: blob). `savedRow` keeps the
  // original (sans-password) for `:_ORIGINAL` rebinds.
  useEffect(() => {
    if (!result) return
    // Pick the row that MATCHES the bind — never blindly rows[0]. A migrated read query may lack the
    // WHERE clause and return every row; trusting rows[0] would then show/update a DIFFERENT parent's
    // child. Fall back to rows[0] only when there are no binds.
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
      // ``savedRow`` carries the ORIGINAL of EVERY loaded column (not just visible fields) so the
      // migrated _put's WHERE ``:<KEY>_ORIGINAL`` binds resolve even for non-field key columns.
      for (const [k, v] of Object.entries(row)) {
        if (isPassword(colByName.get(k.toLowerCase()) ?? null)) continue
        original[k] = v
      }
      for (const f of effFields) {
        const col = colByName.get(f.name.toLowerCase()) ?? null
        const v = valueFor(f.name, row)
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

  // Visibility / required / disabled (same rule engine as the top-level dialog) — re-eval every
  // render against the current formValues so a field gating on a sibling reacts live.
  const fieldStateOf = useCallback((f: ScreenField) => {
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

  // Register the save fn with the parent ScreenDialog. Re-registers each render so the closure
  // captures the *latest* formValues / mode — the registry is a `Map`, so a re-register overwrites
  // the previous entry. Unregister on unmount.
  useEffect(() => {
    if (!savers || parentReadOnly) return   // read-only parent: nothing here ever writes
    const save = async (parentExtra?: Row) => {
      // When the parent is DUPLICATED, ALWAYS insert a new child — never update (forced here so a
      // stale/in-flight read can't flip ``isExistingRef`` back to true and UPDATE the original).
      const isExisting = parentDuplicating ? false : isExistingRef.current
      // Add-mode + never touched: skip insert (don't create empty rows just because the tab opened).
      if (!isExisting && !touchedRef.current && !parentDuplicating) return

      // Re-resolve the FK binds against the parent state MERGED with ``parentExtra`` — the parent's
      // server-assigned SEQUENCE PK on an ADD. Without it the child FK would bind to NULL in add mode.
      const effBound = parentExtra && Object.keys(parentExtra).length
        ? resolveBindList(tab.param_binds, { ...parentFormValues, ...parentExtra })
        : bound

      // Collect values from visible fields only (a hidden field on save isn't written). Drop empty passwords.
      const sent: Row = {}
      for (const f of effFields) {
        if (!fieldStateOf(f).visible) continue
        if (!(f.name in formValues)) continue
        const v = formValues[f.name]
        const col = colByName.get(f.name.toLowerCase()) ?? null
        if (isPassword(col) && (v == null || v === '')) continue
        sent[f.name] = v
      }
      // Bound FK columns are AUTHORITATIVE: the child's parent-key column must be the parent's PK,
      // never the form's stale display value. Drop any case-variant the field collection added, then
      // set the bound value.
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
      // For an update, also bind `:<COL>_ORIGINAL` for the migrated _put's WHERE rebind (skip password
      // originals — we don't have the ciphertext on hand).
      const baseSaved: Row = isExisting
        ? Object.fromEntries(Object.entries(savedRow).filter(([k]) => !isPassword(colByName.get(k.toLowerCase()) ?? null)))
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
  }, [savers, parentReadOnly, tab.id, tab.param_binds, effFields, effUpdate, effInsert, formValues, savedRow, bound, colByName, connector, fieldStateOf, t, parentDuplicating, parentFormValues])

  if (refError) return <Banner $tone="error">{refError}</Banner>
  if (!refScreen) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  // When the PARENT is being added (or duplicated) the FK binds can't resolve yet — but we still
  // render the form so the operator fills it in the same pass; the FK lands at save time from
  // ``parentExtra``. Only show "pending" on a genuine edit whose binds don't resolve.
  if (!hasBinds && parentMode !== 'add' && !parentDuplicating) return <Banner $tone="info">{t('dialog.nested.pendingBinds')}</Banner>
  if (loading && !result) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  if (error) return <Banner $tone="error">{error}</Banner>
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
                disabled={st.disabled || parentReadOnly}
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
  tab, parentFormValues, parentConnector, app, parentReadOnly = false,
}: {
  tab: NestedTableTab
  parentFormValues: Row
  parentConnector: string
  /** The parent screen's app — used to look up the nested screen's detail catalog entry
   *  (GET /api/screens/{app}/{id}). The nested screen lives under the same app since
   *  screens.toml's [screens.<app>.<id>] keys by app. */
  app: string
  /** The parent screen / dialog is read-only — force the embedded grid view-only too (no Add /
   *  Delete / inline edit), even if the nested screen isn't itself flagged ``read_only``. */
  parentReadOnly?: boolean
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
  // Bumped after the embedded grid saves a row → refetch the narrowed result.
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
    // ``_screen`` / ``_app`` pin THIS screen's hints — several screens can share one read_query
    // (a copy with different hidden columns), and without this the backend would apply the first
    // matching screen's hints (the original), so the copy's hide/show wouldn't take effect.
    const qs = bindsToQuery(bound)
    const pin = `_screen=${encodeURIComponent(tab.screen)}&_app=${encodeURIComponent(app)}`
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(q)}${qs ? `${qs}&` : '?'}${pin}`)
      .then((r) => setResult(r))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boundKey serializes bound
  }, [nestedScreen, connector, boundKey, hasBinds, refreshTick])

  const nestedConnector = nestedScreen?.connector || connector
  // A read-only parent forces the embedded grid view-only — flag the nested screen so ResultTable's
  // own ``read_only`` gates (add / delete / inline edit) all turn off.
  const effNestedScreen = parentReadOnly && nestedScreen ? { ...nestedScreen, read_only: true } : nestedScreen

  if (!hasBinds) return <Banner $tone="info">{t('dialog.nested.pendingBinds')}</Banner>
  if (error) return <Banner $tone="error">{error}</Banner>
  if (loading && !result) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  if (!result || !nestedScreen) return null

  // Reuse the FULL grid (ResultTable) — one source of truth for columns/cells (LOOKUP id+label,
  // BOOLEAN dots, ENUM labels), edit, row-click dialog, filters/group/columns/export. The only
  // nested-specific bit is the parent FK: ``addSeed`` pre-fills it on Add, and a save refetches the
  // narrowed result. ``key`` resets the grid's internal state when the target screen changes.
  return (
    <TableWrap>
      <ResultTable
        key={`nested-${app}-${tab.screen}`}
        result={result}
        connector={nestedConnector}
        query={nestedScreen.read_query}
        screen={effNestedScreen}
        updateQuery={nestedScreen.update_query}
        insertQuery={nestedScreen.insert_query}
        deleteQuery={nestedScreen.delete_query}
        keyColumns={nestedScreen.key_columns}
        addSeed={bound}
        nestedDialog
        onSaved={() => setRefreshTick((n) => n + 1)}
      />
    </TableWrap>
  )
}
