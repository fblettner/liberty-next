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
