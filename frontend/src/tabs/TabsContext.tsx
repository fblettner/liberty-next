// The open-tabs workspace — like VS Code / Postman / DBeaver: each `/sql/<c>/<q>` or
// `/http/<c>/<e>` screen you open from the menu becomes a tab in the content area; opening
// the same screen again just reactivates its tab; switching tabs keeps every tab's component
// mounted (TabHost just hides the inactive ones), so each tab's state — scroll, sort, filters,
// the loaded result, an in-progress edit — is preserved. The framework pages (Connectors /
// Assistant / Settings) are NOT tabs (separate phase / a drawer for AI). The set + the active
// tab persist to sessionStorage, so a page refresh restores them.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode, useRef } from 'react'
import { useBranding } from '../branding/BrandingContext'
import { useWorkspace } from '../workspace/WorkspaceContext'

// The set of "tab kinds" the workspace knows how to host. Adding a new one means three coordinated
// edits: the union here, the path builder below, and the TabHost render switch. ``nomaflow_run``
// is the first non-SQL/HTTP page wrapped as a tab — opened from row_click_route on the Job Runs
// screen so the run detail lives alongside Job Runs / Users / etc. in the tab strip instead of
// hijacking the workspace or popping a browser tab.
// 'screen' opens a designed screen by id (``/screen/<app>/<screen_id>``); the tab's ``connector``
// segment carries the screen's APP and ``target`` its id, so two screens sharing a read query get
// distinct tabs (a plain 'sql' tab keys on connector+query and would collide).
// 'page' wraps a framework page (a menus.toml ``type = "page"`` target — nomaflow Jobs / Schedule /
// Package / Changes / Integrity, the Reports list) as a tab, so clicking a page menu item opens a
// tab instead of replacing the active tab's content. The tab's ``target`` IS the route path; the
// path → component map lives in TabHost (see src/tabs/pageTabs.ts for the shared path set).
export type TabKind = 'sql' | 'screen' | 'http' | 'dashboard' | 'nomaflow_run' | 'settings' | 'monitoring' | 'page' | 'job'
export interface Tab {
  id: string // `${kind}:${connector}:${target}` — stable, so opening the same screen reactivates it
  kind: TabKind
  /** Empty for dashboard / nomaflow_run / settings tabs (the target alone identifies them). */
  connector: string
  /** The query name (sql), endpoint name (http), dashboard id (dashboard), run_id (nomaflow_run);
   *  empty for the single settings tab. */
  target: string
}
export function tabId(kind: TabKind, connector: string, target: string): string {
  return `${kind}:${connector}:${target}`
}
export function tabPath(t: Pick<Tab, 'kind' | 'connector' | 'target'>): string {
  // Settings / Monitoring are singleton framework tabs with no connector/target segment.
  if (t.kind === 'settings') return '/settings'
  if (t.kind === 'monitoring') return '/monitoring'
  // A page tab's target IS its route path (e.g. /nomaflow/changes).
  if (t.kind === 'page') return t.target
  // Dashboards / nomaflow_run have no connector segment in the URL — just /<base>/<id>.
  if (t.kind === 'dashboard') return `/dashboard/${encodeURIComponent(t.target)}`
  // A job tab: the job id (a menu item of type ``job``), run from its own page.
  if (t.kind === 'job') return `/job/${encodeURIComponent(t.target)}`
  if (t.kind === 'nomaflow_run') return `/nomaflow/runs/${encodeURIComponent(t.target)}`
  return `/${t.kind}/${encodeURIComponent(t.connector)}/${encodeURIComponent(t.target)}`
}

/** The application a tab belongs to: the connector of a screen / query / endpoint tab, the scope
 *  of a dashboard id (``nomajde.overview``), the app of an Explorer model page; null for the
 *  framework tabs (settings, monitoring, a plain page) — those belong to no application. A job tab
 *  names no app: it is treated as the other application's and closes on a switch. */
export function tabApp(t: Pick<Tab, 'kind' | 'connector' | 'target'>): string | null {
  if (t.kind === 'settings' || t.kind === 'monitoring' || t.kind === 'nomaflow_run') return null
  if (t.kind === 'dashboard') return t.target.includes('.') ? t.target.split('.')[0] : t.target
  if (t.kind === 'job') return ''
  if (t.kind === 'page') {
    const m = /^\/explorer\/([^/]+)\//.exec(t.target)
    return m ? decodeURIComponent(m[1]) : null
  }
  return t.connector
}

