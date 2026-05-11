import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { Login } from "./components/Login";
import { OidcCallback } from "./components/OidcCallback";
import { Connectors } from "./components/Connectors";
import { TableView } from "./components/TableView";
import { HttpRunner } from "./components/HttpRunner";
import { Chat } from "./components/Chat";
import { Settings } from "./components/Settings";

function RequireAuth({ children }: { children: ReactNode }) {
  const { user, ready } = useAuth();
  const location = useLocation();
  if (!ready) return <div className="centered muted">Loading…</div>;
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
