// "A list read from a table", made in one step from where it is needed (a screen's column, a
// dictionary item): the query that reads the table goes to the application's list queries, the
// list that names it to the application's dictionary — the two things a consultant had to create
// in two builders before picking the list. No React here.

export interface NewList {
  /** The query that reads the table: ``{name, sql, label?}``. */
  query: { name: string; sql: string; label?: string }
  /** The list as the dictionary holds it (its query, its code and label columns). */
  dictEntry: Record<string, unknown>
  /** The name the list is picked by. */
  dictId: string
}

type Doc = Record<string, unknown>

/** The connectors with *list*'s query added to *connector*'s list queries (left as it is when a
 *  query of that name is there already). */
export function withListQuery(connectors: Record<string, Doc>, connector: string, list: NewList): Record<string, Doc> {
  const conn = connectors[connector] ?? {}
  const lookups = Array.isArray(conn.lookups) ? (conn.lookups as Doc[]) : []
  if (lookups.some((q) => q.name === list.query.name)) return connectors
  return { ...connectors, [connector]: { ...conn, lookups: [...lookups, { ...list.query }] } }
}

/** The dictionary with *list* added under *app*'s own lists. */
export function withList(dictionary: Doc, app: string, list: NewList): Doc {
  const connectors = { ...((dictionary.connectors as Record<string, Doc> | undefined) ?? {}) }
  const scope = { ...(connectors[app] ?? {}) }
  scope.lookups = { ...((scope.lookups as Record<string, Doc> | undefined) ?? {}), [list.dictId]: list.dictEntry }
  connectors[app] = scope
  return { ...dictionary, connectors }
}

/** The names already taken: the list queries of the connector, and the lists of the application
 *  (its own and the shared ones). */
export function takenNames(connectors: Record<string, Doc>, dictionary: Doc, connector: string, app: string): { queries: Set<string>; lists: Set<string> } {
  const conn = connectors[connector] ?? {}
  const queries = new Set<string>()
  for (const section of ['queries', 'lookups', 'sequences']) {
    for (const q of Array.isArray(conn[section]) ? (conn[section] as Doc[]) : []) if (typeof q.name === 'string') queries.add(q.name)
  }
  for (const tb of Array.isArray(conn.tables) ? (conn.tables as Doc[]) : []) {
    for (const slot of ['get', 'put', 'post', 'delete']) if (tb[slot]) queries.add(`${String(tb.name)}_${slot}`)
  }
  const scoped = ((dictionary.connectors as Record<string, Doc> | undefined)?.[app]?.lookups as Doc | undefined) ?? {}
  const shared = (dictionary.lookups as Doc | undefined) ?? {}
  return { queries, lists: new Set([...Object.keys(scoped), ...Object.keys(shared)]) }
}
