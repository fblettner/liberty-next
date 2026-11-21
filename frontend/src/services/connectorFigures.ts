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
