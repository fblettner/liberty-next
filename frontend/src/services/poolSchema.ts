// Fetches + caches the pool-schema introspection for a SQL connector (the table/column catalog
// that powers the SQL editor's autocomplete + the wizard's table picker). One-shot per-connector,
// kept in memory for the session — the catalog rarely changes in a day of operator work, and a
// failed admin reload re-mounts the editor anyway. Backend: GET /api/sql/{c}/_schema (superuser
// only — see liberty/web/connectors.py). The fetcher tolerates 4xx/5xx silently, so a connector
// the operator isn't allowed to introspect (or one whose pool can't open) just falls back to no
// suggestions instead of throwing.
import { api, ApiError } from '../api/client'

export interface PoolColumn {
  name: string
  type?: string
  nullable?: boolean
}

export interface PoolTable {
  name: string
  schema?: string
  kind: 'table' | 'view'
  columns: PoolColumn[]
}

export interface PoolSchema {
  pool: string
  dialect: string
  tables: PoolTable[]
  truncated: boolean
}

// Session cache. Key = connector name. Value = a settled Promise so multiple consumers
// (autocomplete provider + wizard panel) share the one request.
const cache = new Map<string, Promise<PoolSchema | null>>()

export function getPoolSchema(connector: string): Promise<PoolSchema | null> {
  let p = cache.get(connector)
  if (!p) {
    p = api
      .get<PoolSchema>(`/api/sql/${encodeURIComponent(connector)}/_schema`)
      .catch((e) => {
        // 403 (non-superuser), 404 (unknown connector), 502 (pool unreachable) — degrade gracefully.
        if (e instanceof ApiError) return null
        throw e
      })
    cache.set(connector, p)
  }
  return p
}

/** Drop a connector's cached schema — call after a /admin/reload or when the operator wants
 *  to pick up a freshly-added table without reloading the page. */
export function invalidatePoolSchema(connector?: string): void {
  if (connector === undefined) cache.clear()
  else cache.delete(connector)
}

/** Helper: flatten the table list into a `name → PoolTable` map (case-insensitive lookup, since
 *  DB identifier folding varies — Postgres → lower, Oracle → upper). The match prefers an exact
 *  case hit then falls back to lower-case. Returns undefined when nothing matches. */
export function findTable(schema: PoolSchema, name: string): PoolTable | undefined {
  if (!name) return undefined
  const exact = schema.tables.find((t) => t.name === name)
  if (exact) return exact
  const low = name.toLowerCase()
  return schema.tables.find((t) => t.name.toLowerCase() === low)
}

/** Walk an arbitrary SQL string for `FROM <ident>` / `JOIN <ident>` patterns and return the first
 *  identifier that matches a table on *schema*. Used by the SQL editor's wizard to pre-seed the
 *  table picker from the query being edited — for the v2-migrated wrapper `SELECT * FROM (...)
 *  lib_flt WHERE ...`, the inner real table inside the parens is what surfaces here. Returns
 *  undefined when nothing matches (the wizard then falls back to the first table). */
export function findFirstReferencedTable(sql: string, schema: PoolSchema): string | undefined {
  if (!sql) return undefined
  // Walk char-by-char tracking paren-depth + string literals so the regex doesn't pick up
  // tables inside subqueries (e.g. ``(SELECT MAX(x) FROM AUX) AS A``) before the outer
  // FROM. Real-world case: ``security_users_get`` has a subselect ``(SELECT MAX(LOUT_USAGE)
  // FROM LICENSE_JDE_OUT …)`` that appears earlier in the source than the outer
  // ``FROM SECURITY_USERS`` — the prior regex returned ``license_jde_out`` and the wizard
  // opened on the wrong table. Only consider FROM / JOIN / INTO / UPDATE that sit at
  // paren-depth 0.
  const re = /\b(?:FROM|JOIN|INTO|UPDATE)\s+([A-Za-z_][\w.]*)/gi
  let m: RegExpExecArray | null
  // Pre-compute paren depth at each position once, so the per-match check is O(1).
  const depths = parenDepths(sql)
  while ((m = re.exec(sql)) !== null) {
    if ((depths[m.index] ?? 0) !== 0) continue   // inside a subquery — skip
    const ident = (m[1] ?? '').split('.').pop() ?? m[1]
    const t = findTable(schema, ident)
    if (t) return t.name
  }
  return undefined
}

/** Per-character paren-depth array (1-based after each `(`, decreased after each `)`),
 *  string-literal-aware so we don't count parens inside ``'foo (bar)'``. */
function parenDepths(sql: string): number[] {
  const out = new Array(sql.length).fill(0)
  let depth = 0
  let inSingle = false
  let inDouble = false
  for (let i = 0; i < sql.length; i++) {
    out[i] = depth
    const c = sql[i]
    if (inSingle) { if (c === "'" && sql[i - 1] !== '\\') inSingle = false; continue }
    if (inDouble) { if (c === '"' && sql[i - 1] !== '\\') inDouble = false; continue }
    if (c === "'") inSingle = true
    else if (c === '"') inDouble = true
    else if (c === '(') depth++
    else if (c === ')') depth--
  }
  return out
}
