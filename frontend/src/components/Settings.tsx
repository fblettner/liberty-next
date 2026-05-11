import { useEffect, useState } from "react";
import { api, ApiError } from "../api";

interface ConfigDoc {
  path: string;
  content: string;
}

export function Settings() {
  const [doc, setDoc] = useState<ConfigDoc | null>(null);
  const [content, setContent] = useState("");
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api
      .get<ConfigDoc>("/admin/config/connectors")
      .then((d) => {
        setDoc(d);
        setContent(d.content);
      })
      .catch((e) => setError(e instanceof ApiError ? `${e.status === 403 ? "Superuser required" : e.message}` : String(e)));
  }, []);

  async function save() {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      await api.put<{ saved: boolean }>("/admin/config/connectors", { content });
      setDirty(false);
      setStatus("Saved. Click Reload to apply.");
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function reload() {
    setBusy(true);
    setError(null);
    setStatus(null);
    try {
      const r = await api.post<{ connectors: string[]; pools: string[] }>("/admin/reload");
      setStatus(`Reloaded. Connectors: ${r.connectors.join(", ") || "(none)"} · Pools: ${r.pools.join(", ") || "(none)"}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  if (error && !doc) return <div className="err">{error}</div>;
  if (!doc) return <div className="muted">Loading…</div>;

  return (
    <div className="stack">
      <h2 style={{ marginBottom: 0 }}>Connector config</h2>
      <p className="muted mono" style={{ marginTop: 4 }}>{doc.path}</p>
      <textarea
        className="mono"
        spellCheck={false}
        rows={24}
        value={content}
        onChange={(e) => {
          setContent(e.target.value);
          setDirty(e.target.value !== doc.content);
          setStatus(null);
        }}
      />
      <div className="row">
        <button className="btn" onClick={save} disabled={busy || !dirty}>
          {busy ? "…" : "Save"}
        </button>
        <button className="btn secondary" onClick={reload} disabled={busy || dirty}>
          {busy ? "…" : "Reload"}
        </button>
        {dirty && <span className="muted">unsaved changes</span>}
        {status && <span className="ok">{status}</span>}
        {error && <span className="err">{error}</span>}
      </div>
      <p className="muted" style={{ fontSize: 12 }}>
        Save validates the TOML before writing — invalid config is rejected. Reload rebuilds the connector registry; if the
        new config fails to load the previous one stays active.
      </p>
    </div>
  );
}
