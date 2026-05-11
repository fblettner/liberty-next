// "Current app" workspace state — a soft filter over the UI.
//
// In v2 auth is centralized (you log in once, app-agnostic) and connectors are
// already permission-scoped on the server; this just remembers which connector
// ("app" — nomasx1, nomajde, …) you're focused on so the Connectors page / nav
// don't show everything at once. Persisted to localStorage. No backend role —
// the permission checks are still what actually enforces access. It owns the
// one `GET /api/connectors` + `GET /api/menus` fetch, shared by the header
// picker, the Connectors page and the Sidebar (re-runnable via `refresh()`).
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
// Routes that "belong to" a connector — opening one makes the workspace follow it.
const CONNECTOR_ROUTE = /^\/(?:sql|http)\/([^/]+)\//

interface WorkspaceState {
  connectors: ConnectorMeta[] | null // null while loading / signed out
  menus: MenusByApp | null // app → its (permission-pruned, localized) menu tree
  error: string | null
  currentApp: string | null // the explicitly picked connector; null = all accessible
  currentMenu: AppMenuTree | null // the menu to show in the Sidebar (the picked app's, or — if there's only one connector — that one's)
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

  // If the remembered app isn't (or no longer is) one we can access, drop it.
  useEffect(() => {
    if (connectors && currentApp && !connectors.some((c) => c.name === currentApp)) {
      setCurrentAppState(null)
      writeApp(null)
    }
  }, [connectors, currentApp])

  // Deep-linking into a connector's screen (/sql/<c>/<q>, /http/<c>/<e>) makes the
  // workspace follow it — so "back to Connectors" stays scoped to that app.
  useEffect(() => {
    const m = CONNECTOR_ROUTE.exec(pathname)
    if (!m) return
    const name = decodeURIComponent(m[1])
    setCurrentAppState((cur) => (cur === name ? cur : name))
    writeApp(name)
  }, [pathname])

  const setCurrentApp = useCallback((name: string | null) => {
    setCurrentAppState(name)
    writeApp(name)
  }, [])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  // With exactly one connector there's no picker, so the Sidebar follows it implicitly.
  const currentMenu = useMemo<AppMenuTree | null>(() => {
    if (!menus) return null
    const app = currentApp ?? (connectors?.length === 1 ? connectors[0].name : null)
    return (app && menus[app]) || null
  }, [menus, connectors, currentApp])

  const value = useMemo<WorkspaceState>(
    () => ({ connectors, menus, error, currentApp, currentMenu, setCurrentApp, refresh }),
    [connectors, menus, error, currentApp, currentMenu, setCurrentApp, refresh],
  )
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(WorkspaceContext)
  if (!ctx) throw new Error('useWorkspace must be used within <WorkspaceProvider>')
  return ctx
}
