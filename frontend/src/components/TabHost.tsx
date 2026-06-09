// Renders every open tab's screen, stacked — only the active one is shown (the rest stay
// mounted but display:none, so each tab keeps its state when you switch away and back). The
// page chunks (TableView / HttpRunner) are lazy-loaded; a per-tab Suspense means opening a
// new tab doesn't flash the fallback over the ones already loaded. The whole host is hidden
// when the current route is the index landing or a framework page (Settings / nomaflow) — those
// render through <Outlet/> instead — but the tabs underneath stay mounted.
import { lazy, Suspense, useEffect, useState, type LazyExoticComponent, type ComponentType } from 'react'
import i18n from 'i18next'
import { Centered } from '../common'
import { useTabs } from '../tabs/TabsContext'
import { useWorkspace } from '../workspace/WorkspaceContext'

const TableView = lazy(() => import('../pages/TableView'))
const HttpRunner = lazy(() => import('../pages/HttpRunner'))
const DashboardView = lazy(() => import('../pages/DashboardView'))
const NomaflowRunDetail = lazy(() => import('../pages/Nomaflow/RunDetail'))
const Settings = lazy(() => import('../pages/Settings'))
const Monitoring = lazy(() => import('../pages/Monitoring'))

// `page` tabs (menus.toml type="page") → the framework page rendered inside the tab, keyed by its
// route path (the tab's ``target``). Kept in sync with src/tabs/pageTabs.ts (the path set) + App's
// TabRoute markers. A path with no entry falls back to a spinner (shouldn't happen for known pages).
const PAGE_COMPONENTS: Record<string, LazyExoticComponent<ComponentType>> = {
  '/nomaflow': lazy(() => import('../pages/Nomaflow')),
  '/nomaflow/schedule': lazy(() => import('../pages/Nomaflow/Schedule')),
  '/nomaflow/package': lazy(() => import('../pages/Nomaflow/PackagePage')),
  '/nomaflow/changes': lazy(() => import('../pages/Nomaflow/ChangesPage')),
  '/nomaflow/integrity': lazy(() => import('../pages/Nomaflow/IntegrityPage')),
  '/reports': lazy(() => import('../pages/Reports')),
}

// A ``screen`` tab opens a designed screen by id (``/screen/<app>/<id>``). We resolve the screen
// from the workspace catalog to get its connector + read query, then drive the TableView with the
// screen-id override so the exact screen's columns / dialog apply (even when another screen shares
// the same read query). A spinner shows until the catalog resolves it (or it's been deleted).
function ScreenTab({ app, screenId }: { app: string; screenId: string }) {
  const { findScreenById } = useWorkspace()
  const scr = findScreenById(app, screenId)
  if (!scr) return <Centered />
  return <TableView connector={scr.connector} query={scr.read_query} screenApp={app} screenId={screenId} />
}

export default function TabHost({ hidden }: { hidden: boolean }) {
  const { tabs, activeId } = useTabs()
  // Language nonce — bumped on every i18next ``languageChanged`` so every tab's
  // wrapper key changes, forcing the inner TableView / HttpRunner / etc. to
  // remount. The Layout's switchLang() also calls workspace.refresh() to pull
  // a fresh menu tree under the new Accept-Language; this remount handles the
  // already-loaded query results / column labels / dictionary-resolved cells
  // that the existing tab content cached under the previous language.
  const [langNonce, setLangNonce] = useState(0)
  useEffect(() => {
    const onChanged = () => setLangNonce((n) => n + 1)
    i18n.on('languageChanged', onChanged)
    return () => { i18n.off('languageChanged', onChanged) }
  }, [])
  if (tabs.length === 0) return null
  return (
    <div style={{ display: hidden ? 'none' : 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      {tabs.map((tab) => (
        <div
          key={`${tab.id}@${langNonce}`}
          style={{ display: tab.id === activeId ? 'flex' : 'none', flexDirection: 'column', flex: 1, minHeight: 0 }}
        >
          <Suspense fallback={<Centered />}>
            {tab.kind === 'http' ? (
              <HttpRunner connector={tab.connector} endpoint={tab.target} />
            ) : tab.kind === 'dashboard' ? (
              <DashboardView dashboardId={tab.target} />
            ) : tab.kind === 'nomaflow_run' ? (
              <NomaflowRunDetail runId={tab.target} />
            ) : tab.kind === 'settings' ? (
              <Settings />
            ) : tab.kind === 'monitoring' ? (
              <Monitoring />
            ) : tab.kind === 'page' ? (
              (() => { const P = PAGE_COMPONENTS[tab.target]; return P ? <P /> : <Centered /> })()
            ) : tab.kind === 'screen' ? (
              <ScreenTab app={tab.connector} screenId={tab.target} />
            ) : (
              <TableView connector={tab.connector} query={tab.target} />
            )}
          </Suspense>
        </div>
      ))}
    </div>
  )
}
