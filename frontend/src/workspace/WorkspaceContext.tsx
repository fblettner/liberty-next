// "Current app" workspace state — a soft filter over the UI.
//
// In v2 auth is centralized (you log in once, app-agnostic) and connectors are already
// permission-scoped on the server; this just remembers which *app* you're focused on so the
// Connectors page / nav don't show everything at once. "App" ≠ "connector": one v1 app can map to
// several v2 connectors (e.g. NOMAJDE → `nomajde` (its data DB) + `jdedwards` (the live JDE DB) +
// helper pools). The apps are the connectors that have a menu (`config/menus.toml` defines an app's
// screens); the rest are data sources reached *through* an app's menu, not picked directly. With no
// menus at all (a bare deployment) every connector counts as an app. Persisted to localStorage; no
// backend role — permission checks still enforce access. Owns the one `GET /api/connectors` +
// `GET /api/menus` fetch, shared by the header picker, the Connectors page and the Sidebar.
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { ConnectorMeta } from '../types/connectors'
import type { AppMenuTree, MenusByApp } from '../types/menus'
import type { ScreenListItem, ScreensByApp } from '../types/screens'
import { type LicenseInfo, RESTRICTED } from '../types/license'
import { useAuth } from '../auth/AuthContext'

const APP_KEY = 'liberty.app'
// Routes that "belong to" a connector — opening one makes the workspace follow it *iff* that
// connector is an app (so opening a screen on a data-source connector via an app's menu doesn't
// yank the workspace over to it).
const CONNECTOR_ROUTE = /^\/(?:sql|http)\/([^/]+)\//

interface WorkspaceState {
  connectors: ConnectorMeta[] | null // every accessible connector (null while loading / signed out)
  apps: ConnectorMeta[] | null // the subset that are "apps" (have a menu) — what the header picker offers
  menus: MenusByApp | null // app → its (permission-pruned, localized) menu tree
  screens: Record<string, ScreenListItem[]> | null // app → its accessible screens (list view; no dialog body)
  /** Every dashboard the caller may open (each carries label + widget list; widgets the caller can't
   *  read have already been filtered server-side). `null` while loading; `[]` when there are none. */
  dashboards: DashboardListItem[] | null
  license: LicenseInfo // `full` (licensed connectors loaded) or `restricted` (they weren't); defaults restricted
  error: string | null
  currentApp: string | null // the explicitly picked app; null = "(all apps)"
  currentMenu: AppMenuTree | null // the menu the Sidebar shows (the picked app's, or — with one app — that one's)
  setCurrentApp: (name: string | null) => void
  refresh: () => void
  /** Find the screen (across every app) whose effective connector + read_query match. Returns
   *  null when no screen / multiple matches found. The TableView uses this to decide whether to
   *  open a dialog on row click instead of going through the inline grid editor. */
  findScreen: (connector: string, readQuery: string) => ScreenListItem | null
  /** Look up a screen by its `(app, id)` pair — used by `NavigateAction` runtime to resolve
   *  a row-menu drill target ("open the roles screen filtered to this user") into the URL
   *  `/sql/{connector}/{read_query}` it should open. Returns null when the app or screen id is
   *  unknown to the current workspace. */
  findScreenById: (app: string, id: string) => ScreenListItem | null
}

/** Lightweight dashboard summary — id + label + description. Used by the sidebar to render a
 *  menu leaf's label without a second fetch. The widgets themselves come back on `GET
 *  /api/dashboards/{id}` (which the DashboardView page fetches on mount). */
export interface DashboardListItem {
  id: string
  label: string
  description?: string | null
}

const WorkspaceContext = createContext<WorkspaceState | null>(null)

function readApp(): string | null {
  try {
    const v = localStorage.getItem(APP_KEY)
    return v && v.trim() ? v : null
  } catch {
    return null
  }
}

