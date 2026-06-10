// One field row inside a ScreenDialog / NestedFormView tab — a switch over the column's
// resolved rule (when present) + format/type to pick the right widget. Pulled out of
// ScreenDialog so both the top-level dialog and the editable NestedFormView share the
// same rendering: BOOLEAN → Checkbox, ENUM → SearchSelect (rule values), LOOKUP →
// SearchSelect (resolved options from useLookupTables), password → masked Input,
// numeric/date/text → Input of the matching type. `disabled` / `required` come pre-computed
// from the parent (they fold both the static flag and the per-condition rule). Values are
// kept in the parent's `formValues` map by column name (the read result's case — which is
// what the row already uses).
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Checkbox, Field, Input, SearchSelect } from '../../common'
import type { Column } from '../../types/connectors'
import type { ScreenField } from '../../types/screens'
import { type LookupSpec, lookupKey, lookupOptions, lookupCellColumns, useLookupTables } from '../../services/lookups'
import { colors, fontSize, fonts, radius } from '../../theme'
import { type Row, resolveBindList } from './dialogHelpers'

export const CellWrap = styled.div<{ $span: number }>`
  grid-column: span ${({ $span }) => $span}; min-width: 0;
`
const ReadOnlyBox = styled.div`
  display: block; width: 100%; box-sizing: border-box; padding: 6px 10px; min-height: 32px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  white-space: pre-wrap; overflow-wrap: anywhere;
`

// ── widget choice mirrors ResultTable.editCtrlOf — keep both in sync. ─────────
function isNumericish(fmt: string, typ: string) { return fmt === 'number' || fmt === 'integer' || /int|numeric|decimal|float|double|real/.test(typ) }
// ``jdedate`` is a JDE Julian (CYYDDD) date: stored as an integer, but the read path converts it to
// an ISO ``YYYY-MM-DD`` string (``_from_jde_julian``) and Save re-encodes the picked date back to
// Julian — so it edits as a real date with a calendar, not a number. (Its SQL ``typ`` is integer,
// which is why matching on ``typ`` alone missed it.)
function isDateish(fmt: string, typ: string) { return fmt === 'date' || fmt === 'jdedate' || /date|timestamp/.test(typ) }
export function isPassword(c: Column | null): boolean { return (c?.format ?? '').toLowerCase() === 'password' }

/** Field-lookup shim — keeps the LOOKUP spec resolution call site stable. Resolves the
 *  field's ``lookup_param_binds`` against the current form values so a UDC-style narrowing
 *  query gets its sibling-field-driven params at call time. */
function resolveBinds(field: ScreenField, formValues: Row): Record<string, string> {
  return resolveBindList(field.lookup_param_binds, formValues)
}

