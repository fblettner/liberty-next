import { lazy, useEffect } from "react";
import { Navigate, Route, Routes, useLocation, useParams } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./auth/AuthContext";
import { Centered } from "./common";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import OidcCallback from "./pages/OidcCallback";
import { useTabs, type TabKind } from "./tabs/TabsContext";

// Framework pages are code-split; the SQL/HTTP screens aren't routed directly — they live as
// tabs (see components/TabHost), and these route components just open/activate the matching tab.
const Connectors = lazy(() => import("./pages/Connectors"));
const Chat = lazy(() => import("./pages/Chat"));
const Settings = lazy(() => import("./pages/Settings"));
const Nomaflow = lazy(() => import("./pages/Nomaflow"));
const NomaflowEditor = lazy(() => import("./pages/Nomaflow/JobEditor"));
const NomaflowSchedule = lazy(() => import("./pages/Nomaflow/Schedule"));

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <Centered />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

// A `/sql/:connector/:target` or `/http/:connector/:target` route — just opens/activates the
// tab for it; the TabHost (in Layout) does the actual rendering. Dashboards live at
// `/dashboard/:target` (no connector segment in the URL); we open them as dashboard-kind tabs.
function TabRoute({ kind }: { kind: TabKind }) {
  const { connector = "", target = "" } = useParams();
  const { openOrActivate } = useTabs();
  useEffect(() => {
    if (kind === "dashboard" && target) {
      openOrActivate({ kind, connector: "", target });
    } else if (connector && target) {
      openOrActivate({ kind, connector, target });
    }
  }, [kind, connector, target, openOrActivate]);
  return null;
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
        <Route index element={<Connectors />} />
        <Route path="sql/:connector/:target" element={<TabRoute kind="sql" />} />
        <Route path="http/:connector/:target" element={<TabRoute kind="http" />} />
        <Route path="dashboard/:target" element={<TabRoute kind="dashboard" />} />
        <Route path="chat" element={<Chat />} />
        <Route path="settings" element={<Settings />} />
        {/* nomaflow feature area — Jobs list + Job editor + Schedule (NOMAFLOW-UI.md) */}
        <Route path="nomaflow" element={<Nomaflow />} />
        <Route path="nomaflow/jobs/new" element={<NomaflowEditor />} />
        <Route path="nomaflow/jobs/:id" element={<NomaflowEditor />} />
        <Route path="nomaflow/schedule" element={<NomaflowSchedule />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
