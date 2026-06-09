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

// ── sectioned ⇄ flat adapter ──────────────────────────────────────────────────
// `/admin/config/connectors/parsed` returns SQL connectors in the sectioned shape:
//   tables[]   — each owns name / label / description + up to four CRUD slots (get/put/post/delete)
//   queries[]  — custom standalone queries
//   sequences[]/lookups[] — dictionary value sources
// Every Settings builder works on ONE flat `queries[]` list where each entry carries an explicit
// `type` (table / custom / sequence / lookup). This flattens the sections back into that list so
// none of the flat-model code (pickers, grouping, editors) has to change. The save path sends the
// flat list straight back; the PUT endpoint re-buckets it into sections server-side and lifts the
// table metadata off the `_get` slot, so the on-disk file keeps ONE label / description per table.
//
// A connector already in flat shape (freshly added in-session, or a legacy file the loader didn't
// migrate) is left untouched — its `queries` already carry `type` stamps / CRUD names.
export type ConnectorMap = Record<string, Record<string, unknown>>

export function flattenConnectorSections(conns: ConnectorMap): ConnectorMap {
  const out: ConnectorMap = {}
  for (const [name, c] of Object.entries(conns)) {
    if (!c || c.type === 'api') { out[name] = c; continue }
    const hasSections = ['tables', 'sequences', 'lookups'].some((k) => Array.isArray(c[k]))
    if (!hasSections) { out[name] = c; continue }
    const flat: Record<string, unknown>[] = []
    for (const tbl of (Array.isArray(c.tables) ? c.tables : []) as Record<string, unknown>[]) {
      const base = String(tbl.name ?? '')
      const present = CRUD_KINDS.filter((crud) => tbl[crud] != null)
      // Table label / description ride on the `_get` slot (where the General tab reads/writes
      // them) — or the first present slot when there's no `_get`, so they survive the round-trip.
      const metaSlot = present.includes('get') ? 'get' : present[0]
      for (const crud of present) {
        const slot = { ...(tbl[crud] as Record<string, unknown>) }
        const q: Record<string, unknown> = { name: `${base}_${crud}`, type: 'table', ...slot }
        if (crud === metaSlot) {
          if (tbl.label != null) q.label = tbl.label
          if (tbl.description != null) q.description = tbl.description
        }
        flat.push(q)
      }
    }
    for (const q of (Array.isArray(c.queries) ? c.queries : []) as Record<string, unknown>[]) flat.push({ ...q, type: 'custom' })
    for (const q of (Array.isArray(c.sequences) ? c.sequences : []) as Record<string, unknown>[]) flat.push({ ...q, type: 'sequence' })
    for (const q of (Array.isArray(c.lookups) ? c.lookups : []) as Record<string, unknown>[]) flat.push({ ...q, type: 'lookup' })
    const rest: Record<string, unknown> = { ...c }
    delete rest.tables; delete rest.sequences; delete rest.lookups
    out[name] = { ...rest, queries: flat }
  }
  return out
}

const SUFFIX_RE = /^(.+)_(get|put|post|delete)$/i

/** Split a query name into `<base>` + `<crud>`, or null when it's not a CRUD-named query. */
export function classifyQueryName(name: string): { base: string; crud: CrudKind } | null {
  const m = SUFFIX_RE.exec(name)
  if (!m) return null
  return { base: m[1], crud: m[2].toLowerCase() as CrudKind }
}

// ── sectioned table model ─────────────────────────────────────────────────────
// A first-class CRUD table — mirrors :class:`liberty.connectors.config.TableDef`. Owns its
// `name` / `label` / `description` and up to four CRUD slots (each a `CrudSlotEntry` carrying
// only `sql` / `writable` / `params`). This is what the Connectors builder edits directly now —
// no more stranding the table metadata on a `<base>_get` query.
export interface CrudSlotEntry extends Record<string, unknown> {
  sql?: unknown
  writable?: boolean
  params?: unknown[]
}
export interface TableEntry extends Record<string, unknown> {
  name: string
  label?: string | null
  description?: string | null
  get?: CrudSlotEntry
  put?: CrudSlotEntry
  post?: CrudSlotEntry
  delete?: CrudSlotEntry
}

/** Index of the table named *name* (exact match), or -1. */
export function findTableIndex(tables: ReadonlyArray<TableEntry>, name: string): number {
  return tables.findIndex((t) => t.name === name)
}

/** Replace the table named *name* with *next* (matched on the OLD name so a rename in *next*
 *  still lands in place); appends when absent. */