export function FieldRow({
  field, column, formValues, onChange, disabled, required, suppressLookup = false, onLookupPick,
}: {
  field: ScreenField
  column: Column | null
  formValues: Row
  onChange: (name: string, v: unknown) => void
  disabled: boolean
  required: boolean
  /** When ``true``, render the field's plain widget (text/number/date) even if the column
   *  resolves to a LOOKUP rule — used by ScreenDialog on ``add`` for key columns where the
   *  user must *create* the value rather than pick from existing rows (v2 auto-derivation
   *  of v1's per-field ``col_rules = "DISABLED"`` convention for PKs). */
  suppressLookup?: boolean
  /** Lookup return-params hook (v1 ly_lkp_params lkp_dir='OUT'). When the user picks a row
   *  from a LOOKUP whose rule has ``return_params``, this fires with a ``{dd_id: value}``
   *  map carrying the picked row's *additional* columns (beyond the headline value). The
   *  parent (ScreenDialog) looks up sibling fields by dd and writes them. */
  onLookupPick?: (returnValues: Record<string, unknown>) => void
}) {
  const { t } = useTranslation()
  const value = formValues[field.name]
  const textValue = value === null || value === undefined ? '' : String(value)
  const label = field.label ?? column?.label ?? field.name
  // Effective rule — screen-field-level override (v1 col_rules → ScreenField.rule, resolved by
  // the backend on GET /api/screens) wins over the read column's rule (the dictionary's
  // default). This is what fixes FSOBNM on F00950: the column has no rule but the field
  // does (LOOKUP #9), so we pick that.
  const effectiveRule = field.rule ?? column?.rule ?? null

  // For a LOOKUP field we need a live lookup spec — the *static* params from the rule plus the
  // dynamic ones from the field's lookup_param_binds (resolved against the current form values).
  const lookupSpec: LookupSpec | null = useMemo(() => {
    if (!effectiveRule || effectiveRule.kind !== 'lookup') return null
    const dyn = resolveBinds(field, formValues)
    return {
      connector: effectiveRule.connector,
      query: effectiveRule.query,
      value: effectiveRule.value,
      label: effectiveRule.label,
      params: { ...(effectiveRule.params ?? {}), ...dyn },
    }
  }, [effectiveRule, field, formValues])
  // Mount a single-spec hook unconditionally (React requires stable hook order). The spec list is
  // either [lookupSpec] (lookup field) or [] (everything else); the lookup service caches per spec
  // key so re-renders that don't change the key don't refetch.
  const lookupMaps = useLookupTables(lookupSpec ? [lookupSpec] : [])
  const lookupData = lookupSpec ? lookupMaps.get(lookupKey(lookupSpec)) : undefined

  let widget: React.ReactNode
  if (disabled) {
    // Read-only echo — keep the value visible (and copyable). Boolean shows its raw code, same as
    // every other type; no special widget here so the user sees exactly what's in the column.
    widget = <ReadOnlyBox aria-readonly>{textValue || <span style={{ color: colors.text.muted }}>—</span>}</ReadOnlyBox>
  } else if (effectiveRule?.kind === 'boolean') {
    const trueV = effectiveRule.true_value
    // false_value is the value to send on uncheck — explicit per ``DictionaryEntry.false_value``,
    // or inferred by the backend (Y→N, 1→0, true→false). Omitted → uncheck sends null (the v1
    // default; the DB column must accept NULL for that to work — see CSI_STATUS where it doesn't).
    const falseV = effectiveRule.false_value ?? null
    const checked = textValue === trueV
    widget = (
      <Checkbox
        checked={checked}
        onChange={(v) => onChange(field.name, v ? trueV : falseV)}
        // Label the STATE, not the raw stored code: a user sees "enabled / disabled", not "1 / 0"
        // (the true/false values like Y/1/01 are storage detail, meaningless on a checkbox).
        label={checked ? t('common.enabled') : t('common.disabled')}
      />
    )
  } else if (effectiveRule?.kind === 'enum') {
    const options = effectiveRule.values.map((v) => ({ value: v.value, label: v.label, mono: v.value }))
    widget = (
      <SearchSelect
        value={textValue}
        onChange={(v) => onChange(field.name, v === '' ? null : v)}
        options={options}
        anyLabel={required ? undefined : t('common.none')}
        placeholder={t('common.pick')}
      />
    )
  } else if (effectiveRule?.kind === 'lookup' && lookupSpec && !suppressLookup) {
    const opts = lookupData ? lookupOptions(lookupData, undefined, effectiveRule.display_fields) : []
    // Lookup pick handler — writes the picked value, then applies this field's ``return_binds``:
    // for each {param, column}, read the picked row's ``param`` column (case-insensitive) and fire
    // ``onLookupPick`` with a ``{targetColumn: value}`` map. The parent (ScreenDialog) writes each
    // to the matching sibling field. Example on f00950: pick OBNM → {FSSY: <picked row's SY>} →
    // parent fills the product-code field. No-op when lookupData isn't loaded yet.
    const handlePick = (picked: string) => {
      onChange(field.name, picked === '' ? null : picked)
      const binds = field.return_binds ?? []
      if (!picked || binds.length === 0 || !lookupData || !onLookupPick) return
      const valueCol = effectiveRule.value
      const row = lookupData.rows.find((r) => {
        const rv = r[valueCol] ?? r[valueCol.toLowerCase()] ?? r[valueCol.toUpperCase()]
        return rv != null && String(rv) === picked
      })
      if (!row) return
      const lcRow = new Map(Object.entries(row).map(([k, v]) => [k.toLowerCase(), v]))
      const aux: Record<string, unknown> = {}
      for (const b of binds) {
        const v = lcRow.get(b.param.toLowerCase())
        if (v !== undefined) aux[b.column] = v
      }
      if (Object.keys(aux).length > 0) onLookupPick(aux)
    }
    widget = (
      <SearchSelect
        value={textValue}
        onChange={handlePick}
        options={opts}
        cellColumns={lookupCellColumns(effectiveRule)}
        anyLabel={required ? undefined : t('common.none')}
        loading={!lookupData}
        placeholder={t('common.pick')}
      />
    )
  } else if (isPassword(column)) {
    // Never round-trip the stored value: it's an ``ENC:`` blob and showing it in the dialog
    // leaks ciphertext (and, when it's a plain hash, lets a casual observer copy it). Render
    // a masked input with a placeholder that hints "leave blank to keep". Seeding code skips
    // password fields, so `textValue` here is always empty when the dialog opens — the user
    // types a new value if they want to change the password, or leaves it blank to keep the
    // current one. (The submit path only includes a password field when the user typed
    // something — see ScreenDialog.submit / NestedFormView.save.)
    widget = (
      <Input
        type="password" autoComplete="new-password" required={required}
        value={textValue}
        placeholder={t('dialog.passwordPlaceholder')}
        onChange={(e) => onChange(field.name, e.target.value === '' ? null : e.target.value)}
      />
    )
  } else {
    const fmt = (column?.format ?? '').toLowerCase()
    const typ = (column?.type ?? '').toLowerCase()
    const type = isDateish(fmt, typ) ? 'date' : isNumericish(fmt, typ) ? 'number' : 'text'
    const dv = type === 'date' && textValue ? textValue.slice(0, 10) : textValue
    widget = (
      <Input
        type={type} required={required} value={dv}
        onChange={(e) => {
          const raw = e.target.value
          if (raw === '') onChange(field.name, null)
          else if (type === 'number') onChange(field.name, Number(raw))
          else onChange(field.name, raw)
        }}
      />
    )
  }

  return (
    <CellWrap $span={Math.max(1, field.colspan ?? 1)}>
      <Field label={`${label}${required ? ' *' : ''}`}>{widget}</Field>
    </CellWrap>
  )
}
