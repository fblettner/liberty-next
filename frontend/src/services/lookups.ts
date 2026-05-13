// Lookup caches for the dictionary's LOOKUP display rule (the v2 form of v1's
// `dd_rules = "LOOKUP"`). A LOOKUP rule on a column says "resolve this cell's
// value by running query X on connector Y and treating its result as a
// {<value-column>: <label-column>} map" — we fetch each (connector, query)
// once per session and memoize the result.
//
// `useLookupBatch` gives the value→label map (used by the grid to render LOOKUP
// cells). `useLookupTables` gives the richer `LookupData` (the raw rows too) —
// needed for the TableView filter panel's *cascading* dropdowns, which narrow a
// lookup's options to the rows whose `<flt_target>` column matches another
// filter's value (v1's ly_tbl_filters). Both share one network round-trip per
// (connector, query) via the module-level promise cache.
import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { QueryResult } from '../types/connectors'

export interface LookupSpec {
  connector: string
  query: string
  /** result column whose values match the cell value (the LOOKUP rule's `value`). */
  value: string
  /** result column whose values we display instead (the LOOKUP rule's `label`). */
  label: string
  /** Static parameter bindings for the lookup query — `:name` placeholders bound to literals.
   *  v1's ly_dictionary_filters (flt_type='VALUE'). Without these, queries that need params to
   *  even run (e.g. UDC's :SY/:RT) return nothing or the wrong rows — every entry that points
   *  at the same lookup with different SY/RT pairs (LMSG, LNGP, AT1, …) was sharing the same
   *  result page before. Different param sets are cached separately. */
  params?: Record<string, string>
}

export interface LookupData {
  /** value → label (the raw value cast to string; falls back to the value when no label). */
  map: Map<string, string>
  /** the lookup query's rows, untouched — for cascading-filter narrowing. */
  rows: Record<string, unknown>[]
  /** the resolved (actual-case) names of the `value` / `label` columns in `rows`. */
  vKey: string
  lKey: string
  /** lowercased column name → its actual-case name in `rows` (DB folds identifiers). */
  byColLower: Map<string, string>
}

/** A lookup's options as `{value, label}` — optionally narrowed to rows whose `filter.column`
 *  equals `filter.value` (the cascading-filter case); duplicates by value are dropped, order
 *  follows the query. */
export function lookupOptions(data: LookupData, filter?: { column: string; value: string }): { value: string; label: string }[] {
  const colKey = filter ? (data.byColLower.get(filter.column.toLowerCase()) ?? filter.column) : undefined
  const out: { value: string; label: string }[] = []
  const seen = new Set<string>()
  for (const row of data.rows) {
    const v = row[data.vKey]
    if (v === null || v === undefined) continue
    if (filter && String(row[colKey as string] ?? '') !== filter.value) continue
    const sv = String(v)
    if (seen.has(sv)) continue
    seen.add(sv)
    const l = row[data.lKey]
    out.push({ value: sv, label: l === null || l === undefined ? sv : String(l) })
  }
  return out
}

function specKey(s: LookupSpec): string {
  // Include the static params in the key so two entries that bind the SAME lookup query with
  // different SY/RT cache separately (otherwise one would clobber the other's rows).
  const p = s.params
    ? Object.keys(s.params).sort().map((k) => `${k}=${s.params![k]}`).join('&')
    : ''
  return `${s.connector}/${s.query}/${s.value}/${s.label}?${p}`
}

// Shared promise cache — survives unmounts, lasts the session. A failed fetch
// drops the entry so a re-render gets another shot.
const cache = new Map<string, Promise<LookupData>>()