export function replaceTableByName(
  tables: ReadonlyArray<TableEntry>, name: string, next: TableEntry,
): TableEntry[] {
  const i = findTableIndex(tables, name)
  if (i < 0) return [...tables, next]
  return tables.map((t, j) => (j === i ? next : t))
}

/** A new queries array with the named table removed. */
export function removeTableByName(tables: ReadonlyArray<TableEntry>, name: string): TableEntry[] {
  return tables.filter((t) => t.name !== name)
}

/** A blank table stub — starts with an empty read (`get`) slot, the usual entry point. */
export function newEmptyTable(name: string): TableEntry {
  return { name, get: { sql: '' } }
}

/** True when a table named *name* already exists (case-insensitive — matches the rename guard). */
export function tableExistsByName(tables: ReadonlyArray<TableEntry>, name: string): boolean {
  const lc = name.toLowerCase()
  return tables.some((t) => String(t.name).toLowerCase() === lc)
}

/** Deep-clone the table named *oldName* as *newName* and append. Returns the same array
 *  reference when there's no source table (nothing to copy). */
export function duplicateTableEntry(
  tables: ReadonlyArray<TableEntry>, oldName: string, newName: string,
): TableEntry[] {
  const src = tables.find((t) => t.name === oldName)
  if (!src) return tables as TableEntry[]
  const clone = JSON.parse(JSON.stringify(src)) as TableEntry
  clone.name = newName
  return [...tables, clone]
}

/** Group a flat list of `<base>_<crud>` queries (e.g. the CRUD wizard's output) into ONE
 *  `TableEntry`: each query becomes the matching slot (sql / writable / params only), and the
 *  table's `label` / `description` are lifted off the `_get` query (else the first one). */
export function tableEntryFromFlatQueries(queries: ReadonlyArray<Record<string, unknown>>): TableEntry {
  let base = ''
  const table: TableEntry = { name: '' }
  for (const q of queries) {
    const name = typeof q.name === 'string' ? q.name : ''
    const c = classifyQueryName(name)
    if (!c) continue
    if (!base) base = c.base
    const slot: Record<string, unknown> = {}
    for (const k of ['sql', 'writable', 'params'] as const) if (k in q) slot[k] = q[k]
    table[c.crud] = slot
    // Promote friendly metadata: GET wins, else first slot that carries it.
    for (const meta of ['label', 'description'] as const) {
      if (q[meta] != null && (c.crud === 'get' || table[meta] == null)) table[meta] = q[meta] as string
    }
  }
  table.name = base
  return table
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

/** Make a brand-new query object for a missing CRUD slot of a table. Writable for non-get.
 *  Stamped ``type: 'table'`` so it lands in the Tables tab (the editor classifies by ``type``). */
export function newQueryStub(base: string, crud: CrudKind): Record<string, unknown> {
  const name = `${base}_${crud}`
  return crud === 'get' ? { name, type: 'table', sql: '' } : { name, type: 'table', sql: '', writable: true }
}

/** Deep-clone every `<oldBase>_<crud>` query as `<newBase>_<crud>` and append to the array.
 *  Clones are JSON-cloned, so nested `params` / `columns` / per-dialect `sql` maps are independent.
 *  Returns the same array reference when no source queries exist (nothing to copy). */
export function duplicateTable(
  queries: ReadonlyArray<Record<string, unknown>>,
  oldBase: string,
  newBase: string,
): Record<string, unknown>[] {
  const oldLc = oldBase.toLowerCase()
  const clones: Record<string, unknown>[] = []
  for (const q of queries) {
    const name = typeof q.name === 'string' ? q.name : ''
    const c = classifyQueryName(name)
    if (!c || c.base.toLowerCase() !== oldLc) continue
    const clone = JSON.parse(JSON.stringify(q)) as Record<string, unknown>
    clone.name = `${newBase}_${c.crud}`
    clones.push(clone)
  }
  if (clones.length === 0) return queries as Record<string, unknown>[]
  return [...queries, ...clones]
}

/** Check whether any query in the array belongs to the given base (case-insensitive on the prefix). */
export function tableExists(queries: ReadonlyArray<Record<string, unknown>>, base: string): boolean {
  const lc = base.toLowerCase()
  for (const q of queries) {
    const name = typeof q.name === 'string' ? q.name : ''
    const c = classifyQueryName(name)
    if (c && c.base.toLowerCase() === lc) return true
  }
  return false
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
