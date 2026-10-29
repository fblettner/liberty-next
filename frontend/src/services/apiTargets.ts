// An API connector's environments (no React): the pool its own server serves (``pool``) and one
// server per other pool (``targets``) — AIS per JDE environment. Mirrors the server's rule
// (liberty/connectors/registry.py::_govern_api_targets): the environments are ``pool`` plus every
// pool of a SQL connector running on it; one of them without a server is refused at call time.

/** One environment's server: what differs from the connector's own (blank = inherited). */
export type ApiTarget = {
  base_url?: string
  auth_username?: string
  auth_password?: string
  auth_token?: string
  verify_ssl?: boolean
}

/** The SQL connectors' pools as the builder holds them (``pool`` defaults to ``default``). */
export type SqlPools = { pool?: string | null; pools?: string[] | null }

/** The pools of *own*'s environments: itself and the pools of every SQL connector running on it. */
export function environmentPools(own: string | null | undefined, sql: SqlPools[]): string[] {
  if (!own) return []
  const out = new Set<string>([own])
  for (const c of sql) {
    const allowed = [c.pool || 'default', ...(c.pools ?? [])]
    if (allowed.includes(own)) allowed.forEach((p) => out.add(p))
  }
  return [...out].sort()
}

/** The environments with no server (refused at call time) — only once the connector has targets. */
export function missingTargets(own: string | null | undefined, targets: Record<string, ApiTarget> | undefined, sql: SqlPools[]): string[] {
  const t = targets ?? {}
  if (!own || Object.keys(t).length === 0) return []
  return environmentPools(own, sql).filter((p) => p !== own && !(p in t))
}

/** *t* without its blank fields — what the TOML keeps (blank = the connector's value). */
export function cleanTarget(t: ApiTarget): ApiTarget {
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(t)) if (v !== undefined && v !== null && v !== '') out[k] = v
  return out as ApiTarget
}
