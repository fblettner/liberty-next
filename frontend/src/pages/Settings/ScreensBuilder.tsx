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
import { Save, Plus, Trash2, Search, FileText, Edit3, X, Zap, Filter, Layers, Maximize2, Minimize2 } from 'lucide-react'
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
  Stack,
  SpinnerRing,
  FrameworkEnumsContext,
  VisualBuilderModal,
  useModals,
  type FrameworkEnums,
  type JsonSchema,
} from '../../common'
import type { ConfigSchemas, ConnectorsDoc, ScreensDoc, Screen } from '../../types/config'
import { AddScopeModal } from './AddScopeModal'
import { ScopeBar as ScopeRow } from './ScopeBar'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import ScreenEditor from './ScreenEditor'

type AppScreens = Record<string, Record<string, Screen>>

// Layout: outer Shell flex-fills the page, top toolbar is fixed, the Split fills the remaining
// vertical space, and only the inner panels scroll. No page-level scroll — the operator sees
// Save / Reload / Add / Delete without chasing them past a long list.
const Shell = styled.div`
  display: flex; flex-direction: column; gap: 12px;
  flex: 1; min-height: 0; height: 100%;
`
const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0; align-items: stretch;`
// Left = the chosen app's screens list. Scrolls on its own — a deployment with dozens of screens
// for one app shouldn't drag the whole page.
const NavCol = styled.div`flex: 0 0 240px; display: flex; flex-direction: column; gap: 4px; min-width: 0; min-height: 0;`
// Filter / search bar between the app-chip strip and the screen list. Pill-shaped (matches the
// app chips above), 44px tall with a 16px icon and a larger placeholder — visually a peer of the
// chunky app chips above and the two-line screen rows below (which now run ~50px). The other
// builders (Connectors / Menus) keep a tighter 28px bar because their list items are single-line;
// Screens is the only one with double-height rows so it needed beefing up.
const NavSearch = styled.div`
  display: flex; align-items: center; gap: 10px; height: 44px; padding: 0 16px; margin: 8px 0 6px;
  border: 1px solid ${colors.border}; border-radius: 999px; background: ${colors.bg.input}; color: ${colors.text.muted};
  & svg { flex-shrink: 0; }
  & input {
    flex: 1; min-width: 0; height: 100%; border: none; background: transparent; outline: none;
    color: ${colors.text.primary}; font-size: ${fontSize.md}; font-family: ${fonts.sans};
    &::placeholder { color: ${colors.text.muted}; font-size: ${fontSize.sm}; }
  }
  &:focus-within { border-color: ${colors.blue.border}; }
`
const NavList = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding-right: 4px;`
// The screen row stacks id (mono) and label (sans) vertically — long labels were colliding with
// the id when they shared a line. The icon stays centered against the two-line text block; the
// label clamps to two lines so a "Security - Roles Matrix (combination roles for users)" type
// description never blows the row's height past three lines.
const NavItem = styled.button<{ $active?: boolean }>`
  display: flex; align-items: flex-start; gap: 8px; padding: 7px 10px; border-radius: ${radius.md}; text-align: left;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  cursor: pointer;
  & > svg { flex-shrink: 0; color: ${colors.text.muted}; margin-top: 2px; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .lbl {
    font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted};
    /* clamp the friendly description to two lines so a long label can't blow the row height up
       — the rest is truncated with an ellipsis. */
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
    overflow: hidden; line-height: 1.3;
  }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const FormCol = styled(Card)`flex: 1; min-width: 0; min-height: 0; overflow-y: auto;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`
// Summary card shown in the right pane when a screen is selected — gives the operator a quick
// read of the screen's shape (connector, queries, dialog/actions/hook counts) without opening
// the designer. The big "Open Screen Designer" button raises the full editing modal.
const SummaryDescription = styled.p`
  margin: 0; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; line-height: 1.55;
