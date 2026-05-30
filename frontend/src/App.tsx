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
const Nomaflow = lazy(() => import("./pages/Nomaflow"));
const NomaflowEditor = lazy(() => import("./pages/Nomaflow/JobEditor"));
const NomaflowSchedule = lazy(() => import("./pages/Nomaflow/Schedule"));
// NomaflowRunDetail is mounted by TabHost (workspace tab kind 'nomaflow_run'), not directly
// by a Route here — the `/nomaflow/runs/:runId` route uses TabRoute to add/activate the tab.

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
  const { openOrActivate } = useTabs();
  useEffect(() => {
    if (kind === "settings" || kind === "monitoring") {
      openOrActivate({ kind, connector: "", target: "" });
    } else if (kind === "nomaflow_run" && runId) {
      openOrActivate({ kind, connector: "", target: runId });
    } else if (kind === "dashboard" && target) {
      openOrActivate({ kind, connector: "", target });
    } else if (connector && target) {
      openOrActivate({ kind, connector, target });
    }
  }, [kind, connector, target, runId, openOrActivate]);
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
        <Route path="http/:connector/:target" element={<TabRoute kind="http" />} />
        <Route path="dashboard/:target" element={<TabRoute kind="dashboard" />} />
        <Route path="settings" element={<TabRoute kind="settings" />} />
        <Route path="monitoring" element={<TabRoute kind="monitoring" />} />
        {/* nomaflow feature area — Jobs list + Job editor + Schedule (NOMAFLOW-UI.md) */}
        <Route path="nomaflow" element={<Nomaflow />} />
        <Route path="nomaflow/jobs/new" element={<NomaflowEditor />} />
        <Route path="nomaflow/jobs/:id" element={<NomaflowEditor />} />
        <Route path="nomaflow/schedule" element={<NomaflowSchedule />} />
        {/* nomaflow run detail is hosted by TabHost as a `nomaflow_run` workspace tab — the
            TabRoute marker just opens/activates the tab; the actual rendering happens in
            TabHost so the run-detail page sits in the tab strip alongside Job Runs / Users / … */}
        <Route path="nomaflow/runs/:runId" element={<TabRoute kind="nomaflow_run" />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
