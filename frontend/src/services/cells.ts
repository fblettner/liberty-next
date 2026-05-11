// Plain-TS helpers for rendering query-result cells (no React here).

import type { Column, DisplayRule } from '../types/connectors'

/** Format an arbitrary SQL value for a table cell — `null`/`undefined` → "null"
 *  (flagged via `isNull` so the caller can style it), objects → JSON, else String. */
export function cellText(v: unknown): { text: string; isNull: boolean } {
  if (v === null || v === undefined) return { text: 'null', isNull: true }
  if (typeof v === 'object') return { text: JSON.stringify(v), isNull: false }
  return { text: String(v), isNull: false }
}

/** Build a `{value: label}` lookup from an ENUM rule. Memoize the result per column. */
export function enumMap(rule: Extract<DisplayRule, { kind: 'enum' }>): Map<string, string> {
  return new Map(rule.values.map((v) => [v.value, v.label]))
}

/** A rendered cell value, with the display rule applied — BOOLEAN → "yes"/"no" markers, ENUM →
 *  the value's label, LOOKUP → the resolved label (or the raw value while loading). `kind` lets
 *  the table render it differently (the boolean as an icon, an unresolved lookup as muted, …).
 *  `enumMaps` and `lookupMap` are passed in so callers can compute them once per column /
 *  per batch and reuse across rows. */
export function ruleCell(
  value: unknown,
  column: Column,
  enumMaps?: Map<string, string>,
  lookupMap?: Map<string, string>,
): { text: string; isNull: boolean; kind: 'null' | 'plain' | 'boolean-true' | 'boolean-false' | 'enum' | 'lookup' | 'lookup-pending' } {
  if (value === null || value === undefined) return { text: 'null', isNull: true, kind: 'null' }
  const raw = typeof value === 'object' ? JSON.stringify(value) : String(value)
  const rule = column.rule
  if (!rule) return { text: raw, isNull: false, kind: 'plain' }
  if (rule.kind === 'boolean') {
    const truthy = raw === rule.true_value
    return { text: truthy ? '✓' : '✗', isNull: false, kind: truthy ? 'boolean-true' : 'boolean-false' }
  }
  if (rule.kind === 'enum') {
    const hit = enumMaps?.get(raw)
    return hit !== undefined ? { text: hit, isNull: false, kind: 'enum' } : { text: raw, isNull: false, kind: 'plain' }
  }
  if (rule.kind === 'lookup') {
    if (lookupMap === undefined) return { text: raw, isNull: false, kind: 'lookup-pending' }
    const hit = lookupMap.get(raw)
    return hit !== undefined ? { text: hit, isNull: false, kind: 'lookup' } : { text: raw, isNull: false, kind: 'plain' }
  }
  return { text: raw, isNull: false, kind: 'plain' }
}
