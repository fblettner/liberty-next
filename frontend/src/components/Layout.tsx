import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth";

export function Layout() {
  const { user, logout } = useAuth();
  return (
    <div className="shell">
      <header className="topbar">
        <span className="brand">Liberty v2</span>
        <span className="row">
          <span className="muted">
            {user?.username}
            {user?.is_superuser ? " · superuser" : ""}
          </span>
          <button className="linkish" onClick={logout}>
            Sign out
          </button>
        </span>
      </header>
      <aside className="sidebar">
        <nav className="stack">
          <NavLink to="/" end>
            Connectors
          </NavLink>
          <NavLink to="/chat">Assistant</NavLink>
          {user?.is_superuser && <NavLink to="/settings">Settings</NavLink>}
          <h4>Reference</h4>
          <a href="/docs" target="_blank" rel="noreferrer">
            API docs ↗
          </a>
        </nav>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}
