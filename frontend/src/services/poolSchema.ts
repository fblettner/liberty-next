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
 *  to pick up a freshly-added table without reloading the page. Clears every cache touched
 *  by the introspection helpers, so the next call to either ``getPoolSchema`` or the lazy
 *  ``getPoolSchemaNames`` / ``getPoolSchemaTables`` re-fetches. */
export function invalidatePoolSchema(connector?: string): void {
  if (connector === undefined) {
    cache.clear()
    schemasCache.clear()
    schemaTablesCache.clear()
  } else {
    cache.delete(connector)
    schemasCache.delete(connector)
    for (const k of Array.from(schemaTablesCache.keys())) {
      if (k.startsWith(`${connector}\0`)) schemaTablesCache.delete(k)
    }
  }
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

/** Every table referenced by a SQL string's top-level FROM / JOIN (paren-depth 0), as
 *  ``{schema?, name}`` (schema split off a qualified ``SY920.F0092``). De-duplicated. Powers the
 *  SQL editor's LAZY autocomplete — fetch only these tables' columns instead of the whole pool. */
export function findReferencedTables(sql: string): { schema?: string; name: string }[] {
  if (!sql) return []
  // Match a plain ``[schema.]table`` OR a portable ``#SCHEMA.<KEY>#.<table>`` pool-schema token
  // (the backend resolves the token to the real owner; the editor/wizard keep it for portability).
  const re = /\b(?:FROM|JOIN|INTO|UPDATE)\s+(#SCHEMA(?:\.[A-Za-z0-9_]+)?#\.[A-Za-z_]\w*|[A-Za-z_][\w.]*)/gi
  const depths = parenDepths(sql)
  const out: { schema?: string; name: string }[] = []
  const seen = new Set<string>()
  let m: RegExpExecArray | null
  while ((m = re.exec(sql)) !== null) {
    if ((depths[m.index] ?? 0) !== 0) continue   // inside a subquery — skip
    const tok = m[1] ?? ''
    let schema: string | undefined, name: string
    const sm = tok.match(/^(#SCHEMA(?:\.[A-Za-z0-9_]+)?#)\.([A-Za-z_]\w*)$/i)
    if (sm) { schema = sm[1]; name = sm[2] }                       // #SCHEMA.CTL#.F0004
    else { const parts = tok.split('.'); name = parts.pop() ?? ''; schema = parts.length ? parts.pop() : undefined }
    const key = `${(schema ?? '').toLowerCase()}.${name.toLowerCase()}`
    if (!name || seen.has(key)) continue
    seen.add(key)
    out.push({ schema, name })
  }
  return out
}

/** Fetch ONLY the given tables' columns and assemble them into a partial :class:`PoolSchema` — the
 *  lazy alternative to ``getPoolSchema`` for the SQL editor. Each ref is a targeted ``name_like``
 *  fetch (scoped to its schema when qualified), so a 6-schema JDE pool resolves the 1-3 tables the
 *  query actually touches in milliseconds instead of the 10s full-catalog walk. ``null`` → none. */
export async function fetchTablesSchema(
  connector: string, refs: { schema?: string; name: string }[],
): Promise<PoolSchema | null> {
  if (refs.length === 0) return null
  const parts = await Promise.all(refs.map((r) => getPoolSchemaTables(connector, r.schema ?? null, r.name)))
  const tables: PoolTable[] = []
  const seen = new Set<string>()
  let pool = '', dialect = ''
  for (let i = 0; i < parts.length; i++) {
    const r = parts[i]
    if (!r) continue
    pool = pool || r.pool; dialect = dialect || r.dialect
    // The ``name_like`` fetch can return siblings (LIKE-match); keep only the table we asked for.
    const want = refs[i].name.toLowerCase()
    for (const tbl of r.tables) {
      if (tbl.name.toLowerCase() !== want) continue
      const k = `${(tbl.schema ?? '').toLowerCase()}.${tbl.name.toLowerCase()}`
      if (seen.has(k)) continue
      seen.add(k)
      tables.push(tbl)
    }
  }
  if (tables.length === 0) return null
  return { pool, dialect, tables, truncated: false }
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

// ─── lazy two-step schema fetch (for the CRUD wizard) ─────────────────────────────────────
// Oracle pools enumerate every accessible owner under ``ALL_TAB_COLUMNS`` — the unscoped
// ``/_schema`` call walks every schema's tables + columns, which can take 10+ seconds on a
// JDE-style catalog (6 schemas × hundreds of tables each). The wizard needs only the schema
// LIST up front; tables are only worth fetching once the operator picks one schema. These two
// helpers wrap the matching backend endpoints, both session-cached.

/** Lightweight schema-name list — used by the wizard's first dropdown. ``null`` on 403 / 502 /
 *  404 so the caller degrades gracefully (the picker shows "no schemas"). */
// ``schema_map`` = the ``#SCHEMA.<KEY># → real schema`` mapping (empty for pools without one); lets
// the wizard offer the portable token in its schema picker + keep it in generated SQL.
export interface PoolSchemaNames { pool: string; dialect: string; schemas: string[]; schema_map?: Record<string, string> }
const schemasCache = new Map<string, Promise<PoolSchemaNames | null>>()
export function getPoolSchemaNames(connector: string): Promise<PoolSchemaNames | null> {
  let p = schemasCache.get(connector)
  if (!p) {
    p = api
      .get<PoolSchemaNames>(`/api/sql/${encodeURIComponent(connector)}/_schemas`)
      .catch((e) => {
        if (e instanceof ApiError) return null
        throw e
      })
    schemasCache.set(connector, p)
  }
  return p
}

/** Tables-of-one-schema lookup — the second step of the wizard flow. ``schema = null`` falls
 *  back to the full unscoped walk (autocomplete path, not the wizard). ``nameLike`` is a
 *  SQL-LIKE-style filter (``F009%``) applied before the per-table column fetch — the big
 *  speedup knob on Oracle. Cached per ``(connector, schema, nameLike)`` for the session —
 *  :func:`invalidatePoolSchema` clears it. */
const schemaTablesCache = new Map<string, Promise<PoolSchema | null>>()
export function getPoolSchemaTables(
  connector: string, schema: string | null, nameLike: string | null = null,
): Promise<PoolSchema | null> {
  const pat = (nameLike ?? '').trim()
  const key = `${connector}\0${schema ?? ''}\0${pat}`
  let p = schemaTablesCache.get(key)
  if (!p) {
    const qs = new URLSearchParams()
    if (schema) qs.set('schema', schema)
    if (pat) qs.set('name_like', pat)
    const tail = qs.toString()
    const url = `/api/sql/${encodeURIComponent(connector)}/_schema${tail ? `?${tail}` : ''}`
    p = api
      .get<PoolSchema>(url)
      .catch((e) => {
        if (e instanceof ApiError) return null
        throw e
      })
    schemaTablesCache.set(key, p)
  }
  return p
}