function writeApp(name: string | null): void {
  try {
    if (name) localStorage.setItem(APP_KEY, name)
    else localStorage.removeItem(APP_KEY)
  } catch {
    /* ignore — non-fatal */
  }
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth()
  const { pathname } = useLocation()
  const navigate = useNavigate()
  const [connectors, setConnectors] = useState<ConnectorMeta[] | null>(null)
  const [menus, setMenus] = useState<MenusByApp | null>(null)
  const [screens, setScreens] = useState<Record<string, ScreenListItem[]> | null>(null)
  const [dashboards, setDashboards] = useState<DashboardListItem[] | null>(null)
  const [license, setLicense] = useState<LicenseInfo>(RESTRICTED)
  const [error, setError] = useState<string | null>(null)
  const [currentApp, setCurrentAppState] = useState<string | null>(readApp)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!ready || !user) {
      setConnectors(null)
      setMenus(null)
      setScreens(null)
      setDashboards(null)
      setLicense(RESTRICTED)
      setError(null)
      return
    }
    let cancelled = false
    setError(null)
    // the license is best-effort — a failure there shouldn't blank the workspace
    api.get<LicenseInfo>('/api/license').then((l) => { if (!cancelled) setLicense(l) }).catch(() => { if (!cancelled) setLicense(RESTRICTED) })
    Promise.all([
      api.get<{ connectors: ConnectorMeta[] }>('/api/connectors'),
      api.get<{ menus: MenusByApp }>('/api/menus'),
      // Screens / dashboards are best-effort: deployments without the matching config files
      // return empty payloads, and the UI gracefully degrades (the dialog runtime keeps its
      // inline grid editor; the sidebar's dashboard menu items collapse if their target
      // isn't in the catalog).
      api.get<ScreensByApp>('/api/screens').catch((): ScreensByApp => ({ screens: {} })),
      api.get<{ dashboards: DashboardListItem[] }>('/api/dashboards').catch(() => ({ dashboards: [] as DashboardListItem[] })),
    ])
      .then(([c, m, s, d]) => {
        if (cancelled) return
        setConnectors(c.connectors)
        setMenus(m.menus)
        setScreens(s.screens)
        setDashboards(d.dashboards)
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e))
      })
    return () => {
      cancelled = true
    }
  }, [ready, user, nonce])

  // "Apps" = connectors that have a menu; with no menus defined, every connector is an app.
  const apps = useMemo<ConnectorMeta[] | null>(() => {
    if (!connectors) return null
    const appNames = menus ? Object.keys(menus) : []
    return appNames.length ? connectors.filter((c) => appNames.includes(c.name)) : connectors
  }, [connectors, menus])
  const isApp = useCallback((name: string) => !!apps?.some((a) => a.name === name), [apps])

  // If the remembered app isn't (or no longer is) a pickable app, drop it.
  useEffect(() => {
    if (apps && currentApp && !isApp(currentApp)) {
      setCurrentAppState(null)
      writeApp(null)
    }
  }, [apps, currentApp, isApp])

  // Deep-linking into a screen (/sql/<c>/<q>, /http/<c>/<e>) makes the workspace follow it — but
  // only when <c> is an app; opening a data-source connector's screen (e.g. via the nomajde menu
  // pointing at a jdedwards query) leaves the picked app alone.
  useEffect(() => {
    const m = CONNECTOR_ROUTE.exec(pathname)
    if (!m) return
    const name = decodeURIComponent(m[1])
    if (!isApp(name)) return
    setCurrentAppState((cur) => (cur === name ? cur : name))
    writeApp(name)
  }, [pathname, isApp])

  const setCurrentApp = useCallback((name: string | null) => {
    setCurrentAppState(name)
    writeApp(name)
    // When the operator explicitly picks an app (the workspace picker → this callback) and
    // that app has a configured ``home_path``, navigate straight there. Cancel the redirect
    // when ``name == null`` (the "all apps" reset) — the operator stays on whatever page
    // they were on. The pickup-from-URL path (the CONNECTOR_ROUTE effect above) DOESN'T
    // call this, so deep-linking to /sql/nomasx1/x doesn't yank the page to /dashboard/…
    // — the implicit-follow stays on the URL the operator opened.
    if (name && menus && menus[name]?.home_path) {
      navigate(menus[name].home_path!)
    }
  }, [menus, navigate])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  // With exactly one app there's no picker, so the Sidebar follows it implicitly.
  const currentMenu = useMemo<AppMenuTree | null>(() => {
    if (!menus) return null
    const app = currentApp ?? (apps?.length === 1 ? apps[0].name : null)
    return (app && menus[app]) || null
  }, [menus, apps, currentApp])

  // A connector + read_query → ScreenListItem lookup. The map is rebuilt only when `screens` changes
  // (login, reload, refresh); same connector + same read query under several apps is ambiguous → null
  // (a screen referenced from two apps is a config bug, but pinging the user a 404 is safer than
  // silently guessing). Case-insensitive on both keys to match the runtime's resolver.
  const screenIndex = useMemo(() => {
    const idx = new Map<string, ScreenListItem | 'AMBIGUOUS'>()
    if (!screens) return idx
    for (const list of Object.values(screens)) {
      for (const s of list) {
        const k = `${s.connector.toLowerCase()}/${s.read_query.toLowerCase()}`
        idx.set(k, idx.has(k) ? 'AMBIGUOUS' : s)
      }
    }
    return idx
  }, [screens])
  const findScreen = useCallback(
    (connector: string, readQuery: string): ScreenListItem | null => {
      const hit = screenIndex.get(`${connector.toLowerCase()}/${readQuery.toLowerCase()}`)
      return hit && hit !== 'AMBIGUOUS' ? hit : null
    },
    [screenIndex],
  )
  // Forward index: app → id → ScreenListItem (case-insensitive on both keys to mirror findScreen).
  // Built lazily; cheap to walk the screens map even on the largest deployments (~hundreds of items).
  const findScreenById = useCallback(
    (app: string, id: string): ScreenListItem | null => {
      const appKey = Object.keys(screens ?? {}).find((k) => k.toLowerCase() === app.toLowerCase())
      const list = appKey ? screens?.[appKey] : null
      if (!list) return null
      return list.find((s) => s.id.toLowerCase() === id.toLowerCase()) ?? null
    },
    [screens],
  )

  const value = useMemo<WorkspaceState>(
    () => ({ connectors, apps, menus, screens, dashboards, license, error, currentApp, currentMenu, setCurrentApp, refresh, findScreen, findScreenById }),
    [connectors, apps, menus, screens, dashboards, license, error, currentApp, currentMenu, setCurrentApp, refresh, findScreen, findScreenById],
  )
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(WorkspaceContext)
  if (!ctx) throw new Error('useWorkspace must be used within <WorkspaceProvider>')
  return ctx
}
