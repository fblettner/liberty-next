// A parameter that names a dictionary lookup (ParamDef.lookup, resolved by the server into `lookup`): the
// list its value is picked in, and what narrows it — `binds`: a :parameter of the lookup's query → another
// parameter of the same query / endpoint (its current value is sent) or a fixed value. No React here.
import type { ParamLookup } from '../types/connectors'
import type { LookupSpec } from './lookups'

/** The list to read for *lookup* given the other parameters' *values* — ``spec`` is null while a parameter
 *  it is narrowed by is blank; ``waiting`` names those (as declared), to say "pick … first". */
export function paramLookupSpec(
  lookup: ParamLookup, names: string[], values: Record<string, string | null | undefined>,
): { spec: LookupSpec | null; waiting: string[] } {
  const declared = new Map(names.map((n) => [n.toLowerCase(), n]))
  const given = new Map(Object.entries(values).map(([k, v]) => [k.toLowerCase(), String(v ?? '').trim()]))
  const params: Record<string, string> = {}
  const waiting: string[] = []
  for (const [param, source] of Object.entries(lookup.binds ?? {})) {
    const other = declared.get(String(source).toLowerCase())
    if (!other) { params[param] = String(source); continue }      // not a parameter: a fixed value
    const now = given.get(other.toLowerCase()) ?? ''
    if (now) params[param] = now
    else waiting.push(other)
  }
  if (waiting.length) return { spec: null, waiting }
  return {
    spec: { connector: lookup.connector, query: lookup.query, value: lookup.value, label: lookup.label,
            ...(Object.keys(params).length ? { params } : {}), ...(lookup.sources?.length ? { sources: lookup.sources } : {}) },
    waiting,
  }
}
