// The environments (pools) an application's connectors can run on — what the picker under the application list
// offers. Pure: the workspace context feeds it the connectors the application uses.

interface PooledConnector { type?: string; pool?: string | null; pools?: string[] }

/** The main database of the connectors that offer several pools — the one a request runs on when nothing is
 *  picked — when they all name the same; null when there is none or when they differ. */
export function mainPoolOf(connectors: PooledConnector[]): string | null {
  const mains = new Set<string>()
  for (const c of connectors) {
    if (c.type === 'sql' && (c.pools?.length ?? 0) > 1 && c.pool) mains.add(c.pool)
  }
  return mains.size === 1 ? [...mains][0] : null
}

/** The picker's choices: the main database first, under its own name ('' = nothing picked: no pool is sent), then
 *  the others by name. Without a shared main database the first choice is *fallback* ("Default"). */
export function poolChoices(options: string[], main: string | null, fallback: string): Array<{ value: string; label: string }> {
  const others = options.filter((p) => p !== main).sort((a, b) => a.localeCompare(b))
  return [{ value: '', label: main ?? fallback }, ...others.map((p) => ({ value: p, label: p }))]
}
