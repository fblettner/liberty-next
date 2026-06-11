// Plain-TS helpers for rendering query-result cells (no React here).

import type { Column, DisplayRule } from '../types/connectors'

/** Format an arbitrary SQL value for a table cell — `null`/`undefined` → "" (blank,
 *  with `isNull: true` so the caller can still style the cell distinctly if it
 *  wants), objects → JSON, else String. The blank-instead-of-"null" choice matches
 *  operator expectations: a missing value reads as absence, not as the literal
 *  four-letter SQL keyword (which dominated mostly-empty columns visually). */
export function cellText(v: unknown): { text: string; isNull: boolean } {
  if (v === null || v === undefined) return { text: '', isNull: true }
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
  if (value === null || value === undefined) return { text: '', isNull: true, kind: 'null' }
  const raw = typeof value === 'object' ? JSON.stringify(value) : String(value)
  const rule = column.rule
  if (!rule) return { text: raw, isNull: false, kind: 'plain' }
  if (rule.kind === 'boolean') {
    // Filled bullet for both states — color does the work (green = true, red = false). Reads
    // as a status indicator at a glance instead of needing to parse ✓ vs ✗ glyphs. The caller
    // should pass a hover ``title`` ("yes"/"no") so the value stays accessible. (v1 parity:
    // the screenshot review prompted this — the previous ✓/✗ chars inherited the cell's plain
    // text color, since the styled.ts CSS class names didn't actually match the kind names.)
    const truthy = raw === rule.true_value
    return { text: '●', isNull: false, kind: truthy ? 'boolean-true' : 'boolean-false' }
  }
  if (rule.kind === 'enum') {
    // Prefer the precomputed map (base rule, built once per batch); fall back to the rule's own
    // values so a per-row rules_when ENUM — whose values differ from the base — still resolves its
    // label without a dedicated map.
    const hit = enumMaps?.get(raw) ?? rule.values.find((v) => v.value === raw)?.label
    return hit !== undefined && hit !== '' ? { text: hit, isNull: false, kind: 'enum' } : { text: raw, isNull: false, kind: 'plain' }
  }
  if (rule.kind === 'lookup') {
    if (lookupMap === undefined) return { text: raw, isNull: false, kind: 'lookup-pending' }
    const hit = lookupMap.get(raw)
    return hit !== undefined ? { text: hit, isNull: false, kind: 'lookup' } : { text: raw, isNull: false, kind: 'plain' }
  }
  return { text: raw, isNull: false, kind: 'plain' }
}
