// Wire shapes mirroring liberty/sio.py. Kept in a non-React module so a
// component that only needs the shapes can import without dragging the provider.

export interface LockPayload {
  app: string
  screen: string
  key_values: Record<string, string | number | null>
}

export interface HeldLock {
  room: string
  app: string
  screen: string
  key_values: Record<string, string>
  sid: string
  user_id: string
  username: string
  acquired_at: number
}

export interface PoolStats {
  name: string
  dialect: string
  materialised: boolean
  size?: number | null
  checked_out?: number | null
  checked_in?: number | null
  overflow?: number | null
  max_overflow?: number | null
  class?: string
}

export interface ConnectedUser {
  sid: string
  user_id: string
  username: string
  is_superuser: boolean
  client_id: string | null
}

export interface DashboardSnapshot {
  ts: number
  uptime_s: number
  runtime: { python: string; platform: string; connector_count: number; screen_count: number }
  connected_users: ConnectedUser[]
  locks: HeldLock[]
  pools: PoolStats[]
  ai: { enabled: boolean; model?: string | null }
  license: Record<string, unknown>
}

export interface LogEntry {
  ts: number
  level: string
  name: string
  message: string
  exc_info: string | null
}

/** Canonical room name — must match liberty.sio.canonical_lock_key exactly so
 *  client lookups against the broadcast lock map find the right entry. */
export function canonicalLockKey(payload: LockPayload): string {
  const entries = Object.entries(payload.key_values || {})
    .map(([k, v]) => [k.toLowerCase(), v == null ? "" : String(v)] as const)
    .sort(([a], [b]) => a.localeCompare(b))
  const kv = entries.map(([k, v]) => `${k}=${v}`).join("&")
  return `lock:${payload.app}:${payload.screen}:${kv}`
}