interface TabsState {
  tabs: Tab[]
  activeId: string | null
  openOrActivate: (t: Omit<Tab, 'id'>) => void
  setActive: (id: string) => void
  close: (id: string) => void
  closeAll: () => void
}
const TabsContext = createContext<TabsState | null>(null)

const KEY = 'liberty.tabs'
function load(): { tabs: Tab[]; activeId: string | null } {
  try {
    const v = JSON.parse(sessionStorage.getItem(KEY) ?? 'null')
    if (v && Array.isArray(v.tabs) && v.tabs.every((t: unknown) => t && typeof (t as Tab).id === 'string')) {
      return { tabs: v.tabs as Tab[], activeId: typeof v.activeId === 'string' ? v.activeId : null }
    }
  } catch { /* ignore */ }
  return { tabs: [], activeId: null }
}

export function TabsProvider({ children }: { children: ReactNode }) {
  const [{ tabs, activeId }, setS] = useState(load)
  useEffect(() => {
    try { sessionStorage.setItem(KEY, JSON.stringify({ tabs, activeId })) } catch { /* ignore */ }
  }, [tabs, activeId])

  // The operator's tab policy ([app] in app.toml, served with the theme): one page at a time,
  // a cap on the open tabs (the oldest closes), the tabs closed on an app switch.
  const { ui } = useBranding()
  const { appSwitches, currentApp } = useWorkspace()
  const multi = ui.tabs_multi !== false
  const max = Math.max(1, ui.tabs_max || 5)
  const openOrActivate = useCallback((t: Omit<Tab, 'id'>) => {
    const id = tabId(t.kind, t.connector, t.target)
    setS((s) => {
      if (s.activeId === id) return s
      if (s.tabs.some((x) => x.id === id)) return { ...s, activeId: id }
      if (!multi) return { tabs: [{ ...t, id }], activeId: id }
      const kept = s.tabs.length >= max ? s.tabs.slice(s.tabs.length - max + 1) : s.tabs
      return { tabs: [...kept, { ...t, id }], activeId: id }
    })
  }, [multi, max])
  const switchesSeen = useRef(appSwitches)
  useEffect(() => {
    if (switchesSeen.current === appSwitches) return
    switchesSeen.current = appSwitches
    if (!ui.tabs_close_on_app_switch || !currentApp) return
    // every tab of another application closes; the framework tabs (settings, monitoring) and the
    // tabs of the picked app (its home, opened by the switch itself) stay
    setS((s) => {
      const kept = s.tabs.filter((x) => tabApp(x) === null || tabApp(x) === currentApp)
      if (kept.length === s.tabs.length) return s
      const activeId = kept.some((x) => x.id === s.activeId) ? s.activeId : (kept[kept.length - 1]?.id ?? null)
      return { tabs: kept, activeId }
    })
  }, [appSwitches, currentApp, ui.tabs_close_on_app_switch])
  const setActive = useCallback((id: string) => setS((s) => (s.activeId === id || !s.tabs.some((x) => x.id === id) ? s : { ...s, activeId: id })), [])
  const close = useCallback((id: string) => {
    setS((s) => {
      const i = s.tabs.findIndex((x) => x.id === id)
      if (i < 0) return s
      const remaining = s.tabs.filter((x) => x.id !== id)
      const activeId = s.activeId === id ? (remaining[Math.min(i, remaining.length - 1)]?.id ?? null) : s.activeId
      return { tabs: remaining, activeId }
    })
  }, [])
  const closeAll = useCallback(() => setS((s) => (s.tabs.length === 0 ? s : { tabs: [], activeId: null })), [])

  const value = useMemo<TabsState>(() => ({ tabs, activeId, openOrActivate, setActive, close, closeAll }), [tabs, activeId, openOrActivate, setActive, close, closeAll])
  return <TabsContext.Provider value={value}>{children}</TabsContext.Provider>
}

export function useTabs(): TabsState {
  const ctx = useContext(TabsContext)
  if (!ctx) throw new Error('useTabs must be used within <TabsProvider>')
  return ctx
}
