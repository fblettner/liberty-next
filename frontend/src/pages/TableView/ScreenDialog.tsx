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
import { Copy, Lock as LockIcon, Maximize2, Minimize2, Save, Trash2, X, Zap } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, ConfirmModal, Modal, ModalBody, ModalFooter, ModalHeader, NestedOverlay, NestedScreenDialogModal, Overlay, Row as FlexRow, ScreenDialogModal, SpinnerRing } from '../../common'
import { useSio, useLockState } from '../../sio/SioContext'
import type { LockPayload } from '../../sio/types'
import type { Column } from '../../types/connectors'
import type { Action, FormTab, PromptField, ScreenDetail, ScreenField, ScreenTab } from '../../types/screens'
import { colors, fontSize, fonts } from '../../theme'
import { evalConditions, originalKeys, type Row, withUpper } from './dialogHelpers'
import { ActionPromptDialog } from './ActionPromptDialog'
import { CellWrap, FieldRow, isPassword } from './FieldRow'
import { NestedFormView, NestedTableView } from './NestedTab'
import { resolveAutoFill, runChain } from './actionRunner'

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
 *  parent's error banner; the dialog stays open so the operator can retry.
 *
 *  ``parentExtra`` carries values the parent write just produced that aren't in the parent's
 *  form state yet — specifically the server-assigned SEQUENCE/auto-ID PK from an ADD (returned
 *  on the insert as ``resolved_binds``). The nested saver resolves its ``source`` binds against
 *  the parent form state MERGED with these, so a child insert can tie to the parent's brand-new
 *  PK in the same Save instead of requiring the operator to save the parent first. */
export type NestedSaver = (parentExtra?: Record<string, unknown>) => Promise<void>
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
// A labelled section for an embedded nested form rendered below a form tab's own fields — so one
// tab edits the main table plus related tables. The rule + heading separate it from the main grid.
const NestedSection = styled.div`
  margin-top: 18px; padding-top: 14px; border-top: 1px solid ${colors.border};
`
const NestedSectionTitle = styled.div`
  font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};
  text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 10px;
`

