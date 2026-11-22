// A pool's SQLAlchemy URL as the parts an operator knows — the database kind, the host, the port, the database,
// the account — and back. No React here.
//
//   dialect+driver://user:password@host:port/database?options
//
// The rule that keeps this safe: a URL is shown as parts ONLY when putting the parts back together gives the
// very same text (`urlParts` returns null otherwise, and the page shows the URL alone). So opening a pool
// and saving it never rewrites a URL the page did not understand; `${VAR}` references are kept as typed.

export interface UrlParts {
  /** `postgresql+asyncpg`, `oracle+oracledb`… */
  scheme: string
  user: string
  /** a password written in the URL itself (the pool's own `password` field is the place for it) */
  password: string | null
  host: string
  port: string
  /** what follows the `/` (a database, an Oracle SID, a SQLite file); null = the URL has no `/` part */
  database: string | null
  /** what follows the `?`, as typed; null = none */
  options: string | null
}

// a run of characters that are not separators, a `${VAR}` / `${VAR:-default}` reference counting as one piece
const piece = (stop: string): string => `(?:\\$\\{[^}]*\\}|[^${stop}$]|\\$(?!\\{))*`
const URL_RE = new RegExp(
  `^([A-Za-z][A-Za-z0-9+_.-]*)://`
  + `(?:(${piece(':@/?')})(?::(${piece('@/?')}))?@)?`
  + `(\\[[^\\]]*\\]|${piece(':@/?')})`
  + `(?::(${piece(':@/?')}))?`
  + `(?:/([^?]*))?`
  + `(?:\\?(.*))?$`,
)

export function composeUrl(p: UrlParts): string {
  const account = p.user || p.password != null ? `${p.user}${p.password != null ? `:${p.password}` : ''}@` : ''
  const path = p.database != null ? `/${p.database}` : p.options != null ? '/' : ''
  return `${p.scheme}://${account}${p.host}${p.port ? `:${p.port}` : ''}${path}${p.options != null ? `?${p.options}` : ''}`
}

/** The URL's parts, or null when it cannot be shown as parts without changing it. */
export function urlParts(url: string): UrlParts | null {
  const m = URL_RE.exec(url.trim())
  if (!m) return null
  const parts: UrlParts = { scheme: m[1], user: m[2] ?? '', password: m[3] ?? null, host: m[4] ?? '', port: m[5] ?? '', database: m[6] ?? null, options: m[7] ?? null }
  return composeUrl(parts) === url.trim() ? parts : null
}

export const dialectOf = (scheme: string): string => scheme.split('+')[0].toLowerCase()

/** One option of the URL (`service_name` for Oracle), '' when absent. */
export function optionOf(options: string | null, name: string): string {
  for (const pair of (options ?? '').split('&')) {
    const [k, ...rest] = pair.split('=')
    if (k === name) return rest.join('=')
  }
  return ''
}

/** The options with one of them set (in its place when it was there, at the end otherwise) or removed when blank. */
export function withOption(options: string | null, name: string, value: string): string | null {
  const pairs = (options ?? '').split('&').filter(Boolean)
  const at = pairs.findIndex((p) => p.split('=')[0] === name)
  if (value === '') { if (at >= 0) pairs.splice(at, 1) }
  else if (at >= 0) pairs[at] = `${name}=${value}`
  else pairs.push(`${name}=${value}`)
  return pairs.length ? pairs.join('&') : null
}

/** What each kind of database starts from: its driver, its usual port, what its `/…` part is called. */
export const DIALECTS: Record<string, { scheme: string; port: string; database: 'database' | 'sid' | 'file' | 'base'; template: string }> = {
  postgresql: { scheme: 'postgresql+asyncpg', port: '5432', database: 'database', template: 'postgresql+asyncpg://user@host:5432/dbname' },
  oracle: { scheme: 'oracle+oracledb', port: '1521', database: 'sid', template: 'oracle+oracledb://user@host:1521/?service_name=ORCL' },
  mysql: { scheme: 'mysql+aiomysql', port: '3306', database: 'database', template: 'mysql+aiomysql://user@host:3306/dbname' },
  mariadb: { scheme: 'mariadb+asyncmy', port: '3306', database: 'database', template: 'mariadb+asyncmy://user@host:3306/dbname' },
  mssql: { scheme: 'mssql+aioodbc', port: '1433', database: 'database', template: 'mssql+aioodbc://user@host:1433/dbname?driver=ODBC+Driver+18+for+SQL+Server' },
  sqlite: { scheme: 'sqlite+aiosqlite', port: '', database: 'file', template: 'sqlite+aiosqlite:///./database.db' },
  // not a database: an LDAP directory, set up here like one (its account, its password) and named by what reads it
  ldap: { scheme: 'ldaps', port: '636', database: 'base', template: 'ldaps://user@host:636' },
}

/** The kinds that are a directory, not a database (`ldap://` and `ldaps://` are the same kind). */
export const isDirectory = (scheme: string): boolean => ['ldap', 'ldaps'].includes(dialectOf(scheme))
/** The kind a scheme belongs to, as the form lists it: `ldaps` is the LDAP kind, over TLS. */
export const kindOf = (scheme: string): string => (isDirectory(scheme) ? 'ldap' : dialectOf(scheme))

/** A pool's line in the list: what it connects to, without the account's secret. */
export function poolSummary(url: string): { dialect: string; where: string } {
  const p = urlParts(url)
  if (!p) return { dialect: '', where: url ? '…' : '' }
  const dialect = kindOf(p.scheme)
  if (dialect === 'sqlite') return { dialect, where: p.database ?? '' }
  const service = optionOf(p.options, 'service_name')
  const target = p.database || service
  return { dialect, where: `${p.host}${p.port ? `:${p.port}` : ''}${target ? ` / ${target}` : ''}` }
}

