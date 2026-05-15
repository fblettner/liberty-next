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
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, Search, FolderOpen, FileText } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { api, ApiError } from '../../api/client'
import {
  Button,
  Banner,
  Centered,
  Card,
  Row,
  Stack,
  SpinnerRing,
  Mono,
  FrameworkEnumsContext,
  type FrameworkEnums,
  type JsonSchema,
} from '../../common'
import type { ConfigSchemas, ScreensDoc, Screen } from '../../types/config'
import { colors, fontSize, fonts, radius } from '../../theme'
import ScreenEditor from './ScreenEditor'

type AppScreens = Record<string, Record<string, Screen>>

const Split = styled.div`display: flex; gap: 14px; align-items: flex-start;`
// Left = two stacked columns: the apps chip strip + the chosen app's screens list. Both scroll on
// their own — a deployment with dozens of screens for one app shouldn't drag the whole page.
const NavCol = styled.div`flex: 0 0 240px; display: flex; flex-direction: column; gap: 4px; min-width: 0; max-height: calc(100dvh - 18rem);`
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
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 4px;`
const Chip = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.mono}; cursor: pointer;
  & svg { color: ${colors.text.muted}; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
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
const FormCol = styled(Card)`flex: 1; min-width: 0;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`
const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.5; margin: 0;`

export default function ScreensBuilder() {
  const { t } = useTranslation()
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
  const [path, setPath] = useState('')
  const [doc, setDoc] = useState<AppScreens | null>(null)
  const [original, setOriginal] = useState<string>('')
  const [selApp, setSelApp] = useState<string | null>(null)
  const [selId, setSelId] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<ScreensDoc>('/admin/config/screens/parsed'),
    ])
      .then(([s, d]) => {
        // Build the Screen-level schema by lifting the `Screen` $def to the top while keeping the
        // full $defs map for SchemaNavigator to resolve nested refs (ScreenDialog → ScreenTab → …).
        const defs = (s.screens.$defs ?? {}) as Record<string, JsonSchema>
        const screen = (defs.Screen ?? {}) as JsonSchema
        const merged: JsonSchema = { ...screen, $defs: defs }
        setScreenSchema(merged)
        setEnums(s.framework_enums)
        setPath(d.path); setDoc(d.screens); setOriginal(JSON.stringify(d.screens))
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
          // Preserve the picked app/screen when reloading; default to the first available.
          setSelApp((cur) => (cur && d.screens[cur] ? cur : apps[0] ?? null))
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

  const addApp = () => {
    const name = window.prompt(t('settings.screens.appNamePrompt'))?.trim()
    if (!name) return
    setDoc((p) => ({ ...(p ?? {}), [name]: { ...((p ?? {})[name] ?? {}) } }))
    setSelApp(name); setStatus(null)
  }
  const removeApp = (name: string) => {
    if (!window.confirm(t('settings.screens.confirmAppDelete', { name }))) return
    setDoc((p) => { const next = { ...(p ?? {}) }; delete next[name]; return next })
    setSelApp((s) => (s === name ? null : s)); setStatus(null)
  }
  const addScreen = (app: string) => {
    const id = window.prompt(t('settings.screens.namePrompt'))?.trim()
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
  const removeScreen = (app: string, id: string) => {
    if (!window.confirm(t('settings.screens.confirmDelete', { name: id }))) return
    setDoc((p) => {
      const cur = p ?? {}
      const appCur = { ...(cur[app] ?? {}) }
      delete appCur[id]
      return { ...cur, [app]: appCur }
    })
    setSelId((s) => (s === id ? null : s)); setStatus(null)
  }

  async function save() {
    if (!doc) return
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/screens/parsed', { screens: doc })
      const r = await api.post<{ screen_apps: string[] }>('/admin/reload')
      const list = (r.screen_apps ?? []).join(', ') || `(${t('common.none')})`
      setStatus(t('settings.screens.saved', { apps: list }))
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc || !screenSchema) return <Centered />

  const apps = Object.keys(doc)
  const screens = (selApp && doc[selApp]) || {}
  const ids = Object.keys(screens)
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
    <Stack gap={12}>
      <Mono>{path}</Mono>
      <Split>
        <NavCol>
          {/* App chips — same affordance as the DictionaryBuilder's scope chips. */}
          <Chips>
            {apps.map((a) => (
              <Chip key={a} $active={a === selApp} onClick={() => { setSelApp(a); setStatus(null) }}>
                <FolderOpen size={12} /> {a}
              </Chip>
            ))}
            <Chip onClick={addApp} title={t('settings.screens.addApp')}>
              <Plus size={12} /> {t('settings.screens.addApp')}
            </Chip>
          </Chips>
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
              <Row gap={4} style={{ marginTop: 6 }}>
                <Button $variant="ghost" $size="sm" onClick={() => selApp && addScreen(selApp)} style={{ flex: 1, justifyContent: 'flex-start' }}>
                  <Plus size={13} /> {t('settings.screens.add')}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => selApp && removeApp(selApp)} title={t('settings.screens.deleteApp')}>
                  <Trash2 size={13} />
                </Button>
              </Row>
            </>
          )}
        </NavCol>
        <FormCol>
          {selScreen && selId && selApp ? (
            <Stack gap={12}>
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>
                  [screens.{selApp}.{selId}]
                </strong>
                <Button $variant="danger" $size="sm" onClick={() => removeScreen(selApp, selId)} disabled={busy}>
                  <Trash2 size={13} /> {t('settings.screens.delete')}
                </Button>
              </Row>
              <ScreenEditor
                app={selApp}
                id={selId}
                value={selScreen as unknown as Record<string, unknown>}
                schema={screenSchema}
                onChange={(v) => updateScreen(selApp, selId, v)}
              />
            </Stack>
          ) : (
            <Empty>
              {!selApp
                ? (apps.length ? t('settings.screens.pickApp') : t('settings.screens.empty'))
                : (ids.length ? t('settings.screens.pickOne') : t('settings.screens.emptyApp'))}
            </Empty>
          )}
        </FormCol>
      </Split>
      <Row>
        <Button $variant="primary" onClick={save} disabled={busy || !dirty}>
          {busy ? <SpinnerRing size={14} thickness={2} /> : <Save size={14} />} {t('common.save')}
        </Button>
        <Button onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
          {busy ? <SpinnerRing size={14} thickness={2} /> : <RefreshCw size={14} />} {t('settings.pools.reloadFromDisk')}
        </Button>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
        {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
      </Row>
      <Hint>{t('settings.screens.hint')}</Hint>
    </Stack>
    </FrameworkEnumsContext.Provider>
  )
}
