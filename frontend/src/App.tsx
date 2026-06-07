import { lazy, useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./auth/AuthContext";
import { Centered } from "./common";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import OidcCallback from "./pages/OidcCallback";
import { useTabs, type TabKind } from "./tabs/TabsContext";
import { useWorkspace } from "./workspace/WorkspaceContext";

// Framework pages are code-split; the SQL/HTTP screens aren't routed directly — they live as
// tabs (see components/TabHost), and these route components just open/activate the matching tab.
// Settings is also a tab now (its <Settings> lazy-loads inside TabHost); /settings is a TabRoute
// marker like the screen routes. AI assistant is no longer a routed page — it lives in a
// right-side drawer toggled from the utility bar (see components/AiDrawer). The /chat URL is gone.
const NomaflowEditor = lazy(() => import("./pages/Nomaflow/JobEditor"));
// nomaflow Jobs / Schedule / Package / Changes / Integrity and the Reports list are hosted as
// `page` workspace TABS (TabHost's path → component registry, src/tabs/pageTabs.ts), so clicking a
// page menu item opens a tab instead of replacing the active tab's content. Their routes below are
// TabRoute markers. The job editor stays a direct Outlet page (transient, reached from a button);
// NomaflowRunDetail is its own `nomaflow_run` tab.

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <Centered />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

// A `/sql/:connector/:target` or `/http/:connector/:target` route — just opens/activates the
// tab for it; the TabHost (in Layout) does the actual rendering. Dashboards live at
// `/dashboard/:target` (no connector segment in the URL); nomaflow run detail lives at
// `/nomaflow/runs/:runId` and uses the route param as the tab target. All non-SQL/HTTP kinds
// reuse the same TabRoute marker pattern — the only divergence is which URL param feeds
// ``target``.
function TabRoute({ kind }: { kind: TabKind }) {
  const { connector = "", target = "", runId = "" } = useParams();
  const { pathname } = useLocation();
  const { openOrActivate } = useTabs();
  useEffect(() => {
    if (kind === "page") {
      // The route path IS the page tab's target (one tab per page path).
      openOrActivate({ kind, connector: "", target: pathname });
    } else if (kind === "settings" || kind === "monitoring") {
      openOrActivate({ kind, connector: "", target: "" });
    } else if (kind === "nomaflow_run" && runId) {
      openOrActivate({ kind, connector: "", target: runId });
    } else if (kind === "dashboard" && target) {
      openOrActivate({ kind, connector: "", target });
    } else if (connector && target) {
      openOrActivate({ kind, connector, target });
    }
  }, [kind, connector, target, runId, pathname, openOrActivate]);
  return null;
}

// Index landing ("/") — the app's resting state when no tab is open. Closing all tabs / the last
// tab navigates here. If the current app has a configured home page (menus.toml `home` → its
// resolved `home_path`, e.g. /dashboard/<id>), redirect there; otherwise a blank content area
// (the sidebar + tab strip's app title still frame it). The Connectors landing page was removed —
// connectors are managed and queried entirely from Settings now.
function Home() {
  const { menus, apps, currentApp } = useWorkspace();
  if (!menus) return <Centered />; // still loading — don't flash blank, then redirect
  const app = currentApp ?? (apps?.length === 1 ? apps[0].name : null);
  const home = app ? menus[app]?.home_path : null;
  if (home) return <Navigate to={home} replace />;
  return null; // blank content area
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/oidc/callback" element={<OidcCallback />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      >
        <Route index element={<Home />} />
        <Route path="sql/:connector/:target" element={<TabRoute kind="sql" />} />
        {/* A designed screen by id — ``:connector`` carries the screen's app, ``:target`` its id. */}
        <Route path="screen/:connector/:target" element={<TabRoute kind="screen" />} />
        <Route path="http/:connector/:target" element={<TabRoute kind="http" />} />
        <Route path="dashboard/:target" element={<TabRoute kind="dashboard" />} />
        <Route path="settings" element={<TabRoute kind="settings" />} />
        <Route path="monitoring" element={<TabRoute kind="monitoring" />} />
        {/* nomaflow feature area — Jobs / Schedule / Package / Changes / Integrity open as `page`
            tabs (TabHost renders them); only the Job editor is a direct Outlet page. */}
        <Route path="nomaflow" element={<TabRoute kind="page" />} />
        <Route path="nomaflow/jobs/new" element={<NomaflowEditor />} />
        <Route path="nomaflow/jobs/:id" element={<NomaflowEditor />} />
        <Route path="nomaflow/schedule" element={<TabRoute kind="page" />} />
        <Route path="nomaflow/package" element={<TabRoute kind="page" />} />
        <Route path="nomaflow/changes" element={<TabRoute kind="page" />} />
        <Route path="nomaflow/integrity" element={<TabRoute kind="page" />} />
        {/* nomaflow run detail is hosted by TabHost as a `nomaflow_run` workspace tab — the
            TabRoute marker just opens/activates the tab; the actual rendering happens in
            TabHost so the run-detail page sits in the tab strip alongside Job Runs / Users / … */}
        <Route path="nomaflow/runs/:runId" element={<TabRoute kind="nomaflow_run" />} />
        {/* reports feature area — flat list of available reports + run dialog.
            Operators add a menu entry of type='page' target='/reports' to surface
            it in their sidebar; the API endpoints live in liberty/web/reports.py. */}
        <Route path="reports" element={<TabRoute kind="page" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
