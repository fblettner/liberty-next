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
import { useLocation } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { ConnectorMeta } from '../types/connectors'
import type { AppMenuTree, MenusByApp } from '../types/menus'
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
  error: string | null
  currentApp: string | null // the explicitly picked app; null = "(all apps)"
  currentMenu: AppMenuTree | null // the menu the Sidebar shows (the picked app's, or — with one app — that one's)
  setCurrentApp: (name: string | null) => void
  refresh: () => void
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
  const [connectors, setConnectors] = useState<ConnectorMeta[] | null>(null)
  const [menus, setMenus] = useState<MenusByApp | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [currentApp, setCurrentAppState] = useState<string | null>(readApp)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!ready || !user) {
      setConnectors(null)
      setMenus(null)
      setError(null)
      return
    }
    let cancelled = false
    setError(null)
    Promise.all([
      api.get<{ connectors: ConnectorMeta[] }>('/api/connectors'),
      api.get<{ menus: MenusByApp }>('/api/menus'),
    ])
      .then(([c, m]) => {
        if (cancelled) return
        setConnectors(c.connectors)
        setMenus(m.menus)
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
  }, [])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  // With exactly one app there's no picker, so the Sidebar follows it implicitly.
  const currentMenu = useMemo<AppMenuTree | null>(() => {
    if (!menus) return null
    const app = currentApp ?? (apps?.length === 1 ? apps[0].name : null)
    return (app && menus[app]) || null
  }, [menus, apps, currentApp])

  const value = useMemo<WorkspaceState>(
    () => ({ connectors, apps, menus, error, currentApp, currentMenu, setCurrentApp, refresh }),
    [connectors, apps, menus, error, currentApp, currentMenu, setCurrentApp, refresh],
  )
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(WorkspaceContext)
  if (!ctx) throw new Error('useWorkspace must be used within <WorkspaceProvider>')
  return ctx
}
