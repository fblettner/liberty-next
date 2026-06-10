// Small helpers shared between ScreenDialog and the nested-tab components — pulling these out
// of ScreenDialog so NestedFormTab / NestedTableTab can reuse the same param-bind resolution
// and case-insensitive value lookup. The originals lived inline at the top of ScreenDialog
// until the nested-tab slice; everything here stays type-light + side-effect-free.
import type { Column } from '../../types/connectors'
import type { FieldCondition, ParamBind } from '../../types/screens'

export type Row = Record<string, unknown>

/** The firing row's natural key — ``{KEYCOL: value}`` from the screen columns flagged ``key``,
 *  read case-insensitively and upper-cased to match the capture's key convention. Threaded into
 *  ``_change_context`` so an action's writes/invocations are stamped with the record they fired
 *  for (e.g. AUUSER=DEMO) and group under it in the change package. ``undefined`` when the screen
 *  declares no key columns or the row carries no value for them. */
export function entityKeyOf(columns: readonly Column[] | undefined, row: Row): Record<string, unknown> | undefined {
  const out: Record<string, unknown> = {}
  for (const c of columns ?? []) {
    if (!c.key) continue
    const v = valueFor(c.name, row)
    if (v != null && v !== '') out[c.name.toUpperCase()] = v
  }
  return Object.keys(out).length ? out : undefined
}

/** Send both the as-is keys and UPPERCASE copies — same trick as the inline grid editor:
 *  the migrated `_put`/`_post`/`_delete` queries use v1's uppercase column names, while
 *  Postgres returns the read result's columns lowercased. `text()` only binds what its SQL
 *  references, so the extras are harmless. */
export function withUpper(o: Row): Row {
  const out: Row = { ...o }
  for (const [k, v] of Object.entries(o)) out[k.toUpperCase()] = v
  return out
}

/** For the migrated `_put`'s WHERE rebind: each `:<NAME>` becomes `:<NAME>_ORIGINAL` so editing
 *  a key column still updates the right row (the SET clause untouched — the new value). */
export function originalKeys(row: Row): Row {
  return Object.fromEntries(Object.entries(row).map(([k, v]) => [`${k}_ORIGINAL`, v]))
}

/** Case-insensitive lookup over a row's keys. The DB result rows have lowercase keys
 *  (Postgres folds unquoted identifiers); a ScreenField.name from the migration is uppercase.
 *  Match by lower-casing on both sides so we read the right cell. */
export function valueFor(field: string, src: Row): unknown {
  if (field in src) return src[field]
  const lk = field.toLowerCase()
  if (lk in src) return src[lk]
  for (const k of Object.keys(src)) if (k.toLowerCase() === lk) return src[k]
  return undefined
}

/** Evaluate a list of FieldCondition predicates against the dialog's current form state. The
 *  list AND-s — every predicate must hold. An empty list returns `false` (= no condition
 *  asserted; the caller falls back to the static flag). `value` is a literal (string match)
 *  or a list (membership). Field names match case-insensitively (Postgres lowercases). */
export function evalConditions(rules: FieldCondition[] | undefined, formValues: Row): boolean {
  if (!rules || rules.length === 0) return false
  for (const r of rules) {
    const key = Object.keys(formValues).find((k) => k.toLowerCase() === r.field.toLowerCase())
    const live = key != null ? formValues[key] : undefined
    // Trim both sides — a field value picked from a JDE UDC lookup can be space-padded while the
    // condition's allowed values are clean config (matches the grid's columnVisibleNow).
    const liveStr = (live == null ? '' : String(live)).trim()
    if (Array.isArray(r.value)) {
      if (!r.value.some((x) => String(x).trim() === liveStr)) return false
    } else if (liveStr !== String(r.value).trim()) {
      return false
    }
  }
  return true
}

/** A conditional forced-default rule (``ColumnHint.default_when``). */
export type DefaultWhenRule = { field: string; value: string | string[]; default: string }

/** The conditional forced default for a column given the current values: the FIRST rule whose
 *  {field, value} condition holds, or undefined when none match. When defined, the caller forces
 *  the column to this value and locks it (read-only). Reuses {@link evalConditions} for the same
 *  trim-tolerant, case-insensitive matching. */
export function forcedDefault(rules: DefaultWhenRule[] | undefined, values: Row): string | undefined {
  if (!rules || rules.length === 0) return undefined
  for (const r of rules) {
    if (evalConditions([{ field: r.field, value: r.value }], values)) return r.default
  }
  return undefined
}

/** The ACTIVE display rule for a column/field given the current row/form values: the first
 *  ``rules_when`` whose {field, value} condition holds wins (its rule may be null → plain input),
 *  else the base rule. Generic over the rule type so it serves both the grid (Column.rule) and the
 *  dialog (ScreenField.rule). The discriminator match reuses {@link evalConditions}. */
export function applyRulesWhen<R>(
  rulesWhen: { field: string; value: string | string[]; rule: R | null }[] | undefined,
  baseRule: R | null | undefined,
  values: Row,
): R | null {
  if (rulesWhen && rulesWhen.length) {
    for (const rw of rulesWhen) {
      if (evalConditions([{ field: rw.field, value: rw.value }], values)) return rw.rule
    }
  }
  return baseRule ?? null
}

/** Resolve a list of ParamBinds against the current form state. `value` binds are literals;
 *  `source` binds read the live value of another field on the same form (column name,
 *  case-insensitive). Empty / missing values are dropped — the caller decides whether that
 *  means "no narrowing" (a lookup), "this column keeps its current DB value" (a writable
 *  query), or "skip this fetch" (a nested form that needs its FK to resolve). Reserved
 *  built-ins (`#LOGIN_USER#`/`#SYSDATE#`/…) are skipped — wired in a future auth slice. */
export function resolveBindList(binds: ReadonlyArray<ParamBind> | undefined, formValues: Row): Record<string, string> {
  const out: Record<string, string> = {}
  for (const b of binds ?? []) {
    if (b.value != null && b.value !== '') { out[b.param] = String(b.value); continue }
    if (b.source && !b.source.startsWith('#')) {
      const key = Object.keys(formValues).find((k) => k.toLowerCase() === b.source!.toLowerCase())
      const v = key != null ? formValues[key] : undefined
      if (v != null && String(v) !== '') out[b.param] = String(v)
    }
  }
  return out
}