`
const SummaryGrid = styled.div`display: grid; grid-template-columns: 140px 1fr; gap: 8px 16px; align-items: baseline;`
const SummaryLabel = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`
const SummaryValue = styled.span`color: ${colors.text.primary}; font-size: ${fontSize.md}; font-family: ${fonts.mono};`
const StatChips = styled.div`display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px;`
// Renamed from ``Chip`` to avoid colliding with the app-picker Chip styled-button above —
// they're different shapes (this one's a span, no hover/click).
const StatChip = styled.span<{ $tone?: 'orange' | 'green' | 'muted' }>`
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px;
  border: 1px solid ${({ $tone }) => ($tone === 'orange' ? colors.orange.border : $tone === 'green' ? colors.green.border : colors.border)};
  background: ${({ $tone }) => ($tone === 'orange' ? colors.orange.bg : $tone === 'green' ? colors.green.bg : 'transparent')};
  color: ${({ $tone }) => ($tone === 'orange' ? colors.orange.main : $tone === 'green' ? colors.green.main : colors.text.muted)};
  font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  & svg { flex-shrink: 0; }
`
export default function ScreensBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { currentApp, refresh: refreshWorkspace } = useWorkspace()
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
  const [enums, setEnums] = useState<FrameworkEnums | null>(null)
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
  // The screen designer is a near-fullscreen modal hosting the existing ScreenEditor. The right
  // pane in Settings shows a summary card with a big "Open Screen Designer" button; click →
  // raises this modal. Edits flow through the same ``updateScreen`` callback as before — the
  // ScreensBuilder's Save bar at the bottom commits to disk. Escape closes the modal.
  const [designerOpen, setDesignerOpen] = useState(false)
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
  // doc already reflects every edit, the parent's Save button commits to disk).
  const openDesigner = () => {
    if (selApp && selId) {
      const snap = doc?.[selApp]?.[selId]
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
  // Close the designer when the operator picks a different screen — would otherwise show the
  // wrong screen's data until they reopen. Reset fullscreen too so each open starts windowed.
  // Don't revert here — the operator switched screens, they didn't ask to undo edits.
  useEffect(() => { setDesignerOpen(false); setDesignerSnapshot(null); setDesignerFullscreen(true) }, [selApp, selId])

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<ScreensDoc>('/admin/config/screens/parsed'),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed').catch((): ConnectorsDoc => ({ path: '', connectors: {} })),
    ])
      .then(([s, d, c]) => {
        setConnectors(c.connectors)
        // Build the Screen-level schema by lifting the `Screen` $def to the top while keeping the
        // full $defs map for SchemaNavigator to resolve nested refs (ScreenDialog → ScreenTab → …).
        const defs = (s.screens.$defs ?? {}) as Record<string, JsonSchema>
        const screen = (defs.Screen ?? {}) as JsonSchema
        const merged: JsonSchema = { ...screen, $defs: defs }
        setScreenSchema(merged)
        setEnums(s.framework_enums)
        setDoc(d.screens); setOriginal(JSON.stringify(d.screens))
        const apps = Object.keys(d.screens)
        // Deep-link: `?app=X&screen=Y` (e.g. from ConnectorsBuilder) pre-selects the screen.
        // Only honoured once per mount and only when the requested screen actually exists; we
        // then strip the params from the URL so a manual tab-switch doesn't lock the user into
        // that selection on subsequent visits.
        const linkApp = !consumed.current ? searchParams.get('app') : null
        const linkScreen = !consumed.current ? searchParams.get('screen') : null
        if (linkApp && d.screens[linkApp]) {
          consumed.current = true
          setSelApp(linkApp)
          if (linkScreen && d.screens[linkApp][linkScreen]) setSelId(linkScreen)
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
  // Rename a screen app — calls ``POST /admin/config/rename`` with ``kind=screen_app`` so the
  // ``[screens.<old>]`` top-level key in :file:`screens.toml` AND the matching
  // ``[menus.<old>]`` in :file:`menus.toml` (if it exists) rename together. Refuses to fire
  // with unsaved local edits (the disk rewrite + reload would clobber them). After the
  // endpoint succeeds, runs ``/admin/reload`` + ``refreshWorkspace()`` so the app picker
  // everywhere picks up the new name.
  const renameApp = async (oldName: string) => {
    if (!doc) return
    if (dirty) {
      const choice = await modals.choose({
        title: t('settings.rename.button'),
        message: t('settings.rename.unsavedFirst'),
        options: [
          { value: 'save', label: t('common.save'), variant: 'primary', autoFocus: true },
          { value: 'cancel', label: t('common.cancel'), variant: 'ghost' },
        ],
        cancelValue: 'cancel',
      })
      if (choice !== 'save') return
      await save()
      if (dirty) return
    }
    const existing = Object.keys(doc)
    const next = (await modals.prompt({
      title: t('settings.rename.button'),
      message: t('settings.screens.renameAppPrompt', { name: oldName }),
      defaultValue: oldName,
      submitLabel: t('settings.rename.button'),
      validate: (v) => {
        if (!v) return t('settings.rename.empty')
        if (v === oldName) return null
        if (existing.includes(v)) return t('settings.rename.exists', { name: v })
        if (!/^[a-z][a-z0-9_]*$/.test(v)) return t('settings.rename.invalidIdentifier')
        return null
      },
    }))?.trim()
    if (!next || next === oldName) return
    setBusy(true)
    try {
      const result = await api.post<{ files: Record<string, number>; warnings: string[]; total_refs: number }>(
        '/admin/config/rename', { kind: 'screen_app', old_name: oldName, new_name: next },
      )
      await api.post('/admin/reload')
      refreshWorkspace()
      setSelApp(next)
      load()
      const filesTouched = Object.values(result.files).filter((n) => n > 0).length
      const tail = result.warnings.length ? ` · ${result.warnings.join(' · ')}` : ''
      setStatus(t('settings.screens.appRenamedAcross', {
        from: oldName, to: next, refs: result.total_refs, files: filesTouched,
      }) + tail)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e)
      setError(t('settings.screens.appRenameFailed', { name: oldName, error: msg }))
    } finally {
      setBusy(false)
    }
  }
  // Deleting an app (all its screens) persists immediately (write screens.toml + reload), not as a
  // pending edit a reload would silently drop — same fix as the other editors.
  const removeApp = async (name: string) => {
    const ok = await modals.confirm({
      title: t('settings.screens.deleteApp'),
      message: t('settings.screens.confirmAppDelete', { name }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const next = { ...(doc ?? {}) }; delete next[name]
    await persistScreens(next, () => setSelApp((s) => (s === name ? null : s)))
  }
  const addScreen = async (app: string) => {
    const id = (await modals.prompt({
      title: t('settings.screens.add'),
      message: t('settings.screens.namePrompt'),
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
            {selApp && (
              <>
                <Button $variant="ghost" $size="sm" onClick={() => addScreen(selApp)} title={t('settings.screens.add')}>
                  <Plus size={13} /> {t('settings.screens.add')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => renameApp(selApp)} title={t('settings.screens.renameAppOne', { name: selApp })} disabled={busy}>
                  <Edit3 size={13} /> {t('settings.rename.button')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => removeApp(selApp)} title={t('settings.screens.deleteAppOne', { name: selApp })} style={{ color: colors.red.main }}>
                  <Trash2 size={13} /> {t('common.delete')}
                </Button>
              </>
            )}
            <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
              {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
            </Button>
          </>
        }
      />
      <Split>
        <NavCol>
          {selApp && (
            <>
              {ids.length > 6 && (
                <NavSearch>
                  <Search size={16} />
                  <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`filter ${ids.length}…`} />
                </NavSearch>
              )}
              <NavList>
                {shownIds.map((id) => {
                  const s = screens[id]
                  return (
                    <NavItem key={id} $active={id === selId} onClick={() => { setSelId(id); setStatus(null) }}>
                      <FileText size={13} />
                      <span className="text">
                        <span className="name">{id}</span>
                        {/* Friendly label / description below the id. Falls back to description
                            for screens whose label is blank but description is set. Two-line
                            clamp lives on the CSS so a long label doesn't blow the row's height. */}
                        {(s.label || s.description) && (
                          <span className="lbl">{s.label || s.description}</span>
                        )}
                      </span>
                    </NavItem>
                  )
                })}
                {shownIds.length === 0 && (
                  <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '2px 4px' }}>
                    {ids.length ? t('common.noMatches') : t('settings.screens.emptyApp')}
                  </div>
                )}
              </NavList>
              {/* "Add screen" and "Delete <app>" both live in the scope bar at the top — no list
                  footer needed here. Keeps every per-scope action visible without scrolling past
                  a long screen list. */}
            </>
          )}
        </NavCol>
        <FormCol>
          {selScreen && selId && selApp ? (() => {
            // Read a few stats off the selected screen so the summary card gives a quick read.
            // Cast through Record because Screen's TS type doesn't expose every optional field
            // and we just want counts.
            const sc = selScreen as unknown as Record<string, unknown>
            const dlg = sc.dialog as { tabs?: unknown[]; on_load?: unknown[]; on_save?: unknown[]; on_cancel?: unknown[] } | undefined
            const fieldCount = Array.isArray(dlg?.tabs)
              ? (dlg!.tabs as { fields?: unknown[] }[]).reduce((acc, t) => acc + (Array.isArray(t.fields) ? t.fields.length : 0), 0)
              : 0
            const tabCount = Array.isArray(dlg?.tabs) ? dlg!.tabs.length : 0
            const onLoadCount = Array.isArray(dlg?.on_load) ? dlg!.on_load.length : 0
            const onSaveCount = Array.isArray(dlg?.on_save) ? dlg!.on_save.length : 0
            const onCancelCount = Array.isArray(dlg?.on_cancel) ? dlg!.on_cancel.length : 0
            const actionsCount = Array.isArray(sc.actions) ? (sc.actions as unknown[]).length : 0
            const rowMenuCount = Array.isArray(sc.row_menu) ? (sc.row_menu as unknown[]).length : 0
            const onInsertCount = Array.isArray(sc.on_insert) ? (sc.on_insert as unknown[]).length : 0
            const onUpdateCount = Array.isArray(sc.on_update) ? (sc.on_update as unknown[]).length : 0
            const onDeleteCount = Array.isArray(sc.on_delete) ? (sc.on_delete as unknown[]).length : 0
            return (
            <Stack gap={16}>
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div>
                  <div style={{ fontFamily: fonts.sans, fontSize: fontSize.lg, fontWeight: 600, color: colors.text.primary }}>
                    {(sc.label as string | undefined) || (sc.description as string | undefined) || selId}
                  </div>
                  <div style={{ fontFamily: fonts.mono, fontSize: fontSize.sm, color: colors.text.muted, marginTop: 2 }}>
                    [screens.{selApp}.{selId}]
                  </div>
                </div>
                <Button $variant="danger" $size="sm" onClick={() => removeScreen(selApp, selId)} disabled={busy}>
                  <Trash2 size={13} /> {t('settings.screens.delete')}
                </Button>
              </Row>
              <SummaryDescription>
                {(sc.description as string | undefined) || t('settings.screens.summary.noDescription')}
              </SummaryDescription>
              <SummaryGrid>
                <SummaryLabel>{t('settings.screens.summary.connector')}</SummaryLabel>
                <SummaryValue>{(sc.connector as string | undefined) || `${selApp} (${t('settings.screens.summary.implicit')})`}</SummaryValue>
                <SummaryLabel>{t('settings.screens.summary.readQuery')}</SummaryLabel>
                <SummaryValue>{(sc.read_query as string | undefined) || <span style={{ color: colors.text.muted, fontFamily: fonts.sans }}>{t('settings.screens.summary.notSet')}</span>}</SummaryValue>
                <SummaryLabel>{t('settings.screens.summary.writeQueries')}</SummaryLabel>
                <SummaryValue style={{ fontFamily: fonts.mono, fontSize: fontSize.sm, color: colors.text.secondary }}>
                  {[
                    sc.update_query && `update: ${sc.update_query}`,
                    sc.insert_query && `insert: ${sc.insert_query}`,
                    sc.delete_query && `delete: ${sc.delete_query}`,
                  ].filter(Boolean).join(' · ') || <span style={{ fontFamily: fonts.sans, color: colors.text.muted }}>—</span>}
                </SummaryValue>
              </SummaryGrid>
              <StatChips>
                <StatChip><Layers size={11} /> {t('settings.screens.summary.tabs', { count: tabCount })}</StatChip>
                <StatChip><FileText size={11} /> {t('settings.screens.summary.fields', { count: fieldCount })}</StatChip>
                {actionsCount > 0 && <StatChip $tone="orange"><Zap size={11} /> {t('settings.screens.summary.actions', { count: actionsCount })}</StatChip>}
                {rowMenuCount > 0 && <StatChip $tone="orange"><Filter size={11} /> {t('settings.screens.summary.rowMenu', { count: rowMenuCount })}</StatChip>}
                {(onLoadCount + onSaveCount + onCancelCount) > 0 && <StatChip $tone="green"><Zap size={11} /> {t('settings.screens.summary.dialogHooks', { count: onLoadCount + onSaveCount + onCancelCount })}</StatChip>}
                {(onInsertCount + onUpdateCount + onDeleteCount) > 0 && <StatChip $tone="green"><Zap size={11} /> {t('settings.screens.summary.rowHooks', { count: onInsertCount + onUpdateCount + onDeleteCount })}</StatChip>}
              </StatChips>
              <Row>
                <Button $variant="primary" onClick={openDesigner}>
                  <Edit3 size={14} /> {t('settings.screens.openDesigner')}
                </Button>
              </Row>
            </Stack>
            )
          })() : (
            <Empty>
              {!selApp
                ? (apps.length ? t('settings.screens.pickApp') : t('settings.screens.empty'))
                : (ids.length ? t('settings.screens.pickOne') : t('settings.screens.emptyApp'))}
            </Empty>
          )}
        </FormCol>
      </Split>
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
    </FrameworkEnumsContext.Provider>
  )
}
