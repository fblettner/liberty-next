// ScreenDialog — the modal form shown when the TableView opens a row for editing or "Add row".
// Built from a Screen's `dialog` (tabs → fields → optional lookup_param_binds). For each
// `ScreenField.name` we look up the matching column on the read result to pick the right widget
// (BOOLEAN / ENUM / LOOKUP / number / date / text) and reuse the dictionary's resolved display
// rule. LOOKUP combos resolve their `lookup_param_binds` at *call time* — a `source` bind reads
// the current form's value of `<source>`, a `value` bind is a literal; both feed the lookup spec's
// `params`, which the lookup service already passes as URL binds and uses as a client-side filter.
//
// Save POSTs to /api/sql/{connector}/{update_query|insert_query} with the row's values (uppercased
// + `:_ORIGINAL` keys for edit, same shape the inline grid editor sends). Cancel discards. Tabs
// flagged `hide_on_add` / `hide_on_edit` drop out in the matching mode. Fields with `hidden=true`
// are skipped; `disabled=true` renders read-only; `required=true` triggers HTML5 validation; a
// `colspan` widens the field across the tab's CSS grid (`cols` wide). When a screen has no dialog
// the TableView falls back to its existing inline grid-edit flow.
import { createContext, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Save, X, Zap } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, ModalBody, ModalFooter, ModalHeader, NestedOverlay, NestedScreenDialogModal, Overlay, Row as FlexRow, ScreenDialogModal, SpinnerRing } from '../../common'
import type { Column } from '../../types/connectors'
import type { Action, FormTab, ScreenDetail, ScreenField, ScreenTab } from '../../types/screens'
import { colors, fontSize, fonts } from '../../theme'
import { evalConditions, originalKeys, resolveBindList, type Row, withUpper } from './dialogHelpers'
import { CellWrap, FieldRow, isPassword } from './FieldRow'
import { NestedFormView, NestedTableView } from './NestedTab'

/** Narrow a ScreenTab to FormTab — the original "grid of fields" kind. A tab without a
 *  ``type`` discriminator is treated as a form (matches `parse_screens` backward compat).
 *  Used to skip nested-tab kinds from the parent's seeding / submit loops — those tabs
 *  manage their own read query + form state internally. */
function isFormTab(tab: ScreenTab): tab is FormTab {
  return tab.type === undefined || tab.type === 'form'
}

export type DialogMode = 'edit' | 'add'

/** A NestedFormView registers a save function with its parent ScreenDialog via this context.
 *  After the parent's own update/insert succeeds, ``submit()`` walks the registry and awaits
 *  each entry sequentially — same as v1's FormsDialog flow (main first, then sub-dialogs,
 *  each contributing their own write). A throwing save aborts the chain and surfaces on the
 *  parent's error banner; the dialog stays open so the operator can retry. */
export type NestedSaver = () => Promise<void>
export interface NestedSaversCtx {
  /** Mount a saver under *tabId*. Idempotent — re-registering replaces the previous fn (which
   *  is exactly what we want when NestedFormView's closure captures a new `formValues`). */
  register: (tabId: string, fn: NestedSaver) => void
  unregister: (tabId: string) => void
}
export const NestedSaversContext = createContext<NestedSaversCtx | null>(null)

const TabStrip = styled.div`
  display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid ${colors.border};
`
const TabBtn = styled.button<{ $active?: boolean }>`
  height: 32px; padding: 0 14px; border: none; border-bottom: 2px solid transparent; background: transparent;
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  border-bottom-color: ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer;
  font-weight: ${({ $active }) => ($active ? 600 : 400)};
  &:hover { color: ${colors.text.primary}; }
`
const FieldGrid = styled.div<{ $cols: number }>`
  display: grid; grid-template-columns: repeat(${({ $cols }) => $cols}, 1fr); gap: 12px;
`

