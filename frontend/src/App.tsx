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

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <Centered />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

// A `/sql/:connector/:target` or `/http/:connector/:target` route — just opens/activates the
// tab for it; the TabHost (in Layout) does the actual rendering.
function TabRoute({ kind }: { kind: TabKind }) {
  const { connector = "", target = "" } = useParams();
  const { openOrActivate } = useTabs();
  useEffect(() => {
    if (connector && target) openOrActivate({ kind, connector, target });
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
        <Route path="chat" element={<Chat />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
