// The 4-CRUD grouping over a SQL connector's `queries`. v1 already named its queries
// `<base>_<get|put|post|delete>`, and `liberty-migrate` preserves that — so a connector with 52
// queries is really ~13 tables × 4 slots. This module turns the flat list into that view.
//
// "Suffix-based" is the only rule: a query whose name matches `<base>_<get|put|post|delete>` (case
// insensitive) is part of table `<base>`; anything else is loose and stays in the flat Queries tab.
// `_get` is special — it carries the table-level metadata (label/description/auto_load/max_rows/
// key_columns/columns), since `columns` is a result-schema annotation and only makes sense on a read.
import type { JsonSchema } from '../../common'

export type CrudKind = 'get' | 'put' | 'post' | 'delete'
export const CRUD_KINDS: readonly CrudKind[] = ['get', 'put', 'post', 'delete'] as const

const SUFFIX_RE = /^(.+)_(get|put|post|delete)$/i

/** Split a query name into `<base>` + `<crud>`, or null when it's not a CRUD-named query. */
export function classifyQueryName(name: string): { base: string; crud: CrudKind } | null {
  const m = SUFFIX_RE.exec(name)
  if (!m) return null
  return { base: m[1], crud: m[2].toLowerCase() as CrudKind }
}

export interface QuerySlot {
  name: string
  query: Record<string, unknown>
}

export interface TableGroup {
  /** the prefix, e.g. `"f0005"` — preserves the original case from the query name */
  base: string
  /** which CRUD slots exist; `undefined` for missing slots */
  slots: Partial<Record<CrudKind, QuerySlot>>
}

export interface GroupedQueries {
  tables: TableGroup[]
  loose: QuerySlot[]
}

/** Group a connector's `queries` array by CRUD suffix. Preserves array order — the first time
 *  a base name is seen, that's where the table appears in the list. */
export function groupQueriesByTable(queries: ReadonlyArray<Record<string, unknown>>): GroupedQueries {
  const tables: TableGroup[] = []
  const byBase = new Map<string, TableGroup>()
  const loose: QuerySlot[] = []
  for (const q of queries) {
    const name = typeof q.name === 'string' ? q.name : ''
    if (!name) { loose.push({ name, query: q }); continue }
    const c = classifyQueryName(name)
    if (!c) { loose.push({ name, query: q }); continue }
    let g = byBase.get(c.base.toLowerCase())
    if (!g) {
      g = { base: c.base, slots: {} }
      byBase.set(c.base.toLowerCase(), g)
      tables.push(g)
    }
    g.slots[c.crud] = { name, query: q }
  }
  return { tables, loose }
}

/** Find a query by name (case-sensitive, matches what the migrator emits); -1 if absent. */
export function findQueryIndex(queries: ReadonlyArray<Record<string, unknown>>, name: string): number {
  return queries.findIndex((q) => typeof q.name === 'string' && q.name === name)
}

/** Return a new queries array with the named query replaced (or appended when missing). */
export function replaceQueryByName(
  queries: ReadonlyArray<Record<string, unknown>>,
  name: string,
  next: Record<string, unknown>,
): Record<string, unknown>[] {
  const i = findQueryIndex(queries, name)
  if (i < 0) return [...queries, next]
  return queries.map((q, j) => (j === i ? next : q))
}

/** Return a new queries array with the named query removed (or unchanged when absent). */
export function removeQueryByName(
  queries: ReadonlyArray<Record<string, unknown>>,
  name: string,
): Record<string, unknown>[] {
  return queries.filter((q) => !(typeof q.name === 'string' && q.name === name))
}

/** Make a brand-new query object for a missing CRUD slot of a table. Writable for non-get. */
export function newQueryStub(base: string, crud: CrudKind): Record<string, unknown> {
  const name = `${base}_${crud}`
  return crud === 'get' ? { name, sql: '' } : { name, sql: '', writable: true }
}

/** Pick a subset of a model schema's properties, flattening the picked fields' `x_group` so they
 *  render as one tab (the table-editor already provides its own outer tabs). Required filter likewise. */
export function pickSchemaProperties(s: JsonSchema, keys: ReadonlyArray<string>): JsonSchema {
  const props: Record<string, JsonSchema> = {}
  for (const k of keys) {
    const p = s.properties?.[k]
    if (p) props[k] = { ...p, x_group: undefined }
  }
  return {
    type: 'object',
    title: s.title,
    properties: props,
    required: (s.required ?? []).filter((r) => keys.includes(r)),
  }
}