/** A password written in the URL, as the pool's `password` field wants it (percent-escapes undone). */
export function plainPassword(inUrl: string): string {
  try { return decodeURIComponent(inUrl) } catch { return inUrl }
}

/** What a pool's stages say, read from its settings (the page words them). */
export interface PoolFigures {
  dialect: string; where: string; scheme: string; understood: boolean
  user: string; passwordSet: boolean; passwordInUrl: boolean
  schemas: string[]; dblinks: string[]
  size: number; overflow: number; flags: string[]
}
export function poolFigures(pool: Record<string, unknown>): PoolFigures {
  const url = typeof pool.url === 'string' ? pool.url : ''
  const parts = urlParts(url)
  const keys = (v: unknown): string[] => (v && typeof v === 'object' && !Array.isArray(v) ? Object.keys(v as object) : [])
  const { dialect, where } = poolSummary(url)
  return {
    dialect: dialect || (typeof pool.dialect === 'string' ? pool.dialect : ''), where, scheme: parts?.scheme ?? '', understood: parts != null,
    user: parts?.user ?? '', passwordSet: typeof pool.password === 'string' && pool.password !== '', passwordInUrl: parts?.password != null,
    schemas: keys(pool.schemas), dblinks: keys(pool.dblinks),
    size: typeof pool.pool_size === 'number' ? pool.pool_size : 5, overflow: typeof pool.max_overflow === 'number' ? pool.max_overflow : 10,
    flags: ['trim_strings', 'coalesce_nulls', 'debug_sql', 'echo'].filter((k) => pool[k] === true),
  }
}

/** The drivers offered for each kind of database (the first is the one a new pool starts with). */
export const DRIVERS: Record<string, string[]> = {
  postgresql: ['postgresql+asyncpg', 'postgresql+psycopg'],
  oracle: ['oracle+oracledb'],
  mysql: ['mysql+aiomysql', 'mysql+asyncmy'],
  mariadb: ['mariadb+asyncmy', 'mariadb+aiomysql'],
  mssql: ['mssql+aioodbc'],
  sqlite: ['sqlite+aiosqlite'],
  ldap: ['ldaps', 'ldap'],          // over TLS, or plain
}

/** The drivers to list for a URL's driver: those of its kind of database, and the one it has when it is another. */
export function driversFor(scheme: string): string[] {
  const known = DRIVERS[kindOf(scheme)] ?? []
  return scheme && !known.includes(scheme) ? [scheme, ...known] : known
}

/** The URL moved to another kind of database. Its driver becomes that kind's, and its port too when it was the
 *  usual port of the kind it leaves; the host and the account stay. What names the database follows the kind:
 *  Oracle is reached by a service name (`/?service_name=X`), the others by a database (`/X`) — the name is
 *  carried from one form to the other. To or from SQLite (a file, no host) the URL starts again from the kind's
 *  template. The same URL when it already is of that kind, when the kind is not one the page knows, or when the
 *  URL is not readable. */
export function withDialect(url: string, dialect: string): string {
  const parts = urlParts(url)
  const target = DIALECTS[dialect]
  if (!parts || !target) return url
  const from = kindOf(parts.scheme)
  if (from === dialect) return url
  if (from === 'sqlite' || dialect === 'sqlite') return target.template
  // to or from a directory: the host and the account stay, what named a database does not name a base
  if (from === 'ldap' || dialect === 'ldap') {
    const usual = [DIALECTS[from]?.port ?? '', '', ...(from === 'ldap' ? ['389'] : [])]
    const port = usual.includes(parts.port) ? (parts.host ? target.port : '') : parts.port
    return composeUrl({ ...parts, scheme: target.scheme, port, database: null, options: null })
  }
  const port = parts.port === (DIALECTS[from]?.port ?? '') || parts.port === '' ? (parts.host ? target.port : '') : parts.port
  let { database, options } = parts
  const service = optionOf(options, 'service_name')
  if (dialect === 'oracle' && database && !service) { options = withOption(options, 'service_name', database); database = '' }
  else if (from === 'oracle' && service) { database = database || service; options = withOption(options, 'service_name', '') }
  return composeUrl({ ...parts, scheme: target.scheme, port, database, options })
}

/** What names the database in the form, for a kind of database and a URL: Oracle's service name — or its SID
 *  when the URL is written with one —, a file for SQLite, a database for the others. One field, never two. */
export type TargetField = 'database' | 'service' | 'sid' | 'file' | 'base'
export function targetField(parts: UrlParts): TargetField {
  const kind = kindOf(parts.scheme)
  if (kind === 'sqlite') return 'file'
  if (kind === 'ldap') return 'base'
  if (kind !== 'oracle') return 'database'
  return parts.database && !optionOf(parts.options, 'service_name') ? 'sid' : 'service'
}

/** The kinds of database, as the form names them. */
export const DIALECT_LABELS: Record<string, string> = {
  postgresql: 'PostgreSQL', oracle: 'Oracle', mysql: 'MySQL', mariadb: 'MariaDB', mssql: 'SQL Server', sqlite: 'SQLite',
  ldap: 'LDAP directory',
}

/** A directory's account as the operator types it (a bind DN, `DOMAIN\user`, `user@domain`) ⇄ as the URL
 *  holds it: the characters that would end the account part are percent-escaped, nothing else is touched. */
export function accountInUrl(typed: string): string {
  return typed.replace(/[%@:/?]/g, (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`)
}
export function accountShown(inUrl: string): string {
  try { return decodeURIComponent(inUrl) } catch { return inUrl }
}
