import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { ApiEndpointMeta, ApiResult, ConnectorMeta } from "../types";

export function HttpRunner() {
  const { connector = "", endpoint = "" } = useParams();
  const [meta, setMeta] = useState<ApiEndpointMeta | null>(null);
  const [metaErr, setMetaErr] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ApiResult | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setMeta(null);
    setMetaErr(null);
    setResult(null);
    api
      .get<ConnectorMeta>(`/api/connectors/${encodeURIComponent(connector)}`)
      .then((c) => {
        if (c.type !== "api") throw new Error(`${connector} is not an API connector`);
        const e = c.endpoints.find((x) => x.name === endpoint);
        if (!e) throw new Error(`endpoint ${endpoint} not found on ${connector}`);
        setMeta(e);
        const init: Record<string, string> = {};
        for (const p of e.params) if (p.default != null) init[p.name] = p.default;
        setParams(init);
      })
      .catch((e) => setMetaErr(e instanceof ApiError ? e.message : String(e)));
  }, [connector, endpoint]);

  const call = useCallback(async () => {
    setBusy(true);
    setRunErr(null);
    const sent: Record<string, string> = {};
    for (const [k, v] of Object.entries(params)) if (v !== "") sent[k] = v;
    try {
      setResult(await api.post<ApiResult>(`/api/http/${encodeURIComponent(connector)}/${encodeURIComponent(endpoint)}`, { params: sent }));
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [params, connector, endpoint]);

  if (metaErr) return <div className="err">{metaErr} — <Link to="/">back</Link></div>;
  if (!meta) return <div className="muted">Loading…</div>;

  return (
    <div className="stack">
      <h2 style={{ marginBottom: 0 }}>
        {connector}.{endpoint} <span className="tag">{meta.method}</span> <span className="muted mono">{meta.path}</span>
      </h2>
      {(meta.label || meta.description) && <p className="muted" style={{ marginTop: 4 }}>{meta.label || meta.description}</p>}

      <div className="panel stack">
        {meta.params.length > 0 && (
          <div className="row" style={{ alignItems: "flex-end" }}>
            {meta.params.map((p) => (
              <div key={p.name}>
                <label>{p.label ?? p.name}</label>
                <input
                  type="text"
                  placeholder={p.default != null ? `default: ${p.default}` : ""}
                  value={params[p.name] ?? ""}
                  onChange={(e) => setParams((s) => ({ ...s, [p.name]: e.target.value }))}
                />
              </div>
            ))}
          </div>
        )}
        <button className="btn" onClick={call} disabled={busy} style={{ alignSelf: "flex-start" }}>
          {busy ? "Calling…" : "Call"}
        </button>
        {runErr && <div className="err">{runErr}</div>}
      </div>

      {result && (
        <div className="stack">
          <div className="row" style={{ fontSize: 12 }}>
            <span className={result.success ? "ok" : "err"}>
              {result.success ? "OK" : "FAILED"} · HTTP {result.status_code}
            </span>
            <span className="muted mono">{result.url}</span>
            <span className="muted">{result.duration_ms.toFixed(1)} ms</span>
          </div>
          {result.error && <div className="err">{result.error}</div>}
          {result.extracted !== null && result.extracted !== undefined && (
            <div>
              <label>response_field →</label>
              <code>{JSON.stringify(result.extracted)}</code>
            </div>
          )}
          <pre className="json">{JSON.stringify(result.json ?? result.body, null, 2)}</pre>
        </div>
      )}
    </div>
  );
}
