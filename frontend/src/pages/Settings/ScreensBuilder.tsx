// Structured editor for `config/screens.toml` — v2's port of v1's `ly_tables` + `ly_dlg_*` chain
// collapsed into one `Screen` entity per business object. Layout: left = the apps list (one chip
// per `[screens.<app>]`); below each selected app, a search-filtered list of its screens. Right =
// the selected screen rendered via `SchemaNavigator` over the `Screen` definition from
// `$defs.Screen` — that gives drill-in editing of the dialog (tabs / fields / lookup_param_binds)
// without nested accordions. Save validates the whole ScreensFile + rewrites screens.toml via
// tomlkit (the `[screens]` table is replaced wholesale — nested per-app/per-screen tables round-
// trip cleanly), then reloads. No rename of the app key yet (delete + re-add) — the inspector
// does let you rename a screen's `id` (the dict key follows).
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { Save, Plus, Trash2, Search, FileText, Edit3, X, Copy, Undo2, Maximize2, Minimize2, GitBranch, GitMerge } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../../api/client'
import {
  Button,
  Banner,
  Centered,
  Card,
  ModalFooter,
  ModalHeader,
  Overlay,
  Row,
  SpinnerRing,
  FrameworkEnumsContext,
  VisualBuilderModal,
  useModals,
  type FrameworkEnums,
  type JsonSchema,
} from '../../common'
import type { ConfigSchemas, ConnectorsDoc, ScreensDoc, Screen, DictionaryDoc } from '../../types/config'
import { AddScopeModal } from './AddScopeModal'
import { FindUsagesModal, type FindUsagesTarget } from './FindUsagesModal'
import { FindDependenciesModal, type DependencySeed } from './FindDependenciesModal'
import { ScopeBar as ScopeRow } from './ScopeBar'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import ScreenEditor from './ScreenEditor'

type AppScreens = Record<string, Record<string, Screen>>

// Layout: outer Shell flex-fills the page, top scope row + Add bar are fixed, the flat list scrolls.
// Clicking a row opens the visual designer modal (the editor itself — no inline editing here).
const Shell = styled.div`
  display: flex; flex-direction: column; gap: 12px;
  flex: 1; min-height: 0; height: 100%;
`
const FilterBar = styled.div`
  display: flex; align-items: center; gap: 8px; height: 32px; padding: 0 10px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.muted};
  flex-shrink: 0;
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none;
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
    &::placeholder { color: ${colors.text.muted}; } }
`
const List = styled(Card)`flex: 1; min-height: 0; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px;`
const Item = styled.div`
  display: flex; align-items: center; gap: 12px; padding: 10px 12px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; cursor: pointer; min-width: 0;
  & > .icon { flex-shrink: 0; color: ${colors.blue.main}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .label { font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .meta { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .actions { flex-shrink: 0; display: flex; gap: 4px; }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`
