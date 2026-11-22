// What a connector's line and its sections say without opening them: its kind, the pool it runs on, how many
// tables, queries, sequences, lookups or endpoints it holds — and, for a table, which of its four statements
// are set. No React here.

type Row = Record<string, unknown>
const count = (v: unknown): number => (Array.isArray(v) ? v.length : 0)
const text = (v: unknown): string => (typeof v === 'string' ? v : '')

export interface ConnectorFigures {
  api: boolean; pool: string
  tables: number; queries: number; sequences: number; lookups: number
  endpoints: number
}
export function connectorFigures(conn: Row | undefined): ConnectorFigures {
  const c = conn ?? {}
  return {
    api: c.type === 'api', pool: text(c.pool),
    tables: count(c.tables), queries: count(c.queries), sequences: count(c.sequences), lookups: count(c.lookups),
    endpoints: count(c.endpoints),
  }
}

export type Crud = 'get' | 'put' | 'post' | 'delete'
export const CRUDS: readonly Crud[] = ['get', 'put', 'post', 'delete'] as const

/** A table's four statements: which are set, and — for the three that write — whether writing is allowed
 *  (a write statement that is there but not marked writable is refused at run time: worth an orange mark). */
export interface TableSlot { crud: Crud; set: boolean; writable: boolean; blank: boolean }
export function tableSlots(table: Row | undefined): TableSlot[] {
  return CRUDS.map((crud) => {
    const slot = table?.[crud]
    const obj = slot && typeof slot === 'object' && !Array.isArray(slot) ? (slot as Row) : null
    const sql = obj?.sql
    const blank = obj != null && (typeof sql === 'string' ? sql.trim() === '' : sql == null || (typeof sql === 'object' && Object.keys(sql as object).length === 0))
    return { crud, set: obj != null, writable: obj?.writable === true, blank }
  })
}

// ── a single query (custom / sequence / lookup) ──────────────────────────────────────────────

/** A query's statement per database: `sql` is one text, or a map with a `default` and a variant per dialect. */
function sqlTexts(sql: unknown): Record<string, string> {
  if (typeof sql === 'string') return { default: sql }
  if (sql && typeof sql === 'object' && !Array.isArray(sql)) return Object.fromEntries(Object.entries(sql as Row).map(([k, v]) => [k, text(v)]))
  return {}
}
/** The statement without what is not SQL to run: comments and quoted text (a `:name` in a string is not a bind). */
const bare = (sql: string): string => sql.replace(/--[^\n]*/g, ' ').replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/'(?:[^']|'')*'/g, "''")
const READS = new Set(['SELECT', 'WITH', 'VALUES', 'SHOW', 'EXPLAIN'])

export interface QueryFigures {
  label: string; description: string
  /** the databases the statement is written for (`default` first) */
  dialects: string[]
  /** no statement at all */
  blank: boolean
  /** its first word, in capitals: SELECT, INSERT, UPDATE… ('' when blank) */
  verb: string
  /** it changes rows: refused at run time unless the query is marked writable */
  writes: boolean
  writable: boolean
  /** the `:name` placeholders of the statement, in the order they first appear */
  placeholders: string[]
  /** how many parameters are declared (a label, a default…) */
  declared: number
}
export function queryFigures(q: Row | undefined): QueryFigures {
  const texts = sqlTexts(q?.sql)
  const dialects = Object.keys(texts).sort((a, b) => (a === 'default' ? -1 : b === 'default' ? 1 : a.localeCompare(b)))
  const main = bare(texts.default ?? texts[dialects[0]] ?? '')
  const verb = (/[A-Za-z]+/.exec(main)?.[0] ?? '').toUpperCase()
  const placeholders: string[] = []
  for (const t of Object.values(texts)) {
    for (const m of bare(t).matchAll(/(?<![:\w]):([A-Za-z_]\w*)/g)) if (!placeholders.includes(m[1])) placeholders.push(m[1])
  }
  return {
    label: text(q?.label), description: text(q?.description),
    dialects, blank: Object.values(texts).every((t) => t.trim() === ''), verb,
    writes: verb !== '' && !READS.has(verb), writable: q?.writable === true,
    placeholders, declared: count(q?.params),
  }
}

// ── an API connector ─────────────────────────────────────────────────────────────────────────

export interface ApiFigures {
  /** the server's host (the address without its scheme and path); '' when no address is set */
  host: string
  auth: string
  /** what the chosen authentication still lacks: 'username' | 'token' | 'tokenEndpoint' | '' */
  authMissing: '' | 'username' | 'token' | 'tokenEndpoint' | 'key'
  pool: string
  /** the other environments that have their own server */
  targets: number
  endpoints: number
  writable: number
  groups: number
}
export function apiFigures(conn: Row | undefined): ApiFigures {
  const c = conn ?? {}
  const url = text(c.base_url)
  const host = url.replace(/^[a-z][a-z0-9+.-]*:\/\//i, '').replace(/[/?#].*$/, '')
  const auth = text(c.auth_type) || 'none'
  const has = (k: string) => text(c[k]).trim() !== ''
  const authMissing = auth === 'basic' && !has('auth_username') ? 'username'
    : auth === 'oauth2' && !has('auth_token_endpoint') ? 'tokenEndpoint'
      : (auth === 'bearer' || auth === 'api_key') && !has('auth_token') && !has('auth_token_endpoint') ? 'token'
        : auth === 'user_jwt' && !has('auth_jwt_key') ? 'key' : ''                 // each call as the signed-in user: the key that signs
  const eps = Array.isArray(c.endpoints) ? (c.endpoints as Row[]) : []
  const targets = c.targets && typeof c.targets === 'object' && !Array.isArray(c.targets) ? Object.keys(c.targets as object).length : 0
  return {
    host, auth, authMissing, pool: text(c.pool), targets,
    endpoints: eps.length, writable: eps.filter((e) => e?.writable === true).length,
    groups: new Set(eps.map((e) => text(e?.group)).filter(Boolean)).size,
  }
}