export function ScreenDialog({
  open, mode, screen, columns, row, connector, onClose, onSaved, nested = false,
}: {
  open: boolean
  mode: DialogMode
  /** the full screen definition (dialog + update/insert query refs) */
  screen: ScreenDetail
  /** the read result's columns — used to resolve display rules / widget kinds per field. */
  columns: Column[]
  /** the row being edited (mode='edit'). For 'add' an empty / default-filled row. */
  row: Row
  /** the screen's effective connector — same one the screen's read query lives on. */
  connector: string
  onClose: () => void
  /** Called after a successful save; the TableView reruns its SELECT to refresh the grid. */
  onSaved: () => void
  /** When opened from inside another ScreenDialog (e.g. NestedTableView's row click → edit a
   *  related-rules row), use the smaller auto-height modal variant + bumped Overlay z-index so
   *  the parent's frame remains visible behind it and the sub doesn't dominate the viewport. */
  nested?: boolean
}) {
  const { t } = useTranslation()
  const dlg = screen.dialog
  // Filter tabs by mode (v1's tab_disable_add/_edit → hide_on_add/_edit). An empty list after the
  // filter shouldn't happen in practice but we surface it as a banner instead of crashing.
  const tabs = useMemo<ScreenTab[]>(() => {
    if (!dlg) return []
    return dlg.tabs.filter((tab) => (mode === 'add' ? !tab.hide_on_add : !tab.hide_on_edit))
  }, [dlg, mode])

  const [tabIdx, setTabIdx] = useState(0)
  const [formValues, setFormValues] = useState<Row>({})
  const [savedRow, setSavedRow] = useState<Row>({})  // the *original* values keyed by field name (for `:<FIELD>_ORIGINAL`)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Nested-form save coordination — NestedFormView components mount inside the dialog body
  // (one per ``nested_form`` tab) and register their own save function here. Stored in a ref
  // so re-registers (each time the nested form's closure captures a new ``formValues``) don't
  // re-render the parent; the registry is only walked when the parent's submit() fires.
  const nestedSaversRef = useRef<Map<string, NestedSaver>>(new Map())
  const nestedSaversCtx = useMemo<NestedSaversCtx>(() => ({
    register: (id, fn) => { nestedSaversRef.current.set(id, fn) },
    unregister: (id) => { nestedSaversRef.current.delete(id) },
  }), [])

  // Case-insensitive lookup: the DB result rows have lowercase keys (Postgres folds unquoted
  // identifiers); a ScreenField.name from the migration is uppercase (col_target). Match by
  // lower-casing on both sides so we read the right cell when seeding the form.
  const valueFor = useCallback((field: string, src: Row): unknown => {
    if (field in src) return src[field]
    const lk = field.toLowerCase()
    if (lk in src) return src[lk]
    for (const k of Object.keys(src)) if (k.toLowerCase() === lk) return src[k]
    return undefined
  }, [])

  // Resolve each ScreenField.name to its read-result column (case-insensitive), once per change.
  // Hoisted above the seeding effect so we can skip password fields when seeding.
  const colByName = useMemo(() => {
    const m = new Map<string, Column>()
    for (const c of columns) m.set(c.name.toLowerCase(), c)
    return m
  }, [columns])

  // Re-seed when the dialog (re-)opens or the underlying row changes. The form state is keyed by
  // ScreenField.name (whatever case the screen uses) so on save we can post `{NAME: v}` with the
  // case the migrated update_query expects. `add` mode also applies field defaults. Password
  // fields are NEVER seeded with the stored value — that's a hash / ENC: ciphertext, and putting
  // it in the form means it (a) shows up in the masked input as long-look-alike dots, (b) gets
  // posted back on save, overwriting whatever the user might have set elsewhere. The seeded
  // `savedRow` still carries the original for non-password `:<COL>_ORIGINAL` binds.
  useEffect(() => {
    if (!open || !dlg) return
    const seeded: Row = {}
    const original: Row = {}
    // Only seed the parent's own form tabs — nested-form / nested-table tabs manage their own
    // read query + state inside the NestedFormView / NestedTableView components.
    for (const tab of dlg.tabs.filter(isFormTab)) {
      for (const f of tab.fields ?? []) {
        const col = colByName.get(f.name.toLowerCase()) ?? null
        const v = valueFor(f.name, row)
        if (v !== undefined) original[f.name] = v
        if (isPassword(col)) continue   // seeded blank — user types a new password to change it
        if (v !== undefined) seeded[f.name] = v
        else if (mode === 'add' && f.default != null && f.default !== '') seeded[f.name] = f.default
      }
    }
    setFormValues(seeded)
    setSavedRow(original)
    setTabIdx(0)
    setError(null)
  }, [open, mode, row, dlg, valueFor, colByName])

  const onFieldChange = useCallback((name: string, v: unknown) => {
    setFormValues((p) => ({ ...p, [name]: v }))
  }, [])

  // Run a list of post-save actions sequentially against a *snapshot* of the form state — the
  // dialog has just written its main update_query / insert_query; on_save actions are the v2 form
  // of v1's `ly_act_tasks` for the form-save flow (multi-table writes, audit calls, post-save
  // notifications). Each action's ParamBinds resolve against `ctx`; run_query POSTs to
  // /api/sql/{c}/{q}; notify appends to the local status banner; refresh signals the caller (via
  // the returned flag) to re-run the read query. Unimplemented variants log a console.warn and,
  // when ``stop_on_error`` is false, are skipped — otherwise they abort the chain with a clear
  // error so the operator knows their config references a not-yet-implemented runtime feature.
  // Returns the (possibly empty) list of human-readable warnings to append to the success status.
  const runOnSaveActions = useCallback(async (actions: Action[], ctx: Row): Promise<{ ok: boolean; warnings: string[]; refresh: boolean; error?: string }> => {
    const warnings: string[] = []
    let refresh = false
    for (const a of actions) {
      try {
        switch (a.type) {
          case 'run_query': {
            const target = a.connector || connector
            const bound = resolveBindList(a.param_binds, ctx)
            await api.post(
              `/api/sql/${encodeURIComponent(target)}/${encodeURIComponent(a.query)}`,
              { params: withUpper(bound) },
            )
            break
          }
          case 'notify': {
            warnings.push(a.message)
            break
          }
          case 'refresh': {
            refresh = true
            break
          }
          case 'call_api':
          case 'navigate':
          case 'set_field':
          case 'confirm': {
            // Stubbed — model is in place but the runtime wires up in a later slice. The
            // builder lets you create them already; an unsupported runtime is a console.warn
            // rather than a hard fail unless `stop_on_error = true` (the default).
            const msg = `on_save action '${a.id}' (${a.type}) — runtime not implemented yet`
            console.warn(msg)  // eslint-disable-line no-console
            if (a.stop_on_error !== false) return { ok: false, warnings, refresh, error: msg }
            warnings.push(msg)
            break
          }
        }
      } catch (e) {
        const msg = `${a.label || a.id}: ${e instanceof ApiError ? e.message : String(e)}`
        if (a.stop_on_error !== false) return { ok: false, warnings, refresh, error: msg }
        warnings.push(msg)
      }
    }
    return { ok: true, warnings, refresh }
  }, [connector])

  // Screen-level actions surfaced inside the dialog footer too — v1's NOMAJDE pattern (e.g.
  // role-management dialog carried "Import Security" / "Merge Roles" buttons inside its second
  // tab). The same ``Screen.actions`` list as the TableView toolbar, but fired with the *live
  // form state* as context: a ``source`` ParamBind reads the current values, so "Merge Roles"
  // with ``source = ROL_ID`` picks up the role being edited. Reuses ``runOnSaveActions`` (a
  // single-action chain works fine) — the runner already handles run_query / notify / refresh /
  // navigate, stubs the rest.
  const screenActions = useMemo<Action[]>(() => (screen.actions ?? []) as Action[], [screen])
  const [actionBusy, setActionBusy] = useState<string | null>(null)
  const [actionStatus, setActionStatus] = useState<{ message: string; tone: 'ok' | 'error' } | null>(null)
  const fireScreenAction = useCallback(async (a: Action) => {
    setActionBusy(a.id); setActionStatus(null)
    // Form state takes precedence over the original row's values — the user may have edited a
    // bind-source field before clicking the action. ``savedRow`` provides untouched columns
    // (e.g. the PK on a not-yet-modified record).
    const ctx: Row = { ...savedRow, ...formValues }
    const result = await runOnSaveActions([a], ctx)
    setActionBusy(null)
    if (!result.ok) {
      setActionStatus({ message: result.error || a.label || a.id, tone: 'error' })
    } else {
      // notify-type actions emit their own message via warnings; non-notify successes get a
      // generic "<label> · OK" so the operator sees feedback.
      const msg = result.warnings.length > 0 ? result.warnings.join(' · ') : `${a.label || a.id} · ${t('http.ok')}`
      setActionStatus({ message: msg, tone: 'ok' })
    }
    if (result.refresh) onSaved()
  }, [formValues, savedRow, runOnSaveActions, onSaved, t])

  const submit = useCallback(async () => {
    const targetQuery = mode === 'edit' ? screen.update_query : screen.insert_query
    if (!targetQuery) {
      setError(t(mode === 'edit' ? 'table.editNoUpdate' : 'table.editNoInsert'))
      return
    }
    // Collect every value that's on a *currently visible* field on a non-hidden tab. Fields
    // hidden by `visible_when` (or `hidden = true`) are dropped from the body so a now-irrelevant
    // column keeps its current DB value (same behaviour as v1 — a hidden field on save isn't
    // written). Password fields with empty values are also dropped so we don't overwrite the
    // stored hash / ENC: ciphertext with NULL or "".
    const sent: Row = {}
    // The parent's save only collects its own form tabs' fields. Nested-form tabs write through
    // their own update/insert queries (slice 2 — coordinated via a register-on-parent-save hook).
    for (const tab of tabs.filter(isFormTab)) {
      for (const f of tab.fields ?? []) {
        if (!fieldStateOf(f).visible) continue
        if (!(f.name in formValues)) continue
        const v = formValues[f.name]
        const col = colByName.get(f.name.toLowerCase()) ?? null
        if (isPassword(col) && (v == null || v === '')) continue
        sent[f.name] = v
      }
    }
    setSaving(true); setError(null)
    try {
      // For password fields we never had the original ciphertext in the form (seeded blank), so
      // dropping them from `savedRow` keeps the update-query's SET clause from binding `:PASSWORD`
      // to undefined — text() omits unmentioned params, but a key with the wrong value would still
      // overwrite. The migrated _put queries reference :PASSWORD only in their SET clause; not
      // sending it means the column keeps its current DB value. (When the user *did* type a new
      // password, `sent` carries it and overrides.)
      const baseSaved: Row = mode === 'edit'
        ? Object.fromEntries(Object.entries(savedRow).filter(([k]) => {
            const c = colByName.get(k.toLowerCase()) ?? null
            return !isPassword(c)
          }))
        : {}
      const params = mode === 'edit'
        ? { ...baseSaved, ...sent, ...originalKeys(baseSaved) }
        : sent
      await api.post(
        `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(targetQuery)}`,
        { params: withUpper(params) },
      )
      // Nested-form saves — v1's FormsDialog inside FormsDialog flow. Walk the registry of
      // NestedFormView savers in registration order (Map iteration is insertion order), each
      // contributing its own ``update_query`` / ``insert_query`` against its own connector.
      // A throwing saver aborts the chain and surfaces on the parent's banner; the main row
      // is already written so the user can retry the nested save without re-doing the parent.
      for (const [tabId, save] of nestedSaversRef.current) {
        try {
          await save()
        } catch (e) {
          setSaving(false)
          setError(t('dialog.nestedSaveFailed', {
            tab: tabId,
            message: e instanceof ApiError ? e.message : String(e),
          }))
          onSaved()  // main row was written — refresh so the user sees the new primary state
          return
        }
      }
      // Run on_save actions against a snapshot of the full form (sent values + savedRow merged so
      // ParamBind `source` can reach untouched columns too, e.g. the PK on the main row).
      const ctx: Row = { ...savedRow, ...sent }
      const actions = (screen.dialog?.on_save ?? []) as Action[]
      const result = actions.length > 0
        ? await runOnSaveActions(actions, ctx)
        : { ok: true, warnings: [], refresh: false }
      setSaving(false)
      if (!result.ok) {
        // The *main* save succeeded; only the action chain failed. Tell the user clearly that the
        // primary row is written but some follow-up did not — they decide whether to retry, edit,
        // or close.
        setError(t('dialog.onSaveFailed', { message: result.error || '' }))
        onSaved()  // refresh anyway so the user sees the new primary state
        return
      }
      // success — surface any non-fatal warnings (notify messages, skipped stubs with
      // stop_on_error=false) but proceed to close + refresh.
      if (result.warnings.length > 0) {
        // eslint-disable-next-line no-console
        console.info('on_save warnings:', result.warnings)
      }
      onSaved()
      onClose()
      void result.refresh  // refresh is implied by onSaved() — the caller (TableView) re-runs the read.
    } catch (e) {
      setSaving(false)
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }, [mode, screen, tabs, formValues, savedRow, connector, onClose, onSaved, t, colByName, runOnSaveActions])

  // Resolve a field's effective hidden / required / disabled per render — `*_when` lists, when
  // non-empty, override the static flags. The eval runs against the live `formValues`, so as the
  // user types in field A, dependent fields B/C re-render with new visibility / requirement.
  const fieldStateOf = (f: ScreenField) => {
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
  }

  if (!open || !dlg) return null
  const currentTab = tabs[Math.min(tabIdx, tabs.length - 1)] ?? null
  const title = dlg.title || screen.label || screen.id
  // The parent's grid `cols` only applies when the current tab is a form-shaped tab; nested
  // form tabs carry their own `cols`, nested tables ignore it. Default 2 columns.
  const isForm = currentTab ? isFormTab(currentTab) : false
  const gridCols = isForm && currentTab && (currentTab as FormTab).cols ? (currentTab as FormTab).cols! : 2
  // Drop fields that don't pass their visibility rule. Their values stay in form state so a
  // condition flipping back later restores them — but on submit, the same eval drops them again.
  const visibleFields = isForm && currentTab
    ? ((currentTab as FormTab).fields ?? []).filter((f) => fieldStateOf(f).visible)
    : []

  // Pick the modal frame: top-level dialogs get the fixed-height ScreenDialogModal (so the
  // Save button doesn't move as tabs swap content); a nested sub-dialog gets the smaller
  // auto-height variant on a bumped-z-index Overlay so the parent stays visible behind it.
  const OverlayEl = nested ? NestedOverlay : Overlay
  const ModalEl = nested ? NestedScreenDialogModal : ScreenDialogModal
  return (
    <NestedSaversContext.Provider value={nestedSaversCtx}>
    <OverlayEl onClick={onClose}>
      <ModalEl onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          {mode === 'edit' ? t('dialog.editTitle', { title }) : t('dialog.addTitle', { title })}
        </ModalHeader>
        <ModalBody>
          {tabs.length === 0 ? (
            <Banner $tone="info">{t('dialog.noVisibleTabs')}</Banner>
          ) : (
            <>
              {tabs.length > 1 && (
                <TabStrip role="tablist">
                  {tabs.map((tab, i) => (
                    <TabBtn key={tab.id} type="button" role="tab" aria-selected={i === tabIdx} $active={i === tabIdx} onClick={() => setTabIdx(i)}>
                      {tab.label || tab.id}
                    </TabBtn>
                  ))}
                </TabStrip>
              )}
              {/* Render every tab, hide inactive ones via CSS — nested-form / nested-table tabs
                  need to stay MOUNTED across tab switches so their formValues / fetch state
                  persists (otherwise a user editing JD Edwards, switching to Connexion, and
                  coming back would lose their edits). FormTab content reads from the parent's
                  shared `formValues` so it can render only the active tab safely. */}
              {tabs.map((tab, i) => {
                const active = i === tabIdx
                if (tab.type === 'nested_form') {
                  return (
                    <div key={tab.id} style={{ display: active ? 'block' : 'none' }}>
                      <NestedFormView tab={tab} parentFormValues={formValues} parentConnector={connector} />
                    </div>
                  )
                }
                if (tab.type === 'nested_table') {
                  return (
                    <div key={tab.id} style={{ display: active ? 'block' : 'none' }}>
                      <NestedTableView tab={tab} parentFormValues={formValues} parentConnector={connector} app={screen.app} />
                    </div>
                  )
                }
                // FormTab — lazy render: only mount when active. The state (formValues) lives
                // on the parent, so switching tabs keeps the values intact. Filters its fields
                // through `fieldStateOf` (visible_when / required_when / disabled_when).
                if (!active) return null
                return (
                  <FieldGrid key={tab.id} $cols={gridCols}>
                    {visibleFields.map((f) => {
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
                    {visibleFields.length === 0 && (
                      <CellWrap $span={gridCols}>
                        <Banner $tone="info">{t('dialog.noVisibleFields')}</Banner>
                      </CellWrap>
                    )}
                  </FieldGrid>
                )
              })}
            </>
          )}
          {error && <Banner $tone="error">{error}</Banner>}
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
        </ModalBody>
        <ModalFooter>
          {screenActions.length > 0 && (
            <FlexRow gap={6} style={{ marginRight: 'auto', flexWrap: 'wrap' }}>
              {/* v1's pattern of in-dialog action buttons — fires the screen's actions with the
                  live form state as context. ParamBinds (``source``) read formValues so e.g.
                  "Merge Roles" on the role-management dialog picks up the role being edited. */}
              {screenActions.map((a) => (
                <Button
                  key={a.id}
                  $size="sm"
                  $variant="ghost"
                  onClick={() => { void fireScreenAction(a) }}
                  disabled={saving || actionBusy != null}
                  title={a.id}
                >
                  {actionBusy === a.id ? <SpinnerRing size={13} thickness={2} /> : <Zap size={13} />} {a.label || a.id}
                </Button>
              ))}
            </FlexRow>
          )}
          <FlexRow gap={8}>
            <Button $size="sm" $variant="ghost" onClick={onClose} disabled={saving}>
              <X size={13} /> {t('common.cancel')}
            </Button>
            <Button $size="sm" $variant="primary" onClick={submit} disabled={saving || tabs.length === 0}>
              {saving ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
            </Button>
          </FlexRow>
        </ModalFooter>
      </ModalEl>
    </OverlayEl>
    </NestedSaversContext.Provider>
  )
}