export default function ScreensBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { currentApp } = useWorkspace()
  // Pre-select via `?app=…&screen=…` on first load (the Connectors → Screens cross-link points
  // here). We consume each query param exactly once: clear it from the URL after applying so a
  // subsequent in-app navigation away + back doesn't re-yank the selection over to the deep-link
  // target. A ref tracks "already consumed" across the load → set selection re-render dance.
  const [searchParams, setSearchParams] = useSearchParams()
  const consumed = useRef(false)
  // The schema we navigate is the `Screen` definition from the ScreensFile's $defs — that's where
  // dialog/tabs/fields/param-binds live. We pass the *whole* $defs along so SchemaNavigator can
  // resolve refs into ScreenDialog / ScreenTab / ScreenField / ParamBind / ScreenAction at the
  // right level when the user drills in.
  const [screenSchema, setScreenSchema] = useState<JsonSchema | null>(null)
  // Bundled framework enums (DICTIONARY_TYPE / QUERY_TYPE / …) from the schema endpoint. The
  // dynamic per-app DD_ENTRIES (the dictionary-entries dropdown that drives ``ColumnHint.dd``)
  // is computed below and layered on top before we hand the enums down to ScreenEditor.
  const [bundledEnums, setBundledEnums] = useState<FrameworkEnums | null>(null)
  // The dictionary document — shared entries + per-connector overlays. Needed to populate
  // DD_ENTRIES for the currently-selected screens app (= connector).
  const [dictionary, setDictionary] = useState<DictionaryDoc['dictionary'] | null>(null)
  // Read-only — only used to offer connectors-without-screens when creating a new screens app
  // (an app *is* a connector, so we never invent a free-form app name).
  const [connectors, setConnectors] = useState<Record<string, unknown> | null>(null)
  const [doc, setDoc] = useState<AppScreens | null>(null)
  const [original, setOriginal] = useState<string>('')
  const [selApp, setSelApp] = useState<string | null>(null)
  const [selId, setSelId] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [addScopeOpen, setAddScopeOpen] = useState(false)
  const [usagesTarget, setUsagesTarget] = useState<FindUsagesTarget | null>(null)
  const [depsSeeds, setDepsSeeds] = useState<DependencySeed[] | null>(null)
  // The screen designer is a near-fullscreen modal hosting the existing ScreenEditor. The right
  // pane in Settings shows a summary card with a big "Open Screen Designer" button; click →
  // raises this modal. Edits flow through the same ``updateScreen`` callback as before — the
  // ScreensBuilder's Save bar at the bottom commits to disk. Escape closes the modal.
  const [designerOpen, setDesignerOpen] = useState(false)
  // Set in the load handler when a ``?app=X&screen=Y`` deep-link resolves so the designer opens
  // automatically once selApp/selId/doc have all caught up (you can't call ``openDesigner`` inline
  // because the freshly-fetched ``doc`` isn't yet in state — the snapshot lookup would miss).
  const [autoOpenDesigner, setAutoOpenDesigner] = useState(false)
  // Snapshot of the screen at modal-open — Cancel reverts to this so edits made inside the
  // designer (e.g. "Create dialog" + a few fields) don't stick when the operator changes their
  // mind. Save clears the snapshot before closing. Stored as a JSON-serialised string so a deep
  // copy is cheap; ``selScreen`` is re-derived from ``doc`` on every render.
  const [designerSnapshot, setDesignerSnapshot] = useState<string | null>(null)
  // Fullscreen toggle for the designer modal — the default 1400×900 envelope can still feel
  // tight on a complex screen (palette + many fields + inspector). Operator clicks the maximize
  // icon in the header, the modal grows to 100vw / 100vh + drops its border-radius.
  // Default to fullscreen: the Screen Designer carries a lot of information (the dialog's
  // tabs + fields + every action's prompt fields + the inspector) and almost always benefits
  // from the whole viewport. Operators can hit the restore button (top-right of the modal
  // header) to drop back to the windowed envelope when they want to see the underlying
  // Screens list behind it.
  const [designerFullscreen, setDesignerFullscreen] = useState(true)
  // Open / close helpers — Cancel restores the snapshot, Save just discards it (the in-memory
  // doc already reflects every edit, the parent's Save button commits to disk). Optional ``id``
  // lets the caller pick the screen and open the designer in one click — otherwise the
  // setSelId / openDesigner pair in a click handler hits a stale closure (selId still null).
  const openDesigner = (id?: string) => {
    if (id) setSelId(id)
    const targetId = id ?? selId
    if (selApp && targetId) {
      const snap = doc?.[selApp]?.[targetId]
      if (snap !== undefined) setDesignerSnapshot(JSON.stringify(snap))
    }
    setDesignerOpen(true)
  }
  const cancelDesigner = async () => {
    // If the operator made changes since opening the designer, show a 3-way prompt:
    // **Discard** reverts the snapshot + closes (the current cancel behaviour).
    // **Save** commits to disk + closes — same as clicking the footer Save button.
    // **Keep editing** dismisses the prompt without closing anything.
    // When nothing changed inside the modal, just close (no prompt needed).
    const hasModalEdits = (() => {
      if (designerSnapshot == null || !selApp || !selId) return false
      const cur = doc?.[selApp]?.[selId]
      if (cur === undefined) return false
      try { return JSON.stringify(cur) !== designerSnapshot } catch { return false }
    })()
    if (hasModalEdits) {
      const choice = await modals.choose({
        title: t('settings.screens.designer.unsavedTitle', 'Unsaved changes'),
        message: t('settings.screens.designer.unsavedMsg', 'You have unsaved changes in this screen. Save them, discard them, or keep editing?'),
        options: [
          { value: 'discard', label: t('settings.screens.designer.discard', 'Discard'), variant: 'danger' },
          { value: 'save', label: t('common.save'), variant: 'primary' },
          { value: 'keep', label: t('settings.screens.designer.keepEditing', 'Keep editing'), variant: 'ghost', autoFocus: true },
        ],
        cancelValue: 'keep',  // Escape / overlay click = keep editing
      })
      if (choice === 'keep' || choice == null) return  // dismiss the close
      if (choice === 'save') {
        await save()
        setDesignerSnapshot(null)
        setDesignerOpen(false)
        return
      }
      // discard → fall through to the revert path below
    }
    // Revert every edit made in the modal session. When the snapshot is missing (the operator
    // opened an outdated reference) we fall through and just close — better than throwing.
    if (designerSnapshot != null && selApp && selId) {
      try {
        const snap = JSON.parse(designerSnapshot) as Screen
        updateScreen(selApp, selId, snap as unknown as Record<string, unknown>)
      } catch {
        // ignore — snapshot was malformed, just close
      }
    }
    setDesignerSnapshot(null)
    setDesignerOpen(false)
  }
  const closeDesigner = () => {
    // Save path — the snapshot is no longer needed; doc already has the saved edits.
    setDesignerSnapshot(null)
    setDesignerOpen(false)
  }
  useEffect(() => {
    if (!designerOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') cancelDesigner() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps  -- cancelDesigner reads stable state
  }, [designerOpen])
  // Switching the *app* closes the designer (the previously-selected screen no longer applies).
  // Row clicks set selId + open the designer in one go via ``openDesigner(id)`` — there's no
  // selId-change observer that would otherwise close the modal we just opened.
  useEffect(() => { setDesignerOpen(false); setDesignerSnapshot(null); setDesignerFullscreen(true) }, [selApp])
  // Honour the deep-link ``autoOpenDesigner`` once selApp/selId/doc are all in sync (the load
  // handler set it after a ``?app=X&screen=Y`` cross-link from ConnectorsBuilder).
  useEffect(() => {
    if (!autoOpenDesigner || !selApp || !selId || !doc) return
    openDesigner()
    setAutoOpenDesigner(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- openDesigner is stable from closure
  }, [autoOpenDesigner, selApp, selId, doc])

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<ScreensDoc>('/admin/config/screens/parsed'),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed').catch((): ConnectorsDoc => ({ path: '', connectors: {} })),
      // Dictionary feeds the DD_ENTRIES dropdown on ColumnHint.dd in the Columns tab.
      // A failure is non-fatal — the dd field would just lose its dropdown suggestions.
      api.get<DictionaryDoc>('/admin/config/dictionary/parsed').catch((): DictionaryDoc => ({ path: '', dictionary: {} as DictionaryDoc['dictionary'] })),
    ])
      .then(([s, d, c, dict]) => {
        setConnectors(c.connectors)
        setDictionary(dict.dictionary)
        // Build the Screen-level schema by lifting the `Screen` $def to the top while keeping the
        // full $defs map for SchemaNavigator to resolve nested refs (ScreenDialog → ScreenTab → …).
        const defs = (s.screens.$defs ?? {}) as Record<string, JsonSchema>
        const screen = (defs.Screen ?? {}) as JsonSchema
        const merged: JsonSchema = { ...screen, $defs: defs }
        setScreenSchema(merged)
        setBundledEnums(s.framework_enums)
        setDoc(d.screens); setOriginal(JSON.stringify(d.screens))
        const apps = Object.keys(d.screens)
        // Deep-link: `?app=X&screen=Y` (e.g. from ConnectorsBuilder's "Open visual builder")
        // pre-selects the screen AND opens the visual designer in one go — the connector editor
        // no longer drops you on the Screens list to click again. Only honoured once per mount
        // and only when the requested screen actually exists; we then strip the params from the
        // URL so a manual tab-switch doesn't lock the user into that selection on subsequent visits.
        const linkApp = !consumed.current ? searchParams.get('app') : null
        const linkScreen = !consumed.current ? searchParams.get('screen') : null
        if (linkApp && d.screens[linkApp]) {
          consumed.current = true
          setSelApp(linkApp)
          if (linkScreen && d.screens[linkApp][linkScreen]) {
            setSelId(linkScreen)
            setAutoOpenDesigner(true)
          }
          const np = new URLSearchParams(searchParams)
          np.delete('app'); np.delete('screen')
          setSearchParams(np, { replace: true })
        } else {
          // Preserve the picked app/screen when reloading; on first open default to the workspace's
          // selected app (when it has screens here), else the first available.
          setSelApp((cur) => (cur && d.screens[cur] ? cur : (currentApp && d.screens[currentApp] ? currentApp : apps[0] ?? null)))
        }
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  // When the app changes, drop the search + restore (or clear) the screen selection.
  useEffect(() => {
    if (!doc || !selApp) { setSelId(null); return }
    setQ('')
    const ids = Object.keys(doc[selApp] ?? {})
    setSelId((cur) => (cur && doc[selApp]?.[cur] ? cur : ids[0] ?? null))
  }, [selApp, doc])

  const dirty = useMemo(() => doc != null && JSON.stringify(doc) !== original, [doc, original])

  // ── mutators ────────────────────────────────────────────────────────────────────
  const updateScreen = (app: string, id: string, v: Record<string, unknown>) =>
    setDoc((p) => {
      // SchemaNavigator may have renamed `id` — when it does, the dict key follows + every
      // same-app sibling screen's references to the old id are rewritten (``row_click_screen``
      // at the top level, ``NestedTableTab.screen`` inside each screen's ``dialog.tabs``).
      // Cross-app + cross-file refs are NOT touched — those live in different files behind
      // different PUTs and a multi-document write is out of scope.
      const cur = p ?? {}
      const appCur = { ...(cur[app] ?? {}) }
      const newId = typeof v.id === 'string' && v.id.trim() ? v.id.trim() : id
      if (newId !== id) delete appCur[id]
      appCur[newId] = { ...(v as unknown as Screen), id: newId }
      if (newId !== id) {
        for (const [otherId, otherScreen] of Object.entries(appCur)) {
          if (otherId === newId) continue
          const sc = otherScreen as unknown as Record<string, unknown>
          let mut: Record<string, unknown> | null = null
          if (sc.row_click_screen === id) {
            mut = { ...sc, row_click_screen: newId }
          }
          const dlg = sc.dialog as Record<string, unknown> | undefined
          if (dlg && Array.isArray(dlg.tabs)) {
            const tabs = dlg.tabs as Record<string, unknown>[]
            let tabsChanged = false
            const nextTabs = tabs.map((tab) => {
              if (tab?.type === 'nested_table' && tab.screen === id) {
                tabsChanged = true
                return { ...tab, screen: newId }
              }
              return tab
            })
            if (tabsChanged) {
              mut = { ...(mut ?? sc), dialog: { ...dlg, tabs: nextTabs } }
            }
          }
          if (mut) appCur[otherId] = mut as unknown as Screen
        }
      }
      const next = { ...cur, [app]: appCur }
      // Keep the selection pinned to the (possibly renamed) screen.
      if (newId !== id) setSelId(newId)
      return next
    })

  // "Add scope" = give an existing connector a screens section. A screens app *is* a connector (the
  // section key is the connector name, and each screen's connector defaults to it), so the
  // AddScopeModal offers the connectors that don't have a screens section yet — never a free-form
  // name (which produced an orphan `[screens.<name>]` with no backing connector).
  const addScopeCandidates = Object.keys(connectors ?? {}).filter((c) => !(doc ?? {})[c]).sort()
  const onPickScope = (name: string) => {
    setDoc((p) => ({ ...(p ?? {}), [name]: { ...((p ?? {})[name] ?? {}) } }))
    setSelApp(name); setStatus(null)
  }
  // App-level rename / delete are handled by the connector cascade (Settings → Connectors →
  // Clone / Rename / Delete operate across connectors.toml + dictionary.toml + menus.toml +
  // screens.toml + charts.toml + dashboards.toml). Per-screen Clone / Rename / Delete + Add
  // screen live here; everything else stays in the Connectors editor.
  const validateScreenId = (v: string, app: string, oldId: string | null): string | null => {
    if (!v) return t('common.nameRequired', 'Name is required.')
    if (oldId && v === oldId) return t('settings.tables.duplicateSameName', 'Pick a different name.')
    if (v in (doc?.[app] ?? {})) return t('common.nameExists', { name: v })
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(v)) return t('settings.rename.invalidIdentifier', 'Use letters, digits, underscore; leading letter.')
    return null
  }
  const addScreen = async (app: string) => {
    const id = (await modals.prompt({
      title: t('settings.screens.add'),
      message: t('settings.screens.namePrompt'),
      validate: (v) => validateScreenId(v.trim(), app, null),
    }))?.trim()
    if (!id) return
    setDoc((p) => {
      const cur = p ?? {}
      const appCur = { ...(cur[app] ?? {}) }
      if (appCur[id]) return cur
      appCur[id] = { id, read_query: '' }
      return { ...cur, [app]: appCur }
    })
    setSelId(id); setStatus(null)
    openDesigner()
  }
  const renameScreen = async (app: string, oldId: string) => {
    const next = (await modals.prompt({
      title: t('settings.rename.button', 'Rename'),
      message: t('settings.screens.renamePrompt', 'New id for "{{name}}":', { name: oldId }),
      defaultValue: oldId, submitLabel: t('settings.rename.button', 'Rename'),
      validate: (v) => validateScreenId(v.trim(), app, oldId),
    }))?.trim()
    if (!next || next === oldId) return
    // Reuse updateScreen's id-rename machinery (also rewrites same-app sibling references to oldId).
    const src = (doc?.[app]?.[oldId] ?? {}) as Record<string, unknown>
    updateScreen(app, oldId, { ...src, id: next })
  }
  const cloneScreen = async (app: string, oldId: string) => {
    const next = (await modals.prompt({
      title: t('common.clone', 'Clone'),
      message: t('settings.screens.clonePrompt', 'New id for the copy of "{{name}}":', { name: oldId }),
      defaultValue: `${oldId}_copy`, submitLabel: t('common.clone', 'Clone'),
      validate: (v) => validateScreenId(v.trim(), app, oldId),
    }))?.trim()
    if (!next) return
    setDoc((p) => {
      const cur = p ?? {}
      const appCur = { ...(cur[app] ?? {}) }
      const src = JSON.parse(JSON.stringify(appCur[oldId])) as Record<string, unknown>
      src.id = next
      appCur[next] = src as unknown as Screen
      return { ...cur, [app]: appCur }
    })
    setSelId(next); setStatus(null)
  }
  const discard = () => {
    if (!original) return
    try { setDoc(JSON.parse(original) as AppScreens); setStatus(null); setError(null) } catch { /* ignore */ }
  }
  // Deleting a screen persists immediately — a screen is a top-level record, so its delete
  // shouldn't sit as a pending edit a reload would silently drop.
  const removeScreen = async (app: string, id: string) => {
    const ok = await modals.confirm({
      title: t('settings.screens.delete'),
      message: t('settings.screens.confirmDelete', { name: id }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const cur = doc ?? {}
    const appCur = { ...(cur[app] ?? {}) }
    delete appCur[id]
    await persistScreens({ ...cur, [app]: appCur }, () => setSelId((s) => (s === id ? null : s)))
  }

  // Write the whole screens.toml + reload (the shared persist path for Save + the immediate
  // deletes). ``after`` runs on success (clear the relevant selection) before re-loading.
  async function persistScreens(next: AppScreens, after?: () => void) {
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/screens/parsed', { screens: next })
      const r = await api.post<{ screen_apps: string[] }>('/admin/reload')
      after?.()
      const list = (r.screen_apps ?? []).join(', ') || `(${t('common.none')})`
      setStatus(t('settings.screens.saved', { apps: list }))
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  async function save() {
    if (!doc) return
    await persistScreens(doc)
  }

  // Per-app dynamic enum: DD_ENTRIES = the dictionary entries available to ``selApp``'s screens
  // (the shared scope merged with the connector's own overlay; per-connector wins on collision —
  // same merge order the runtime applies in ``DictionaryFile.find_entry``). Layered on top of the
  // bundled framework enums so the ColumnHint.dd / PromptField.dd dropdown surfaces a real list
  // instead of a plain text input. Recomputed when the app selection changes or the dictionary is
  // re-fetched.
  const enums: FrameworkEnums | null = useMemo(() => {
    if (!bundledEnums) return null
    const base: FrameworkEnums = { ...bundledEnums }
    if (!dictionary) {
      base.DD_ENTRIES = { label: 'Dictionary entries', values: [] }
      return base
    }
    const out = new Map<string, string>()
    const ingest = (m: Record<string, Record<string, unknown>> | undefined) => {
      if (!m) return
      for (const [id, rec] of Object.entries(m)) {
        const lbl = typeof (rec as Record<string, unknown>)?.label === 'string'
          ? ((rec as Record<string, unknown>).label as string)
          : id
        out.set(id, lbl)
      }
    }
    ingest(dictionary.entries)
    if (selApp) ingest((dictionary.connectors ?? {})[selApp]?.entries)
    const values = [...out.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([value, label]) => ({ value, label }))
    base.DD_ENTRIES = { label: 'Dictionary entries (this app)', values }
    return base
  }, [bundledEnums, dictionary, selApp])

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc || !screenSchema) return <Centered />

  // Alphabetical sort — both the app scope-chip strip and the per-app screen list. v1's
  // ``tbl_seq`` / ``apps_seq`` ordering doesn't carry over to v2 (each `[screens.<app>.<id>]`
  // is just a dict), so a stable name-sorted order matches operator expectations.
  const apps = Object.keys(doc).sort((a, b) => a.localeCompare(b))
  const screens = (selApp && doc[selApp]) || {}
  const ids = Object.keys(screens).sort((a, b) => a.localeCompare(b))
  const needle = q.trim().toLowerCase()
  const shownIds = needle
    ? ids.filter((id) => {
        const s = screens[id]
        const haystack = `${id} ${s.label ?? ''} ${s.description ?? ''}`.toLowerCase()
        return haystack.includes(needle)
      })
    : ids
  const selScreen = selApp && selId ? doc[selApp]?.[selId] ?? null : null

  return (
    <FrameworkEnumsContext.Provider value={enums}>
    <Shell>
      {/* One consolidated top toolbar — config path + status on the left, scope chips and every
          action (Add app · Add screen · Delete app · Save · Reload) inline on the right. Replaces
          the old "scope bar at top + bottom Save Row" split — the operator never has to scroll
          past a long screen list to reach Save / Reload. */}
      <ScopeRow
        scopes={apps.map((a) => ({ value: a, label: a }))}
        value={selApp ?? ''}
        onChange={(a) => { setSelApp(a); setStatus(null) }}
        onAddScope={() => setAddScopeOpen(true)}
        leading={(dirty || status || error) ? (
          <>
            {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
            {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
            {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
          </>
        ) : undefined}
        right={
          <>
            <Button $variant="ghost" $size="sm" onClick={() => selApp && addScreen(selApp)} disabled={busy || !selApp}>
              <Plus size={13} /> {t('settings.screens.add')}
            </Button>
            <Button $variant="ghost" $size="sm" onClick={discard} disabled={busy || !dirty}>
              <Undo2 size={13} /> {t('common.discard', 'Discard')}
            </Button>
            <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
              {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
            </Button>
          </>
        }
      />
      {selApp && ids.length > 0 && (
        <FilterBar>
          <Search size={14} />
          <input value={q} onChange={(e) => setQ(e.target.value)}
            placeholder={t('settings.filterPlaceholder', 'Filter…')} />
        </FilterBar>
      )}
      <List>
        {selApp && shownIds.map((id) => {
          const sc = screens[id] as unknown as Record<string, unknown>
          const dlg = sc.dialog as { tabs?: unknown[] } | undefined
          const tabCount = Array.isArray(dlg?.tabs) ? dlg!.tabs.length : 0
          const fieldCount = Array.isArray(dlg?.tabs)
            ? (dlg!.tabs as { fields?: unknown[] }[]).reduce((acc, t) => acc + (Array.isArray(t.fields) ? t.fields.length : 0), 0)
            : 0
          const actionsCount = Array.isArray(sc.actions) ? (sc.actions as unknown[]).length : 0
          const rowMenuCount = Array.isArray(sc.row_menu) ? (sc.row_menu as unknown[]).length : 0
          const connector = (sc.connector as string | undefined) || selApp
          const readQuery = sc.read_query as string | undefined
          const label = (sc.label as string | undefined) || (sc.description as string | undefined)
          const meta = [
            readQuery && `${connector}.${readQuery}`,
            t('settings.screens.summary.tabs', { count: tabCount }),
            t('settings.screens.summary.fields', { count: fieldCount }),
            actionsCount > 0 && t('settings.screens.summary.actions', { count: actionsCount }),
            rowMenuCount > 0 && t('settings.screens.summary.rowMenu', { count: rowMenuCount }),
          ].filter(Boolean).join(' · ')
          return (
            <Item key={id} onClick={() => { setStatus(null); openDesigner(id) }}>
              <FileText className="icon" size={18} />
              <span className="text">
                <span className="name">{id}</span>
                {label && <span className="label">{label}</span>}
                {meta && <span className="meta">{meta}</span>}
              </span>
              <span className="actions" onClick={(e) => e.stopPropagation()}>
                <Button $variant="ghost" $size="sm" onClick={() => setUsagesTarget({
                  kind: 'screen', name: id, scope: selApp, label: `screen ${selApp}.${id}`,
                })} disabled={busy}>
                  <GitBranch size={13} /> {t('findUsages.button', 'Find usages')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => setDepsSeeds([{
                  kind: 'screen', name: id, scope: selApp, label: `screen ${selApp}.${id}`,
                }])} disabled={busy}>
                  <GitMerge size={13} /> {t('findDeps.button', 'Find dependencies')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void renameScreen(selApp, id)} disabled={busy}>
                  <Edit3 size={13} /> {t('settings.rename.button', 'Rename')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void cloneScreen(selApp, id)} disabled={busy}>
                  <Copy size={13} /> {t('common.clone', 'Clone')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void removeScreen(selApp, id)} disabled={busy} style={{ color: colors.red.main }}>
                  <Trash2 size={13} /> {t('common.delete', 'Delete')}
                </Button>
              </span>
            </Item>
          )
        })}
        {(!selApp || shownIds.length === 0) && (
          <Empty>
            {!selApp
              ? (apps.length ? t('settings.screens.pickApp') : t('settings.screens.empty'))
              : ids.length === 0
                ? t('settings.screens.emptyApp')
                : t('common.noMatches', 'No matches')}
          </Empty>
        )}
      </List>
      {addScopeOpen && (
        <AddScopeModal
          candidates={addScopeCandidates}
          title={t('settings.screens.addApp', 'Add screens for a connector')}
          emptyHint={t('settings.screens.allHaveScreens', 'Every connector already has a screens section. Add a connector in the Connectors tab first.')}
          onPick={onPickScope}
          onClose={() => setAddScopeOpen(false)}
        />
      )}
    </Shell>
    {/* Screen Designer modal — near-fullscreen (VisualBuilderModal preset) hosting the existing
        ScreenEditor with its 5 internal tabs (General / Queries / Dialog / Actions / Row menu).
        Portaled to document.body because Settings panels use backdrop-filter (the liquid-glass
        surface), which would otherwise clamp position:fixed to the panel bounds. The modal closes
        on Overlay click, header X, and Escape (handler bound above). Edits flow through the same
        updateScreen → setDoc chain as before; ScreensBuilder's Save bar at the bottom commits. */}
    {designerOpen && selScreen && selApp && selId && createPortal(
      // Overlay click = Cancel (revert in-modal edits). Matches the dialog convention everywhere
      // else in the app: clicking outside a modal cancels.
      <Overlay onClick={cancelDesigner}>
        <VisualBuilderModal $fullscreen={designerFullscreen} onClick={(e) => e.stopPropagation()}>
          <ModalHeader>
            {/* The header is two flex regions — title on the left, actions clustered on the
                right. Wrapping the actions in their own Row prevents ``justify-content:
                space-between`` from distributing each child (title / dirty / maximize / close)
                evenly across the bar, which pushed the maximize button into the middle. */}
            <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
              <span>
                {t('settings.screens.designerTitle')} ·{' '}
                <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontWeight: 400 }}>
                  [screens.{selApp}.{selId}]
                </span>
              </span>
              <Row gap={8} style={{ alignItems: 'center' }}>
                {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
                {/* Fullscreen / restore — same affordance browsers + IDEs use. ``aria-pressed``
                    exposes the on/off state to screen readers without needing two icons in DOM. */}
                <Button
                  $variant="ghost"
                  $size="sm"
                  onClick={() => setDesignerFullscreen((v) => !v)}
                  title={designerFullscreen ? t('common.restore') : t('common.maximize')}
                  aria-pressed={designerFullscreen}
                >
                  {designerFullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={cancelDesigner}>
                  <X size={13} /> {t('common.cancel')}
                </Button>
              </Row>
            </Row>
          </ModalHeader>
          {/* Custom body styling — overflow:hidden so the ScreenEditor's per-tab scroll wrappers
              own the scroll. Without this the modal body would scroll and the visual builder's
              panels would lose their fixed-height layout. */}
          <div style={{
            flex: '1 1 auto',
            minHeight: 0,
            padding: 20,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
            color: colors.text.secondary,
            fontSize: fontSize.md,
          }}>
            <ScreenEditor
              app={selApp}
              id={selId}
              value={selScreen as unknown as Record<string, unknown>}
              schema={screenSchema}
              // Sibling screen IDs feed the "open dialog" row-click picker — the operator
              // picks from a dropdown of real screens in this app instead of typing the id.
              siblingScreenIds={Object.keys(doc?.[selApp] ?? {}).filter((i) => i !== selId)}
              onChange={(v) => updateScreen(selApp, selId, v)}
            />
          </div>
          {/* Footer with Save button — calls the same save() that ScreensBuilder's main bar uses
              + closes the modal on success. Without this, operators couldn't see the main Save
              bar (hidden behind the modal); they'd have to close, then save, awkward two-step. */}
          <ModalFooter>
            <Row gap={8}>
              {/* Cancel reverts every edit made in this modal session to the snapshot taken
                  when the designer opened — operator can experiment without leaving stale
                  state behind if they change their mind. */}
              <Button onClick={cancelDesigner} $variant="ghost" $size="sm" disabled={busy}>
                <X size={13} /> {t('common.cancel')}
              </Button>
              <Button
                $variant="primary"
                $size="sm"
                disabled={busy || !dirty}
                onClick={async () => {
                  await save()
                  closeDesigner()
                }}
              >
                {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
              </Button>
            </Row>
          </ModalFooter>
        </VisualBuilderModal>
      </Overlay>,
      document.body,
    )}
    {usagesTarget && <FindUsagesModal target={usagesTarget} onClose={() => setUsagesTarget(null)} />}
    {depsSeeds && <FindDependenciesModal seeds={depsSeeds} onClose={() => setDepsSeeds(null)} />}
    </FrameworkEnumsContext.Provider>
  )
}
