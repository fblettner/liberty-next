// Pure helpers for the data-scope rules (liberty/auth/scope.py::ScopeRule on the wire): the
// dictionary items a rule can target, where a rule's values may come from (an enum / a lookup),
// the user attributes rules refer to, and a one-line description for lists and the profile.
import type { DictionaryDoc, ScreensDoc } from '../../../types/config'
import type { LookupSpec } from '../../../services/lookups'

export interface ScopeRule {
  column?: string | null
  dd?: string | null
  values?: string[] | null
  source?: string | null
  exclude?: boolean
  mode?: 'ignore' | 'deny'
  connector?: string | null
}

const USER_REF = /^#USER\.([A-Za-z_][A-Za-z0-9_]*)#$/

export const attributeOf = (source: string | null | undefined): string | null => USER_REF.exec(source ?? '')?.[1] ?? null
export const sourceFor = (attribute: string): string => `#USER.${attribute}#`
export const ruleTarget = (r: ScopeRule): string => r.dd || r.column || ''

/** Strip empty keys so the wire shape stays minimal (`exclude_none` on the backend). */
export function cleanRule(r: ScopeRule): ScopeRule {
  const out: ScopeRule = {}
  if (r.dd) out.dd = r.dd
  else if (r.column) out.column = r.column
  if (r.source) out.source = r.source
  else out.values = [...(r.values ?? [])]
  if (r.exclude) out.exclude = true
  if (r.mode === 'deny') out.mode = 'deny'
  if (r.connector) out.connector = r.connector
  return out
}

/** "CO not in 00, 01" / "CO = user's company" / "COMPANY in A, B" — with the deny mode and the
 *  connector appended when set. *labels* maps an item key to its dictionary label. */
export function describeRule(r: ScopeRule, labels: Record<string, string> = {}): string {
  const target = ruleTarget(r)
  const name = labels[target] ? `${labels[target]} (${target})` : target
  const attr = attributeOf(r.source)
  let body: string
  if (attr) body = `${name} ${r.exclude ? '≠' : '='} user.${attr}`
  else {
    const vals = r.values ?? []
    body = vals.length === 0 ? `${name}: ${r.exclude ? 'nothing excluded' : 'nothing allowed'}` : `${name} ${r.exclude ? 'not in' : 'in'} ${vals.join(', ')}`
  }
  const extra: string[] = []
  if (r.mode === 'deny') extra.push('deny when absent')
  if (r.connector) extra.push(r.connector)
  return extra.length ? `${body}  [${extra.join(', ')}]` : body
}

/** Groups OR together, rules AND within a group (the resolved `scope` claim). */
export function describeScope(groups: ScopeRule[][] | null | undefined, labels: Record<string, string> = {}): string[] {
  if (!groups || groups.length === 0) return []
  return groups.map((g) => g.map((r) => describeRule(r, labels)).join('  and  '))
}

export interface DictionaryItem {
  key: string
  label: string | null
  /** 'shared' or the connector section the entry lives in. */
  section: string
  rules: string | null
  rulesValues: string | null
}

/** Every dictionary entry (shared + per-connector), one row per key and section. */
export function dictionaryItems(doc: DictionaryDoc | null): DictionaryItem[] {
  if (!doc) return []
  const out: DictionaryItem[] = []
  const push = (section: string, entries: Record<string, Record<string, unknown>> | undefined) => {
    for (const [key, e] of Object.entries(entries ?? {})) {
      out.push({
        key, section,
        label: typeof e.label === 'string' ? e.label : null,
        rules: typeof e.rules === 'string' ? e.rules.toUpperCase() : null,
        rulesValues: typeof e.rules_values === 'string' ? e.rules_values : null,
      })
    }
  }
  push('shared', doc.dictionary.entries)
  for (const [conn, sec] of Object.entries(doc.dictionary.connectors ?? {})) push(conn, sec.entries)
  return out.sort((a, b) => a.key.localeCompare(b.key) || a.section.localeCompare(b.section))
}

export function itemLabels(items: DictionaryItem[]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const it of items) if (it.label && !out[it.key]) out[it.key] = it.label
  return out
}

export type ValueSource =
  | { kind: 'enum'; options: { value: string; label: string }[] }
  | { kind: 'lookup'; spec: LookupSpec }
  | null

/** Where an item's values can be picked from: its ENUM's members, or its LOOKUP query (resolved
 *  on the entry's own connector, else the lookup's); null when the item has neither. */
export function valueSourceFor(item: DictionaryItem | undefined, doc: DictionaryDoc | null): ValueSource {
  if (!item || !doc || !item.rulesValues) return null
  const id = item.rulesValues
  const shared = doc.dictionary
  const own = item.section === 'shared' ? undefined : shared.connectors?.[item.section]
  if (item.rules === 'ENUM') {
    const en = own?.enums?.[id] ?? shared.enums?.[id]
    const values = Array.isArray(en?.values) ? (en.values as { value: string; label?: string | null }[]) : []
    return { kind: 'enum', options: values.map((v) => ({ value: String(v.value), label: v.label || String(v.value) })) }
  }
  if (item.rules === 'LOOKUP') {
    const lk = own?.lookups?.[id] ?? shared.lookups?.[id]
    if (!lk || typeof lk.query !== 'string' || typeof lk.value !== 'string') return null
    const connector = (typeof lk.connector === 'string' && lk.connector) || (item.section !== 'shared' ? item.section : null)
    if (!connector) return null
    return { kind: 'lookup', spec: { connector, query: lk.query, value: lk.value, label: typeof lk.label === 'string' ? lk.label : lk.value } }
  }
  return null
}

/** connector → the result column names its screens declare (sorted, unique) — the pool a rule on
 *  a plain column can pick from when the column has no dictionary item. */
export function screenColumnNames(doc: ScreensDoc | null): Record<string, string[]> {
  const out: Record<string, Set<string>> = {}
  for (const [app, screens] of Object.entries(doc?.screens ?? {})) {
    for (const scr of Object.values(screens)) {
      const conn = scr.connector || app
      for (const c of scr.columns ?? []) if (c.name) (out[conn] ??= new Set()).add(c.name)
    }
  }
  return Object.fromEntries(Object.entries(out).map(([k, v]) => [k, [...v].sort()]))
}

/** The attribute names in play: every `#USER.x#` the roles use plus every key a user carries. */
export function attributeKeys(roles: { scope?: ScopeRule[] | null }[], users: { attributes?: Record<string, unknown> | null }[]): string[] {
  const out = new Set<string>()
  for (const r of roles) for (const rule of r.scope ?? []) { const a = attributeOf(rule.source); if (a) out.add(a) }
  for (const u of users) for (const k of Object.keys(u.attributes ?? {})) out.add(k)
  return [...out].sort()
}

/** "company=00001,00050" text ⇄ attribute values (a comma makes a list). */
export const attributeToText = (v: unknown): string => (Array.isArray(v) ? v.map(String).join(', ') : v == null ? '' : String(v))
export const attributeFromText = (text: string): string | string[] => {
  const parts = text.split(',').map((s) => s.trim()).filter(Boolean)
  return parts.length > 1 ? parts : (parts[0] ?? '')
}