function fetchLookup(spec: LookupSpec): Promise<LookupData> {
  const key = specKey(spec)
  let p = cache.get(key)
  if (!p) {
    // Bind the lookup's static params as :placeholders on the URL (the SQL connector reads
    // them off the query string — same path the TableView's Run uses). Skip empty params so
    // they bind to NULL rather than `""` (asyncpg sometimes can't infer the type for an empty
    // string and ends up filtering nothing).
    const url = `/api/sql/${encodeURIComponent(spec.connector)}/${encodeURIComponent(spec.query)}`
    const qs = spec.params
      ? new URLSearchParams(Object.entries(spec.params).filter(([, v]) => v !== '' && v != null)).toString()
      : ''
    p = api
      .get<QueryResult>(qs ? `${url}?${qs}` : url)
      .then((r) => {
        // v1's lkp_dd_id / lkp_dd_label are uppercase, but the DB may report the lookup query's
        // columns in another case (Postgres → lowercase) — resolve them against the result's columns.
        const byColLower = new Map(r.columns.map((c) => [c.name.toLowerCase(), c.name]))
        const vKey = byColLower.get(spec.value.toLowerCase()) ?? spec.value
        const lKey = byColLower.get(spec.label.toLowerCase()) ?? spec.label
        // Params double as a *client-side* row filter: v1 (and migrated v2) UDC SQLs don't carry a
        // WHERE on :SY/:RT, they just return every UDC row and v1's runtime narrowed them. Match
        // each non-empty param against the same-named result column (case-insensitive, trimmed) —
        // this is what makes the Language column show "Français" instead of "Fahrenheit" for "F".
        // If the SQL *does* have a WHERE on the param (some future query), the URL bind already
        // narrowed server-side and the filter here is a no-op.
        const paramFilters: Array<[string, string]> = spec.params
          ? Object.entries(spec.params)
              .filter(([, v]) => v !== '' && v != null)
              .map(([k, v]) => [(byColLower.get(k.toLowerCase()) ?? k), String(v).trim()])
          : []
        const rows: Record<string, unknown>[] = []
        for (const row of r.rows) {
          let keep = true
          for (const [col, want] of paramFilters) {
            const got = row[col]
            if (got === null || got === undefined || String(got).trim() !== want) { keep = false; break }
          }
          if (keep) rows.push(row)
        }
        const map = new Map<string, string>()
        for (const row of rows) {
          const v = row[vKey]
          if (v === null || v === undefined) continue
          const l = row[lKey]
          map.set(String(v), l === null || l === undefined ? String(v) : String(l))
        }
        return { map, rows, vKey, lKey, byColLower }
      })
      .catch((e) => {
        cache.delete(key)
        throw e
      })
    cache.set(key, p)
  }
  return p
}

// Subscribe to a set of lookup fetches; `select` projects each resolved `LookupData` to whatever
// the caller wants (the value→label map, or the whole thing). Re-runs when the spec list changes.
function useLookupSelector<T>(specs: LookupSpec[], select: (d: LookupData) => T): Map<string, T> {
  const [out, setOut] = useState<Map<string, T>>(() => new Map())
  const depKey = specs.map(specKey).sort().join('|')

  useEffect(() => {
    if (specs.length === 0) return
    let cancelled = false
    Promise.all(specs.map(async (s) => [specKey(s), select(await fetchLookup(s))] as const))
      .then((pairs) => {
        if (cancelled) return
        setOut((prev) => {
          const next = new Map(prev)
          for (const [k, v] of pairs) next.set(k, v)
          return next
        })
      })
      .catch(() => {
        /* leave the failed spec out — the cell falls back to the raw value / the dropdown stays empty */
      })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- depKey covers specs; select is a stable module fn
  }, [depKey])

  return out
}

/** Fetch every LOOKUP referenced by *specs* (dedup'd via the shared cache). Returns
 *  `key → Map<value, label>`, updating as each fetch resolves. Key cells/options with :func:`lookupKey`. */
export function useLookupBatch(specs: LookupSpec[]): Map<string, Map<string, string>> {
  return useLookupSelector(specs, (d) => d.map)
}

/** Like :func:`useLookupBatch` but returns the full :type:`LookupData` (raw rows included) — for
 *  cascading-filter dropdowns. Use :func:`lookupOptions` to turn an entry into `{value,label}[]`. */
export function useLookupTables(specs: LookupSpec[]): Map<string, LookupData> {
  return useLookupSelector(specs, (d) => d)
}

export { specKey as lookupKey }
