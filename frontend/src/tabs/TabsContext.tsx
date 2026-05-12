// The open-tabs workspace — like VS Code / Postman / DBeaver: each `/sql/<c>/<q>` or
// `/http/<c>/<e>` screen you open from the menu becomes a tab in the content area; opening
// the same screen again just reactivates its tab; switching tabs keeps every tab's component
// mounted (TabHost just hides the inactive ones), so each tab's state — scroll, sort, filters,
// the loaded result, an in-progress edit — is preserved. The framework pages (Connectors /
// Assistant / Settings) are NOT tabs (separate phase / a drawer for AI). The set + the active
// tab persist to sessionStorage, so a page refresh restores them.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'

export type TabKind = 'sql' | 'http'
export interface Tab {
  id: string // `${kind}:${connector}:${target}` — stable, so opening the same screen reactivates it
  kind: TabKind
  connector: string
  target: string // the query name (sql) or endpoint name (http)
}
export function tabId(kind: TabKind, connector: string, target: string): string {
  return `${kind}:${connector}:${target}`
}
export function tabPath(t: Pick<Tab, 'kind' | 'connector' | 'target'>): string {
  return `/${t.kind}/${encodeURIComponent(t.connector)}/${encodeURIComponent(t.target)}`
}

interface TabsState {
  tabs: Tab[]
  activeId: string | null
  openOrActivate: (t: Omit<Tab, 'id'>) => void
  setActive: (id: string) => void
  close: (id: string) => void
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

  const openOrActivate = useCallback((t: Omit<Tab, 'id'>) => {
    const id = tabId(t.kind, t.connector, t.target)
    setS((s) =>
      s.activeId === id ? s : s.tabs.some((x) => x.id === id) ? { ...s, activeId: id } : { tabs: [...s.tabs, { ...t, id }], activeId: id },
    )
  }, [])
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

  const value = useMemo<TabsState>(() => ({ tabs, activeId, openOrActivate, setActive, close }), [tabs, activeId, openOrActivate, setActive, close])
  return <TabsContext.Provider value={value}>{children}</TabsContext.Provider>
}

export function useTabs(): TabsState {
  const ctx = useContext(TabsContext)
  if (!ctx) throw new Error('useTabs must be used within <TabsProvider>')
  return ctx
}
