// Lookup caches for the dictionary's LOOKUP display rule (the v2 form of v1's
// `dd_rules = "LOOKUP"`). A LOOKUP rule on a column says "resolve this cell's
// value by running query X on connector Y and treating its result as a
// {<value-column>: <label-column>} map" — we fetch each (connector, query)
// once per session and memoize the resulting Map.
//
// The module-level cache is shared across every component that asks for it,
// so a query referenced by 5 columns triggers one network round-trip.
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
}

function specKey(s: LookupSpec): string {
  return `${s.connector}/${s.query}/${s.value}/${s.label}`
}

// Shared promise cache — survives unmounts, lasts the session. A failed fetch
// drops the entry so a re-render gets another shot.
const cache = new Map<string, Promise<Map<string, string>>>()

function fetchLookup(spec: LookupSpec): Promise<Map<string, string>> {
  const key = specKey(spec)
  let p = cache.get(key)
  if (!p) {
    p = api
      .get<QueryResult>(`/api/sql/${encodeURIComponent(spec.connector)}/${encodeURIComponent(spec.query)}`)
      .then((r) => {
        const m = new Map<string, string>()
        for (const row of r.rows) {
          const v = row[spec.value]
          const l = row[spec.label]
          if (v === null || v === undefined) continue
          m.set(String(v), l === null || l === undefined ? String(v) : String(l))
        }
        return m
      })
      .catch((e) => {
        cache.delete(key)
        throw e
      })
    cache.set(key, p)
  }
  return p
}

/** Fetch every LOOKUP referenced by *specs* (dedup'd via the shared cache), once. Returns a
 *  `key → Map<value, label>` lookup, updating as each fetch resolves. Components subscribe by
 *  keying their cells with :func:`specKey`. */
export function useLookupBatch(specs: LookupSpec[]): Map<string, Map<string, string>> {
  const [maps, setMaps] = useState<Map<string, Map<string, string>>>(() => new Map())
  // Stable cache-key for the deps — re-fetch when the spec list changes.
  const depKey = specs.map(specKey).sort().join('|')

  useEffect(() => {
    if (specs.length === 0) return
    let cancelled = false
    Promise.all(
      specs.map(async (s) => {
        const m = await fetchLookup(s)
        return [specKey(s), m] as const
      }),
    )
      .then((pairs) => {
        if (cancelled) return
        setMaps((prev) => {
          const next = new Map(prev)
          for (const [k, m] of pairs) next.set(k, m)
          return next
        })
      })
      .catch(() => {
        /* leave the failed spec out of `maps` — the cell falls back to the raw value */
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- depKey covers specs
  }, [depKey])

  return maps
}

export { specKey as lookupKey }
