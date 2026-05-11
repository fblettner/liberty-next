import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { ConnectorMeta, QueryResult, SqlQueryMeta } from "../types";

const PAGE_SIZE = 50;

function cellText(v: unknown): { text: string; isNull: boolean } {
  if (v === null || v === undefined) return { text: "null", isNull: true };
  if (typeof v === "object") return { text: JSON.stringify(v), isNull: false };
  return { text: String(v), isNull: false };
}

export function TableView() {
  const { connector = "", query = "" } = useParams();
  const [meta, setMeta] = useState<SqlQueryMeta | null>(null);
  const [metaErr, setMetaErr] = useState<string | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [result, setResult] = useState<QueryResult | null>(null);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(null);
  const [page, setPage] = useState(0);

  // Load the query's metadata (params, statement type, writable) from the filtered connector view.
  useEffect(() => {
    setMeta(null);
    setMetaErr(null);
    setResult(null);
    api
      .get<ConnectorMeta>(`/api/connectors/${encodeURIComponent(connector)}`)
      .then((c) => {
        if (c.type !== "sql") throw new Error(`${connector} is not a SQL connector`);
        const q = c.queries.find((x) => x.name === query);
        if (!q) throw new Error(`query ${query} not found on ${connector}`);
        setMeta(q);
        const init: Record<string, string> = {};
        for (const p of q.params) if (p.default != null) init[p.name] = p.default;
        setParams(init);
      })
      .catch((e) => setMetaErr(e instanceof ApiError ? e.message : String(e)));
  }, [connector, query]);

  const paramNames = useMemo(() => {
    if (!meta) return [] as string[];
    const seen = new Set<string>();
    const out: string[] = [];
    for (const n of [...meta.bind_params, ...meta.params.map((p) => p.name)]) {
      if (!seen.has(n)) {
        seen.add(n);
        out.push(n);
      }
    }
    return out;
  }, [meta]);

  const run = useCallback(async () => {
    if (!meta) return;
    if (meta.writable && !window.confirm(`Run the writable query "${connector}.${query}"? This may modify data.`)) return;
    setBusy(true);
    setRunErr(null);
    setSort(null);
    setPage(0);
    // Only send params the caller actually filled in (blank → omit → bound to SQL NULL).
    const sent: Record<string, string> = {};
    for (const [k, v] of Object.entries(params)) if (v !== "") sent[k] = v;
    try {
      let res: QueryResult;
      if (meta.statement_type === "SELECT") {
        const qs = new URLSearchParams(sent).toString();
        res = await api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}${qs ? `?${qs}` : ""}`);
      } else {
        res = await api.post<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}`, { params: sent });
      }
      setResult(res);
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [meta, params, connector, query]);

  const sortedRows = useMemo(() => {
    if (!result) return [];
    if (!sort) return result.rows;
    const { col, dir } = sort;
    return [...result.rows].sort((a, b) => {
      const av = a[col],
        bv = b[col];
      if (av === bv) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av < bv ? -1 : 1) * dir;
    });
  }, [result, sort]);

  const pageRows = sortedRows.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
  const pages = result ? Math.max(1, Math.ceil(result.rows.length / PAGE_SIZE)) : 1;

  if (metaErr) return <div className="err">{metaErr} — <Link to="/">back</Link></div>;
  if (!meta) return <div className="muted">Loading…</div>;

  return (
    <div className="stack">
      <h2 style={{ marginBottom: 0 }}>
        {connector}.{query} <span className="tag">{meta.statement_type}</span>
        {meta.writable && <span className="tag write">writable</span>}
      </h2>
      {(meta.label || meta.description) && <p className="muted" style={{ marginTop: 4 }}>{meta.label || meta.description}</p>}

      <div className="panel stack">
        {paramNames.length > 0 ? (
          <div className="row" style={{ alignItems: "flex-end" }}>
            {paramNames.map((name) => {
              const def = meta.params.find((p) => p.name === name);
              return (
                <div key={name}>
                  <label>{def?.label ?? name}</label>
                  <input
                    type="text"
                    placeholder={def?.default != null ? `default: ${def.default}` : "(null if blank)"}
                    value={params[name] ?? ""}
                    onChange={(e) => setParams((p) => ({ ...p, [name]: e.target.value }))}
                  />
                </div>
              );
            })}
            <button className="btn" onClick={run} disabled={busy}>
              {busy ? "Running…" : "Run"}
            </button>
          </div>
        ) : (
          <button className="btn" onClick={run} disabled={busy} style={{ alignSelf: "flex-start" }}>
            {busy ? "Running…" : "Run"}
          </button>
        )}
        {runErr && <div className="err">{runErr}</div>}
      </div>

      {result && result.statement_type === "SELECT" && (
        <div className="stack">
          <div className="row muted" style={{ fontSize: 12 }}>
            {result.row_count} row{result.row_count === 1 ? "" : "s"} · {result.duration_ms.toFixed(1)} ms
            {result.truncated && <span className="err"> · truncated to {result.rows.length}</span>}
          </div>
          {result.columns.length === 0 ? (
            <div className="muted">(no columns)</div>
          ) : (
            <>
              <div className="table-scroll">
                <table className="data">
                  <thead>
                    <tr>
                      {result.columns.map((c) => (
                        <th
                          key={c.name}
                          onClick={() =>
                            setSort((s) =>
                              s && s.col === c.name ? { col: c.name, dir: (s.dir * -1) as 1 | -1 } : { col: c.name, dir: 1 },
                            )
                          }
                          title={c.type ?? undefined}
                        >
                          {c.name}
                          {sort?.col === c.name ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((row, i) => (
                      <tr key={i}>
                        {result.columns.map((c) => {
                          const { text, isNull } = cellText(row[c.name]);
                          return <td key={c.name} className={isNull ? "null" : undefined}>{text}</td>;
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {pages > 1 && (
                <div className="row">
                  <button className="btn secondary" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                    ‹ Prev
                  </button>
                  <span className="muted">
                    Page {page + 1} / {pages}
                  </span>
                  <button className="btn secondary" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>
                    Next ›
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}

      {result && result.statement_type !== "SELECT" && (
        <div className="panel">
          <span className="ok">{result.statement_type} OK</span> — {result.rowcount} row{result.rowcount === 1 ? "" : "s"} affected ·{" "}
          {result.duration_ms.toFixed(1)} ms
        </div>
      )}
    </div>
  );
}
