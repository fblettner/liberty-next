// A small time-to-live cache for dashboard widget fetches (no React). Coming back to a dashboard
// tab within the TTL reuses the last result instead of re-running every widget's query; the
// bar's Refresh button bypasses it. Keyed by the request URL, so the same query with other
// binds (another period, another filter) is another entry.

interface Entry<T> { at: number; promise: Promise<T> }

const entries = new Map<string, Entry<unknown>>()

/** The cached promise for *key* when younger than *ttlSeconds*, else a fresh one from *load*.
 *  A failed load is dropped at once so the next call retries. `force` ignores the cache. */
export function cached<T>(key: string, ttlSeconds: number, load: () => Promise<T>, force = false): Promise<T> {
  const now = Date.now()
  const hit = entries.get(key) as Entry<T> | undefined
  if (!force && hit && ttlSeconds > 0 && now - hit.at < ttlSeconds * 1000) return hit.promise
  const promise = load().catch((e: unknown) => { entries.delete(key); throw e })
  entries.set(key, { at: now, promise })
  return promise
}

/** When the newest cached entry whose key starts with *prefix* was fetched, or null. */
export function lastFetchedAt(prefix: string): number | null {
  let best: number | null = null
  for (const [k, e] of entries) if (k.startsWith(prefix) && (best === null || e.at > best)) best = e.at
  return best
}

export function clearWidgetCache(): void {
  entries.clear()
}
