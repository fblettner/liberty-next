// What an import does with a sheet's rows, without the grid: which header is which column, the check of a row against
// its columns' rules, the lookup return-fill. Shared by the grid's import (useImport) and a page's (AppPage/PageImport).
import type { Column } from '../../types/connectors'
import { loadLookup, lookupKey, type LookupData, type LookupSpec } from '../../services/lookups'
import { activeRulesWhen, resolveBindList } from './dialogHelpers'

export type SpecForRule = (r: Column['rule'] | null | undefined, binds: { param: string; value?: string | null; source?: string | null }[] | undefined,
  rowValues: Record<string, unknown>) => LookupSpec | null

/** (rule, binds, row) → the lookup to fetch: the rule's own parameters plus the row's binds. Null for a non-lookup. */
export const specForRule: SpecForRule = (r, binds, rowValues) => {
  if (r?.kind !== 'lookup') return null
  const dyn = binds && binds.length ? resolveBindList(binds, rowValues) : {}
  return { connector: r.connector, query: r.query, value: r.value, label: r.label, params: { ...(r.params ?? {}), ...dyn }, sources: r.sources }
}

/** header text (lowercased) → result column name: the column's name, its label (not for a coded column: a description is
 *  not unique, its code is in the "(ID)" column an export writes) and the "(ID)" forms. */
export function headerMap(columns: Column[], idTag: string): Map<string, string> {
  const m = new Map<string, string>()
  for (const c of columns) {
    const add = (s: string | null | undefined) => { if (s) m.set(s.trim().toLowerCase(), c.name) }
    add(c.name)
    if (c.rule?.kind !== 'lookup' && c.rule?.kind !== 'enum') add(c.label)
    add(`${c.label ?? c.name} ${idTag}`); add(`${c.name} ${idTag}`)
  }
  return m
}

/** A sheet's rows ({header → value}) as rows keyed by column name; a row whose headers match nothing is left out. */
export function mapSheetRows(rows: Record<string, unknown>[], byHeader: Map<string, string>): Record<string, unknown>[] {
  return rows.map((r) => {
    const out: Record<string, unknown> = {}
    for (const [header, v] of Object.entries(r)) {
      const col = byHeader.get(header.trim().toLowerCase())
      if (col) out[col] = v
    }
    return out
  }).filter((s) => Object.keys(s).length > 0)
}

/** The rule errors of one row: each filled cell against the rule that applies to THIS row (rules_when), matched
 *  trim-tolerantly; a lookup against this row's list, loaded on demand — one that cannot be loaded is an error. */
export async function validateRow(columns: Column[], row: Record<string, unknown>, spec: SpecForRule, listUnavailable: string): Promise<string[]> {
  const errs: string[] = []
  for (const c of columns) {
    const raw = row[c.name]
    if (raw == null || String(raw).trim() === '') continue   // empty → DB defaults / nullability decide
    const val = String(raw).trim()
    const name = c.label || c.name
    const aw = c.rules_when?.length ? activeRulesWhen(c.rules_when, row) : null
    const rule = aw ? (aw.rule ?? null) : (c.rule ?? null)
    if (rule?.kind === 'lookup') {
      const s = spec(rule, aw ? aw.lookup_param_binds : undefined, row)
      if (!s) continue
      let codes: Set<string>
      try {
        codes = new Set([...(await loadLookup(s)).map.keys()].map((k) => k.trim()))
      } catch {
        errs.push(`${name}: ${listUnavailable}`)
        continue
      }
      if (codes.size > 0 && !codes.has(val)) errs.push(`${name}: “${val}” is not a valid value`)
    } else if (rule?.kind === 'enum') {
      if (!rule.values.some((v) => v.value === val || v.value.trim() === val)) errs.push(`${name}: “${val}” is not an allowed value`)
    } else if (rule?.kind === 'boolean') {
      // Only a COMPLETE boolean (both tokens) is checkable — a one-sided flag can't be validated
      // without false positives across JDE's mixed Y/N · 0/1 · blank conventions.
      const tv = rule.true_value
      const fv = rule.false_value
      if (tv != null && fv != null && val !== String(tv).trim() && val !== String(fv).trim()) errs.push(`${name}: “${val}” must be ${tv} or ${fv}`)
    }
  }
  return errs
}

/** Lookup ``return_binds`` on mapped rows: a coded column fills its target columns from the matching lookup row, as a
 *  pick in the form does. Fill-if-empty (a given value wins); a list not in *lookups* is skipped. Mutates the rows. */
export function returnFill(columns: Column[], seeds: Record<string, unknown>[], lookups: Map<string, LookupData>): void {
  const cols = columns.filter((c) => c.rule?.kind === 'lookup' && (c.return_binds?.length ?? 0) > 0)
  if (cols.length === 0) return
  for (const seed of seeds) {
    for (const c of cols) {
      const rule = c.rule
      if (rule?.kind !== 'lookup') continue
      const code = seed[c.name]
      if (code == null || String(code).trim() === '') continue
      const data = lookups.get(lookupKey({ connector: rule.connector, query: rule.query, value: rule.value, label: rule.label, params: rule.params, sources: rule.sources }))
      if (!data?.rows) continue
      const valKey = rule.value
      const want = String(code).trim()
      const row = data.rows.find((r) => {
        const rv = r[valKey] ?? r[valKey.toLowerCase()] ?? r[valKey.toUpperCase()]
        return rv != null && String(rv).trim() === want
      })
      if (!row) continue
      const lcRow = new Map(Object.entries(row).map(([k, v]) => [k.toLowerCase(), v]))
      for (const b of c.return_binds ?? []) {
        const target = columns.find((rc) => rc.name.toLowerCase() === b.column.toLowerCase())?.name ?? b.column
        const cur = seed[target]
        if (cur != null && String(cur).trim() !== '') continue  // a given value wins
        const v = lcRow.get(b.param.toLowerCase())
        if (v !== undefined) seed[target] = v
      }
    }
  }
}

/** The lists the return-fill needs (the coded columns that fill others), loaded: by lookup key. One that fails is left out. */
export async function loadReturnLookups(columns: Column[]): Promise<Map<string, LookupData>> {
  const out = new Map<string, LookupData>()
  for (const c of columns) {
    if (c.rule?.kind !== 'lookup' || !(c.return_binds?.length)) continue
    const s = specForRule(c.rule, undefined, {})
    if (!s || out.has(lookupKey(s))) continue
    try { out.set(lookupKey(s), await loadLookup(s)) } catch { /* the fill is a help, not a check */ }
  }
  return out
}
