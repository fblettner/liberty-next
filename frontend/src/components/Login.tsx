import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../auth";
import { ApiError } from "../api";

export function Login() {
  const { user, ready, login, oidcLogin } = useAuth();
  const location = useLocation();
  const from = (location.state as { from?: string } | null)?.from ?? "/";
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (ready && user) return <Navigate to={from} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="centered">
      <form className="panel login-card stack" onSubmit={submit}>
        <h2 style={{ margin: 0 }}>Sign in</h2>
        <div>
          <label htmlFor="u">Username</label>
          <input id="u" type="text" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} style={{ width: "100%" }} />
        </div>
        <div>
          <label htmlFor="p">Password</label>
          <input id="p" type="password" value={password} onChange={(e) => setPassword(e.target.value)} style={{ width: "100%" }} />
        </div>
        {error && <div className="err">{error}</div>}
        <button className="btn" type="submit" disabled={busy || !username || !password}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <button className="btn secondary" type="button" onClick={oidcLogin}>
          Sign in with OIDC
        </button>
        <div className="muted" style={{ fontSize: 12 }}>
          OIDC must be enabled in <code>config/app.toml</code>; otherwise that button 404s.
        </div>
      </form>
    </div>
  );
}
