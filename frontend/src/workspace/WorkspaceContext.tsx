// "Current app" workspace state — a soft filter over the UI.
//
// In v2 auth is centralized (you log in once, app-agnostic) and connectors are
// already permission-scoped on the server; this just remembers which connector
// ("app" — nomasx1, nomajde, …) you're focused on so the Connectors page / nav
// don't show everything at once. Persisted to localStorage. No backend role —
// the permission checks are still what actually enforces access. It also owns
// the one `GET /api/connectors` fetch, shared by the header picker and the
// Connectors page (and re-runnable via `refresh()` after a config reload).
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { api, ApiError } from '../api/client'
import type { ConnectorMeta } from '../types/connectors'
import { useAuth } from '../auth/AuthContext'

const APP_KEY = 'liberty.app'

interface WorkspaceState {
  connectors: ConnectorMeta[] | null // null while loading / signed out
  error: string | null
  currentApp: string | null // null = all accessible connectors
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
  const [connectors, setConnectors] = useState<ConnectorMeta[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [currentApp, setCurrentAppState] = useState<string | null>(readApp)
  const [nonce, setNonce] = useState(0)

  useEffect(() => {
    if (!ready || !user) {
      setConnectors(null)
      setError(null)
      return
    }
    let cancelled = false
    setError(null)
    api
      .get<{ connectors: ConnectorMeta[] }>('/api/connectors')
      .then((r) => {
        if (!cancelled) setConnectors(r.connectors)
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

  const setCurrentApp = useCallback((name: string | null) => {
    setCurrentAppState(name)
    writeApp(name)
  }, [])

  const refresh = useCallback(() => setNonce((n) => n + 1), [])

  const value = useMemo<WorkspaceState>(
    () => ({ connectors, error, currentApp, setCurrentApp, refresh }),
    [connectors, error, currentApp, setCurrentApp, refresh],
  )
  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
}

export function useWorkspace(): WorkspaceState {
  const ctx = useContext(WorkspaceContext)
  if (!ctx) throw new Error('useWorkspace must be used within <WorkspaceProvider>')
  return ctx
}