export function ScreenDialog({
  open, mode, screen, columns, row, connector, keyColumns, onClose, onSaved, nested = false,
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
  /** Result columns that identify a row (v1's ``col_key`` / ``QueryDef.key_columns``). In
   *  ``mode='add'`` the dialog auto-suppresses the LOOKUP widget on these fields — the user is
   *  *creating* a new key value, not picking an existing one (v1 hand-set ``col_rules =
   *  "DISABLED"`` on these columns; v2 derives it from the existing ``key_columns`` metadata
   *  so no operator config is needed). On ``edit`` mode the LOOKUP renders as usual. */
  keyColumns?: string[]
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

  // Lowercase set of key-column names — used on ``add`` to suppress the LOOKUP dropdown for
  // a key column (the user is creating a new PK value, not picking an existing one — same
  // intent as v1's ``col_rules = "DISABLED"`` for PKs, but auto-derived from ``key_columns``
  // so no per-field operator config is needed). Recomputed when the metadata changes.
  const keyColumnSet = useMemo(() => new Set((keyColumns ?? []).map((k) => k.toLowerCase())), [keyColumns])

  const [tabIdx, setTabIdx] = useState(0)
  const [formValues, setFormValues] = useState<Row>({})
  const [savedRow, setSavedRow] = useState<Row>({})  // the *original* values keyed by field name (for `:<FIELD>_ORIGINAL`)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Fullscreen / restore toggle for the top-level dialog — the default 900×700 envelope can be
  // tight on a many-tab / many-field screen (NOMASX1 settings_applications has 31 fields on the
  // Default tab alone). Operator clicks the maximize icon in the header, the modal grows to
  // 100vw / 100vh. Doesn't apply when ``nested`` (those are intentionally smaller so the parent
  // remains visible behind). Resets to windowed on each open.
  const [fullscreen, setFullscreen] = useState(false)
  useEffect(() => { if (!open) setFullscreen(false) }, [open])

  // Duplicate: in EDIT mode, "Duplicate" turns the current record into a NEW one — the form keeps
  // every value the operator sees, but the dialog now behaves as ADD (Save → insert_query, a fresh
  // PK is assigned). ``duplicating`` overrides the parent's ``mode`` prop for that purpose; it's
  // reset whenever the dialog (re-)opens or the underlying row changes so reopening an existing row
  // is a normal edit again.
  const [duplicating, setDuplicating] = useState(false)
  useEffect(() => { setDuplicating(false) }, [open, row])
  const effMode: DialogMode = duplicating ? 'add' : mode

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
        else if (mode === 'add' && col?.rule?.kind === 'auto_fill') {
          // v1's `dd_rules` SYSDATE / CURRENT_DATE / LOGIN — auto-fill the field on add. The
          // operator can still type over the seed. Explicit `f.default` above wins (the operator
          // set it on this dialog specifically, so they meant it). Edit mode keeps the row's
          // existing value, even when the column is auto_fill-ruled — we don't want to overwrite
          // the original SYSDATE / LOGIN that was captured at insert time.
          const filled = resolveAutoFill(col.rule.source)
          if (filled != null) seeded[f.name] = filled
        }
      }
    }
    setFormValues(seeded)
    setSavedRow(original)
    setTabIdx(0)
    setError(null)
  }, [open, mode, row, dlg, valueFor, colByName])

  // ── record lock (Phase 9 — Socket.IO) ────────────────────────────────────────────────
  // On open in ``edit`` mode we acquire a row-level lock so a concurrent edit by another
  // user opens the dialog read-only with a "Locked by Alice (Xm ago)" banner. ``add`` mode
  // skips locking — the row doesn't exist yet, there's no composite key to lock.
  //
  // **Why we destructure the callbacks instead of depending on the whole ``sio`` object**:
  // ``useSio()`` returns a memoised value whose identity rebuilds every time ``locks`` or
  // ``status`` changes. Putting that value in the effect's deps caused a tight loop on every
  // broadcast — acquire → broadcast → ``locks`` updates → ``sio`` identity changes → effect
  // cleanup releases → effect re-runs and acquires again. ``acquireLock`` / ``releaseLock``
  // are wrapped in ``useCallback([])`` on the provider so their identities are stable; depend
  // on those refs and the loop is gone.
  const { acquireLock, releaseLock } = useSio()
  const lockPayload = useMemo<LockPayload | null>(() => {
    if (mode !== 'edit') {
      console.info("[lock] skip — mode is", mode)
      return null
    }
    if (!keyColumns || keyColumns.length === 0) {
      console.info("[lock] skip — keyColumns is",
        keyColumns, "for screen", `${screen.app}.${screen.id}`)
      return null
    }
    const kv: Record<string, string> = {}
    for (const k of keyColumns) {
      const v = valueFor(k, row)
      if (v == null || v === '') {
        console.info("[lock] skip — key column", k, "is null/empty in row.",
          { keyColumns, rowKeys: Object.keys(row), screen: `${screen.app}.${screen.id}` })
        return null
      }
      kv[k] = String(v)
    }
    return { app: screen.app, screen: screen.id, key_values: kv }
  }, [mode, keyColumns, row, valueFor, screen.app, screen.id])

  // Watch the live lock map for this payload. ``locks`` updates as the server's
  // ``lock.acquired`` / ``lock.released`` broadcasts arrive; when Alice releases her lock,
  // ``foreignLock`` clears and the form becomes editable — no reopen needed.
  const { lock: heldLock, ownedByMe } = useLockState(lockPayload)
  const foreignLock = heldLock && !ownedByMe ? heldLock : null
  const readOnly = foreignLock != null

  // Acquire on open / release on close. ``cancelled`` covers a fast close while the ack
  // is still in flight — releases the lock if we happened to win it after unmount.
  //
  // Diagnostic console logs (Phase 9) so a "no lock visible in dashboard" report can be
  // diagnosed without re-instrumenting the build. Open DevTools → Console and look for the
  // ``[lock]`` entries: ``no payload`` (keyColumns missing / row empty), ``acquire request``
  // (effect fired the emit), ``result`` (server ack or denial), ``release on cleanup``
  // (effect cleanup, expected on dialog close).
  useEffect(() => {
    if (!open || lockPayload == null) return
    let cancelled = false
    console.info("[lock] acquire request", lockPayload)
    void (async () => {
      const result = await acquireLock(lockPayload)
      console.info("[lock] result", result)
      if (cancelled && result.ok) releaseLock(lockPayload)
    })()
    return () => {
      cancelled = true
      console.info("[lock] release on cleanup", lockPayload)
      releaseLock(lockPayload)
    }
    // ``mode`` is used only for the diagnostic log; the actual reactive trigger is
    // ``lockPayload`` (which itself depends on mode). Listed in deps for the lint rule.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, lockPayload, acquireLock, releaseLock])

  // Human-friendly "X minutes ago" for the banner. Recomputed every render — the WS broadcasts
  // already drive re-renders frequently enough that no setInterval is needed.
  const formatLockTime = (ts: number): string => {
    const diff = Date.now() / 1000 - ts
    if (diff < 60) return t('dialog.lockedSecondsAgo', { count: Math.max(1, Math.floor(diff)), defaultValue: '{{count}}s ago' })
    if (diff < 3600) return t('dialog.lockedMinutesAgo', { count: Math.floor(diff / 60), defaultValue: '{{count}}m ago' })
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  const onFieldChange = useCallback((name: string, v: unknown) => {
    setFormValues((p) => ({ ...p, [name]: v }))
  }, [])

  // dd_id → field_name map across every FormTab. Drives the lookup-return-params write-back:
  // when a LOOKUP pick exposes extra columns (v1's ly_lkp_params lkp_dir='OUT'), the parent
  // looks up each return_param's dd here and writes the value to the matching sibling field.
  // Case-insensitive (Postgres folds unquoted to lowercase). When several fields claim the
  // same dd, the first wins — v1's convention; the operator can pin a different one via the
  // explicit field.dd.
  const ddToFieldName = useMemo(() => {
    const m = new Map<string, string>()
    if (!dlg) return m
    for (const tab of dlg.tabs.filter(isFormTab)) {
      for (const f of tab.fields ?? []) {
        const dd = (f.dd || f.name).toLowerCase()
        if (!m.has(dd)) m.set(dd, f.name)
      }
    }
    return m
  }, [dlg])
  const onLookupReturnValues = useCallback((returnValues: Record<string, unknown>) => {
    setFormValues((p) => {
      const next = { ...p }
      let touched = false
      for (const [dd, v] of Object.entries(returnValues)) {
        const fieldName = ddToFieldName.get(dd.toLowerCase())
        if (!fieldName) continue
        next[fieldName] = v
        touched = true
      }
      return touched ? next : p
    })
  }, [ddToFieldName])

  // Imperative-from-async prompt plumbing. The chain runner pauses on actions with
  // ``prompt_fields`` and awaits ``requestPrompt`` — which sets ``pendingPrompt`` state +
  // stows a resolver in the ref. The mounted ActionPromptDialog reads the state; its onSubmit
  // resolves with the entered values, its onCancel resolves with ``null`` (chain aborts soft —
  // no error banner). The ref pattern keeps ``requestPrompt`` referentially stable so the
  // runner's useCallback deps don't churn on every state change.
  const [pendingPrompt, setPendingPrompt] = useState<
    | { fields: PromptField[]; title: string; cols: number | null; submitLabel: string | null }
    | null
  >(null)
  const promptResolveRef = useRef<((v: Row | null) => void) | null>(null)
  const requestPrompt = useCallback((spec: { fields: PromptField[]; title: string | null; cols: number | null; submitLabel: string | null }, fallbackTitle: string): Promise<Row | null> => {
    return new Promise<Row | null>((resolve) => {
      promptResolveRef.current = resolve
      setPendingPrompt({
        fields: spec.fields,
        title: spec.title || fallbackTitle,
        cols: spec.cols,
        submitLabel: spec.submitLabel,
      })
    })
  }, [])
  const handlePromptSubmit = useCallback((v: Row) => {
    const r = promptResolveRef.current; promptResolveRef.current = null
    setPendingPrompt(null)
    r?.(v)
  }, [])
  const handlePromptCancel = useCallback(() => {
    const r = promptResolveRef.current; promptResolveRef.current = null
    setPendingPrompt(null)
    r?.(null)
  }, [])

  // Run a list of actions through the recursive runner (:file:`actionRunner.ts`). Slice B
  // (Phase 6 4d) added the chain context — each action lands its prompt values under
  // ``INPUT.<name>`` and (when ``bind_result = true``) its rows under the step's ``id``, so
  // a later step can reference them via ``ParamBind {source: '<id>.first_row.<col>'}``.
  // ChainAction / IfAction / LoopAction / ReturnAction execute recursively from there.
  //
  // The runner is decoupled from React via ``ActionRunnerDeps``: we pass the screen's
  // effective connector (``defaultConnector``) and the same imperative-prompt resolver the
  // old inline runner used (``requestPrompt``). The result shape stays the same
  // ``{ok, warnings, refresh, error?}`` the old runner returned, plus a ``returnedValues``
  // dict from any :class:`ReturnAction` / :class:`SetFieldAction` — we merge that back into
  // ``formValues`` so the dialog reflects the workflow's output (v1's RETURN semantics).
  //
  // Backward-compat: a hand-written flat ``list[Action]`` (no chain wrapper) still works —
  // each top-level action runs sequentially against the same chain context, just without the
  // explicit grouping a v1 named-workflow migration would emit.
  const runOnSaveActions = useCallback(async (
    actions: Action[],
    baseCtx: Row,
  ): Promise<{ ok: boolean; warnings: string[]; refresh: boolean; error?: string }> => {
    const result = await runChain(actions, baseCtx, baseCtx, {
      defaultConnector: connector,
      requestPrompt,
    })
    // :class:`ReturnAction` / :class:`SetFieldAction` write into the caller's form — apply
    // those before returning so the dialog reflects the workflow's output. Match by
    // field-name case-insensitively (Postgres lowercases columns; v1 migration emits upper).
    if (Object.keys(result.returnedValues).length > 0) {
      setFormValues(prev => {
        const next: Row = { ...prev }
        for (const [k, v] of Object.entries(result.returnedValues)) {
          const matched = Object.keys(next).find(existing => existing.toLowerCase() === k.toLowerCase())
          next[matched ?? k] = v
        }
        return next
      })
    }
    // Soft cancel maps to ok=true with no error (no banner) — same convention the old runner
    // used. The result.cancelled flag is the explicit signal but the previous shape only had
    // ok/warnings/refresh/error so we keep it.
    return {
      ok: result.ok,
      warnings: result.warnings,
      refresh: result.refresh,
      error: result.error,
    }
  }, [connector, requestPrompt])

  // ``screen.actions`` was previously mirrored as in-dialog buttons in the footer. Removed —
  // the v1 model attaches actions either to *events* (``ly_evt_cpt`` → ``dialog.on_save``,
  // fires automatically on Save) or as *per-tab InputAction buttons* (``ly_dlg_col
  // col_component='InputAction'`` → ``tab.actions``). ``actionStatus`` surfaces the outcome
  // of a per-tab button click; ``actionBusy`` tracks the firing button to show a spinner.
  const [actionStatus, setActionStatus] = useState<{ message: string; tone: 'ok' | 'error' } | null>(null)
  const [actionBusy, setActionBusy] = useState<string | null>(null)
  // ``on_load`` fires once per open session — after seeding completes. The ref gates against
  // refiring on every formValues change while the dialog stays open.
  const onLoadFiredRef = useRef(false)
  useEffect(() => { if (!open) onLoadFiredRef.current = false }, [open])
  const fireTabAction = useCallback(async (a: Action) => {
    setActionBusy(a.id); setActionStatus(null)
    // Action ParamBinds resolve against the live form state — same context as on_save.
    const ctx: Row = { ...savedRow, ...formValues }
    const result = await runOnSaveActions([a], ctx)
    setActionBusy(null)
    if (!result.ok) {
      setActionStatus({ message: result.error || a.label || a.id, tone: 'error' })
    } else {
      // For ``notify`` the action emits its own message via warnings; for run_query / refresh
      // / navigate we surface a generic "<label> · OK" so the operator sees feedback.
      const msg = result.warnings.length > 0
        ? result.warnings.join(' · ')
        : `${a.label || a.id} · ${t('http.ok')}`
      setActionStatus({ message: msg, tone: 'ok' })
    }
    if (result.refresh) onSaved()
  }, [formValues, savedRow, runOnSaveActions, onSaved, t])

  // ``dialog.on_load`` — fires once after the dialog opens *and* the seeding effect has run
  // (so formValues + savedRow reflect the loaded row). Useful for: refreshing a lookup,
  // prefetching related data, logging the open. Failures surface on the action status banner
  // but don't block the open — the user can still edit. The ``onLoadFiredRef`` gate above
  // makes sure we only fire once per open session, not on every formValues tick.
  useEffect(() => {
    if (!open || !dlg || onLoadFiredRef.current) return
    const actions = (dlg.on_load ?? []) as Action[]
    onLoadFiredRef.current = true
    if (actions.length === 0) return
    void (async () => {
      const ctx: Row = { ...savedRow, ...formValues }
      const result = await runOnSaveActions(actions, ctx)
      if (!result.ok) setActionStatus({ message: result.error || t('dialog.onLoadFailed', { defaultValue: 'on_load failed' }), tone: 'error' })
      if (result.refresh) onSaved()
    })()
  }, [open, dlg, formValues, savedRow, runOnSaveActions, onSaved, t])

  // Dirty tracking — true when any field's current value differs from the seeded original.
  // Compared as strings so a number-typed input that the user typed identical to the saved
  // value doesn't show as dirty. Used by ``handleClose`` to gate the close on an unsaved-
  // changes confirm; cleared by a successful submit (formValues is replaced + savedRow
  // updates implicitly via the seeding effect on the next open).
  //
  // Password fields are skipped: their seeded value is the stored hash / ``ENC:`` blob and
  // the form is *intentionally* empty (the dialog's "leave blank to keep" pattern). Without
  // this skip every dialog with a password column shows the "Unsaved changes" modal on
  // close even with no edits — settings_applications hit this on every open via the
  // Connection / Audit Trail / Custom SQL tabs.
  const isDirty = useMemo(() => {
    const keys = new Set<string>([...Object.keys(formValues), ...Object.keys(savedRow)])
    for (const k of keys) {
      const cur = formValues[k]
      const curStr = cur == null ? '' : String(cur)
      // A password field with the form left blank is *not* dirty even though savedRow holds
      // the stored hash — that's the intentional seeding skip. Once the user types anything,
      // the field becomes dirty (curStr ≠ "") and flows through to the regular comparison.
      if (curStr === '' && isPassword(colByName.get(k.toLowerCase()) ?? null)) continue
      const seed = savedRow[k]
      const seedStr = seed == null ? '' : String(seed)
      if (curStr !== seedStr) return true
    }
    return false
  }, [formValues, savedRow, colByName])

  // ``dialog.on_cancel`` — fires before the dialog actually closes. A failing action *blocks*
  // the close (the operator sees the error and can retry or fix); when stop_on_error = false
  // on every step, the close goes through anyway. The Overlay's click-outside and the Cancel
  // button both go through ``handleClose``; with unsaved changes the close is gated on a
  // Save / Discard / Stay confirm modal so a misclick can't lose edits.
  const [closing, setClosing] = useState(false)
  const [confirmClose, setConfirmClose] = useState(false)
  const runCancelChain = useCallback(async (): Promise<boolean> => {
    const actions = (dlg?.on_cancel ?? []) as Action[]
    if (actions.length === 0) return true
    setClosing(true); setActionStatus(null)
    const ctx: Row = { ...savedRow, ...formValues }
    const result = await runOnSaveActions(actions, ctx)
    setClosing(false)
    if (!result.ok) {
      setActionStatus({ message: result.error || t('dialog.onCancelFailed', { defaultValue: 'on_cancel failed' }), tone: 'error' })
      return false   // block the close
    }
    return true
  }, [dlg, savedRow, formValues, runOnSaveActions, t])
  const handleClose = useCallback(async () => {
    if (closing) return
    if (isDirty) { setConfirmClose(true); return }
    const ok = await runCancelChain()
    if (ok) onClose()
  }, [closing, isDirty, runCancelChain, onClose])

  // Delete (edit mode + screen.delete_query). v1 didn't have this; v2 adds it as a useful
  // convenience — you're already editing a row, you decide to drop it. Confirms first. After
  // the row is deleted, ``screen.on_delete`` fires with the row's original values; on success
  // the dialog closes + the caller's onSaved() refreshes the grid.
  const [deleting, setDeleting] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  // Duplicate the open record into a new one: drop the key columns (so a fresh PK is assigned by
  // the sequence, or typed by the operator) and flip to add-mode behaviour. Every other value the
  // operator sees is kept, so they just tweak + Save → insert_query writes a new row. ``savedRow``
  // is cleared so no ``:_ORIGINAL`` WHERE binds carry over from the source record.
  const handleDuplicate = useCallback(() => {
    setFormValues((prev) => {
      const next = { ...prev }
      for (const k of Object.keys(next)) {
        if (keyColumnSet.has(k.toLowerCase())) delete next[k]
      }
      return next
    })
    setSavedRow({})
    setError(null)
    setDuplicating(true)
  }, [keyColumnSet])

  const handleDelete = useCallback(async () => {
    if (!screen.delete_query) return
    setDeleting(true); setError(null); setActionStatus(null)
    try {
      const targetConn = screen.connector || connector
      const params = withUpper(savedRow)
      // Capture the response so we can detect a no-op delete (200 OK + rowcount 0). The most
      // common cause is a CHAR-padding mismatch on the WHERE clause — covered by the pool's
      // ``trim_strings`` flag on Oracle/JDE — but stale data, the wrong delete_query, or a
      // race with another deleter can also produce it. We surface as a hard error here
      // (rather than the chain's soft warning) because the operator's intent on Delete is
      // unambiguous: "this row should be gone".
      const resp = await api.post<{ rowcount?: number; statement_type?: string }>(
        `/api/sql/${encodeURIComponent(targetConn)}/${encodeURIComponent(screen.delete_query)}`,
        { params },
      )
      if (resp?.rowcount === 0) {
        setDeleting(false)
        setError(t('dialog.deleteNoMatch', {
          defaultValue: 'Delete affected 0 rows — the row may have already been removed, or the WHERE clause didn\'t match. Try refreshing.',
        }))
        return
      }
      // Row-level on_delete hook — fires once with the deleted row's values as context.
      const onDelete = (screen.on_delete ?? []) as Action[]
      if (onDelete.length > 0) {
        const result = await runOnSaveActions(onDelete, savedRow)
        if (!result.ok) {
          // Row was deleted; the chain failed. Surface clearly and still refresh so the user
          // sees the new state. Same convention as on_save failure.
          setError(t('dialog.onSaveFailed', { message: result.error || '' }))
          onSaved()
          setDeleting(false)
          return
        }
      }
      setDeleting(false)
      onSaved()
      onClose()
    } catch (e) {
      setDeleting(false)
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }, [screen, savedRow, connector, runOnSaveActions, onSaved, onClose, t])

  // The active tab's action buttons — shown in the footer (always visible, regardless of
  // body scroll). Each tab kind (form / nested_form / nested_table) carries its own list via
  // the shared ``_ScreenTabBase.actions`` field.
  const currentTabActions: Action[] = useMemo(() => {
    const tab = tabs[Math.min(tabIdx, tabs.length - 1)] ?? null
    return ((tab?.actions ?? []) as Action[])
  }, [tabs, tabIdx])

  const submit = useCallback(async () => {
    const targetQuery = effMode === 'edit' ? screen.update_query : screen.insert_query
    if (!targetQuery) {
      setError(t(effMode === 'edit' ? 'table.editNoUpdate' : 'table.editNoInsert'))
      return
    }
    // Collect every value that's on a *currently visible* field on a non-hidden tab. Fields
    // hidden by `visible_when` (or `hidden = true`) are dropped on UPDATE so a now-irrelevant
    // column keeps its current DB value (same behaviour as v1 — a hidden field on save isn't
    // written). On INSERT we INCLUDE hidden fields when they carry a value in formValues —
    // there's no current DB value to preserve, and seeded binds (e.g. a nested_table tab's
    // parent-PK bind into the FK column) MUST land or the backend's sequence-auto-fill /
    // default-substitution machinery fires for an empty bind and the row gets the wrong PK.
    // Password fields with empty values are still dropped on both modes so we don't overwrite
    // the stored hash / ENC: ciphertext with NULL or "".
    const sent: Row = {}
    // The parent's save only collects its own form tabs' fields. Nested-form tabs write through
    // their own update/insert queries (slice 2 — coordinated via a register-on-parent-save hook).
    for (const tab of tabs.filter(isFormTab)) {
      for (const f of tab.fields ?? []) {
        if (!fieldStateOf(f).visible && effMode !== 'add') continue
        if (!(f.name in formValues)) continue
        const v = formValues[f.name]
        // On add, also skip empty hidden fields — there's no point binding NULL to a hidden
        // column the operator never saw; let the backend's defaults take over. Only hidden
        // fields WITH a value (from seed / bind / explicit default) flow through.
        if (effMode === 'add' && !fieldStateOf(f).visible && (v == null || v === '')) continue
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
      const baseSaved: Row = effMode === 'edit'
        ? Object.fromEntries(Object.entries(savedRow).filter(([k]) => {
            const c = colByName.get(k.toLowerCase()) ?? null
            return !isPassword(c)
          }))
        : {}
      const params = effMode === 'edit'
        ? { ...baseSaved, ...sent, ...originalKeys(baseSaved) }
        : sent
      const writeResp = await api.post<{ rowcount?: number; statement_type?: string; resolved_binds?: Record<string, unknown> }>(
        `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(targetQuery)}`,
        { params: withUpper(params) },
      )
      // Values the server assigned for binds we left empty — the SEQUENCE/auto-ID PK on an ADD.
      // Merge them into the parent state we hand to the nested savers so a child insert can bind
      // to the parent's freshly-assigned PK in this same Save (the "save both at once" flow).
      const parentExtra: Row = { ...sent, ...(writeResp?.resolved_binds ?? {}) }
      // No-op UPDATE — same pathology as the no-op DELETE above: the SQL succeeded but
      // the WHERE clause matched 0 rows (most often a CHAR-padding mismatch covered by
      // the pool's ``trim_strings`` flag, sometimes a stale primary-key value or the
      // wrong update_query). INSERT can't be 0-row at this layer (the SQL would have
      // raised), so only edit mode is checked.
      if (effMode === 'edit' && writeResp?.rowcount === 0) {
        setSaving(false)
        setError(t('dialog.updateNoMatch', {
          defaultValue: 'Update affected 0 rows — the row may have already been modified or removed. Try refreshing.',
        }))
        return
      }
      // Nested-form saves — v1's FormsDialog inside FormsDialog flow. Walk the registry of
      // NestedFormView savers in registration order (Map iteration is insertion order), each
      // contributing its own ``update_query`` / ``insert_query`` against its own connector.
      // A throwing saver aborts the chain and surfaces on the parent's banner; the main row
      // is already written so the user can retry the nested save without re-doing the parent.
      for (const [tabId, save] of nestedSaversRef.current) {
        try {
          await save(parentExtra)
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
      // Then run the matching row-level hook on the screen — on_insert (add) or on_update (edit).
      // Both chains see the same context. on_insert / on_update are independent of dialog.on_save
      // so an operator can wire e.g. v1 FormsTable evt 2 actions there without polluting on_save.
      const ctx: Row = { ...savedRow, ...sent }
      const dialogActions = (screen.dialog?.on_save ?? []) as Action[]
      const screenRowActions = effMode === 'add'
        ? ((screen.on_insert ?? []) as Action[])
        : ((screen.on_update ?? []) as Action[])
      const actions = [...dialogActions, ...screenRowActions]
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
    <OverlayEl onClick={() => { void handleClose() }}>
      <ModalEl $fullscreen={!nested && fullscreen} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          <FlexRow gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ flex: 1 }}>
              {effMode === 'edit' ? t('dialog.editTitle', { title }) : t('dialog.addTitle', { title })}
            </span>
            {/* Fullscreen / restore — only meaningful for the top-level dialog (nested ones are
                deliberately smaller so the parent's frame stays visible). Same affordance the
                Screen Designer modal uses; ``aria-pressed`` exposes the on/off state without
                two icons in the DOM. */}
            {!nested && (
              <Button
                $variant="ghost"
                $size="sm"
                onClick={() => setFullscreen((v) => !v)}
                title={fullscreen ? t('common.restore') : t('common.maximize')}
                aria-pressed={fullscreen}
              >
                {fullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
              </Button>
            )}
          </FlexRow>
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
                      <NestedFormView tab={tab} parentFormValues={formValues} parentConnector={connector} app={screen.app} parentMode={mode} parentDuplicating={duplicating} />
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
                // Plain form tabs lazy-render (perf — their field state lives on the parent, so
                // nothing is lost on unmount). But a tab WITH embedded nested forms must STAY
                // mounted when inactive (display:none) — each NestedFormView owns its local input +
                // saver registration, which an unmount would discard, so switching tabs then Saving
                // would drop the nested write. The main field grid still renders only when active
                // (it reads ``visibleFields``, which is the ACTIVE tab's fields).
                const embedded = (tab as FormTab).nested_forms ?? []
                if (!active && embedded.length === 0) return null
                return (
                  <div key={tab.id} style={{ display: active ? 'block' : 'none' }}>
                    {active && (
                      <FieldGrid $cols={gridCols}>
                        {visibleFields.map((f) => {
                          const st = fieldStateOf(f)
                          return (
                            <FieldRow
                              key={f.name}
                              field={f}
                              column={colByName.get(f.name.toLowerCase()) ?? null}
                              formValues={formValues}
                              onChange={onFieldChange}
                              // Foreign-lock wins over per-field rules: every input is
                              // disabled when someone else holds the row's lock. The
                              // banner above the body explains why.
                              disabled={st.disabled || readOnly}
                              required={st.required}
                              suppressLookup={effMode === 'add' && keyColumnSet.has(f.name.toLowerCase())}
                              onLookupPick={onLookupReturnValues}
                            />
                          )
                        })}
                        {visibleFields.length === 0 && embedded.length === 0 && (
                          <CellWrap $span={gridCols}>
                            <Banner $tone="info">{t('dialog.noVisibleFields')}</Banner>
                          </CellWrap>
                        )}
                      </FieldGrid>
                    )}
                    {/* Embedded nested forms — labelled sections below the main fields. Each mounts a
                        NestedFormView that registers its own saver, so this one tab writes the main
                        table + every embedded related table in a single Save (Part B). Always
                        mounted (hidden when inactive) to keep their state + registration alive. */}
                    {embedded.map((nf) => (
                      <NestedSection key={nf.id}>
                        <NestedSectionTitle>{nf.label || nf.id}</NestedSectionTitle>
                        <NestedFormView
                          tab={nf}
                          parentFormValues={formValues}
                          parentConnector={connector}
                          app={screen.app}
                          parentMode={mode}
                          parentDuplicating={duplicating}
                        />
                      </NestedSection>
                    ))}
                  </div>
                )
              })}
            </>
          )}
          {/* Foreign-lock banner — shown when another user holds the row's lock. Every
              input is disabled (see ``disabled={st.disabled || readOnly}`` above), Save
              + Delete are hidden in the footer; the banner tells the operator *why*.
              Auto-clears when the other user releases the lock — no reopen needed,
              the live broadcast updates ``foreignLock`` and the form becomes editable. */}
          {foreignLock && (
            <Banner $tone="info">
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <LockIcon size={14} />
                {t('dialog.lockedBy', {
                  user: foreignLock.username, when: formatLockTime(foreignLock.acquired_at),
                  defaultValue: 'Locked by {{user}} ({{when}}) — read-only',
                })}
              </span>
            </Banner>
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
          {/* Tab actions live on the LEFT of the footer (always visible — they used to sit
              inside the scrolling body, so on a tall tab content the user had to scroll past
              the nested table to reach Import Security / Merge Roles). ``marginRight: auto`` on
              the left group pushes Delete / Cancel / Save to the right. */}
          {currentTabActions.length > 0 && (
            <FlexRow gap={6} style={{ marginRight: 'auto', flexWrap: 'wrap' }}>
              {currentTabActions.map((a) => (
                <Button
                  key={a.id}
                  $size="sm"
                  $variant="ghost"
                  onClick={() => { void fireTabAction(a) }}
                  disabled={saving || closing || actionBusy != null}
                  title={a.id}
                >
                  {actionBusy === a.id
                    ? <SpinnerRing size={13} thickness={2} />
                    : <Zap size={13} />} {a.label || a.id}
                </Button>
              ))}
            </FlexRow>
          )}
          <FlexRow gap={8}>
            {/* Delete (edit mode + delete_query). v1 didn't have this — delete lived on the
                table — but it's a useful v2 extension: you may want to edit + decide to drop
                the row without closing the dialog first. Confirms before firing. */}
            {effMode === 'edit' && screen.delete_query && !readOnly && (
              <Button
                $size="sm"
                $variant="danger"
                onClick={() => setConfirmDelete(true)}
                disabled={saving || closing || deleting}
                title={t('common.delete')}
              >
                {deleting ? <SpinnerRing size={13} thickness={2} /> : <Trash2 size={13} />} {t('common.delete')}
              </Button>
            )}
            {/* Duplicate (edit mode + insert_query) — turn this record into a new one: drop the key
                columns, switch to add behaviour, keep the rest. Mirrors the table's bulk-edit
                duplicate, but from inside the open form. Hidden once duplicating (effMode → add). */}
            {effMode === 'edit' && screen.insert_query && !readOnly && (
              <Button
                $size="sm"
                $variant="ghost"
                onClick={handleDuplicate}
                disabled={saving || closing || deleting}
                title={t('dialog.duplicate', { defaultValue: 'Duplicate' })}
              >
                <Copy size={13} /> {t('dialog.duplicate', { defaultValue: 'Duplicate' })}
              </Button>
            )}
            <Button $size="sm" $variant="ghost" onClick={() => { void handleClose() }} disabled={saving || closing || deleting}>
              {closing ? <SpinnerRing size={13} thickness={2} /> : <X size={13} />} {t('common.cancel')}
            </Button>
            {/* Save hidden when another user holds the lock — they can't save anyway
                (every input is disabled), and removing the button makes the read-only
                state unambiguous. Cancel stays so the operator can close the dialog. */}
            {!readOnly && (
              <Button $size="sm" $variant="primary" onClick={submit} disabled={saving || closing || deleting || tabs.length === 0}>
                {saving ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
              </Button>
            )}
          </FlexRow>
        </ModalFooter>
      </ModalEl>
    </OverlayEl>
    {/* Unsaved-changes guard — Save / Discard / Stay. Shown only when the form is dirty AND
        the user attempts to close. Stops a misclick on the overlay (or Cancel) from blowing
        away edits. ``Save`` reuses the main submit flow so on_save / on_insert / on_update
        all fire as if the user clicked Save in the footer; ``Discard`` runs on_cancel then
        closes; ``Stay`` simply dismisses the prompt and leaves the dialog open. */}
    {confirmClose && (
      <Overlay onClick={(e) => { e.stopPropagation(); setConfirmClose(false) }} style={{ zIndex: 600 }}>
        <Modal style={{ width: 'min(440px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
          <ModalHeader>{t('dialog.unsavedTitle', { defaultValue: 'Unsaved changes' })}</ModalHeader>
          <ModalBody>{t('dialog.unsavedMessage', { defaultValue: 'You have unsaved changes. What would you like to do?' })}</ModalBody>
          <ModalFooter>
            <FlexRow gap={8}>
              <Button $size="sm" $variant="ghost" onClick={() => setConfirmClose(false)} disabled={saving || closing}>
                {t('dialog.unsavedStay', { defaultValue: 'Stay' })}
              </Button>
              <Button
                $size="sm"
                $variant="danger"
                onClick={async () => {
                  setConfirmClose(false)
                  const ok = await runCancelChain()
                  if (ok) onClose()
                }}
                disabled={saving || closing}
              >
                {closing ? <SpinnerRing size={13} thickness={2} /> : <X size={13} />} {t('dialog.unsavedDiscard', { defaultValue: 'Discard' })}
              </Button>
              <Button
                $size="sm"
                $variant="primary"
                onClick={() => { setConfirmClose(false); void submit() }}
                disabled={saving || closing}
              >
                {saving ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
              </Button>
            </FlexRow>
          </ModalFooter>
        </Modal>
      </Overlay>
    )}
    {/* Delete-row guard — single confirm, fires ``handleDelete`` on Yes. */}
    {confirmDelete && (
      <ConfirmModal
        title={t('dialog.deleteTitle', { defaultValue: 'Delete this row?' })}
        message={t('dialog.deleteMessage', { defaultValue: 'The row will be removed and any on_delete actions will fire. This cannot be undone.' })}
        confirmLabel={t('common.delete')}
        variant="danger"
        onConfirm={() => { setConfirmDelete(false); void handleDelete() }}
        onCancel={() => setConfirmDelete(false)}
      />
    )}
    {/* Prompt-before-fire sub-dialog (v2's port of v1's ly_act_params). Mounted at top level so
        it sits above the parent dialog's modal but below any further nested confirm. The runner
        awaits the resolver; Submit returns the values, Cancel returns null → chain aborts soft. */}
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
    </NestedSaversContext.Provider>
  )
}
