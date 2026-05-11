import { lazy } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./auth/AuthContext";
import { Centered } from "./common";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import OidcCallback from "./pages/OidcCallback";

// Page components are code-split — each becomes its own chunk, so the heavy libs
// they pull in (react-table, react-markdown, the Monaco loader) only load when
// their page is visited. The shell (Layout/Login/OidcCallback) stays eager.
const Connectors = lazy(() => import("./pages/Connectors"));
const TableView = lazy(() => import("./pages/TableView"));
const HttpRunner = lazy(() => import("./pages/HttpRunner"));
const Chat = lazy(() => import("./pages/Chat"));
const Settings = lazy(() => import("./pages/Settings"));

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <Centered />;
  if (!user) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
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
        <Route path="sql/:connector/:query" element={<TableView />} />
        <Route path="http/:connector/:endpoint" element={<HttpRunner />} />
        <Route path="chat" element={<Chat />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
