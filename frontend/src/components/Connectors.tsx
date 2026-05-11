import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError } from "../api";
import type { ConnectorMeta } from "../types";

export function Connectors() {
  const [connectors, setConnectors] = useState<ConnectorMeta[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .get<{ connectors: ConnectorMeta[] }>("/api/connectors")
      .then((r) => setConnectors(r.connectors))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)));
  }, []);

  if (error) return <div className="err">{error}</div>;
  if (!connectors) return <div className="muted">Loading connectors…</div>;
  if (connectors.length === 0)
    return (
      <div>
        <h2>Connectors</h2>
        <p className="muted">No connectors are accessible with your permissions.</p>
      </div>
    );

  return (
    <div>
      <h2>Connectors</h2>
      <p className="muted">Queries and endpoints you may run, discovered from the server's config.</p>
      {connectors.map((c) => (
        <div key={c.name} className="panel conn-card">
          <h3>
            {c.name} <span className="tag">{c.type}</span>
            {c.type === "api" && c.base_url ? <span className="muted mono"> {c.base_url}</span> : null}
          </h3>
          <ul className="item-list">
            {c.type === "sql" &&
              c.queries.map((q) => (
                <li key={q.name}>
                  <span className="name">
                    <Link to={`/sql/${encodeURIComponent(c.name)}/${encodeURIComponent(q.name)}`}>{q.name}</Link>
                  </span>
                  <span className="tag">{q.statement_type}</span>
                  {q.writable && <span className="tag write">writable</span>}
                  <span className="muted">{q.label ?? q.description ?? ""}</span>
                </li>
              ))}
            {c.type === "api" &&
              c.endpoints.map((e) => (
                <li key={e.name}>
                  <span className="name">
                    <Link to={`/http/${encodeURIComponent(c.name)}/${encodeURIComponent(e.name)}`}>{e.name}</Link>
                  </span>
                  <span className="tag">{e.method}</span>
                  <span className="muted mono">{e.path}</span>
                  <span className="muted">{e.label ?? e.description ?? ""}</span>
                </li>
              ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
