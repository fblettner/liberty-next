// Renders every open tab's screen, stacked — only the active one is shown (the rest stay
// mounted but display:none, so each tab keeps its state when you switch away and back). The
// page chunks (TableView / HttpRunner) are lazy-loaded; a per-tab Suspense means opening a
// new tab doesn't flash the fallback over the ones already loaded. The whole host is hidden
// when the current route is a framework page (Connectors / Chat / Settings) — those render
// through <Outlet/> instead — but the tabs underneath stay mounted.
import { lazy, Suspense, useEffect, useState } from 'react'
import i18n from 'i18next'
import { Centered } from '../common'
import { useTabs } from '../tabs/TabsContext'

const TableView = lazy(() => import('../pages/TableView'))
const HttpRunner = lazy(() => import('../pages/HttpRunner'))
const DashboardView = lazy(() => import('../pages/DashboardView'))
const NomaflowRunDetail = lazy(() => import('../pages/Nomaflow/RunDetail'))

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
            ) : (
              <TableView connector={tab.connector} query={tab.target} />
            )}
          </Suspense>
        </div>
      ))}
    </div>
  )
}
