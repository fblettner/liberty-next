// The field one declared parameter is filled in — of a query or of an endpoint — wherever that is: the
// screen's parameter form, the HTTP runner, a test step, the endpoint editor's test. A parameter that names
// a dictionary lookup is picked in its list (code, label, the lookup's columns and filter chips; a list
// narrowed by another parameter waits for it), one with a few fixed choices in a dropdown, any other typed.
// A value that is not in the list may still be typed (a `{{captured}}` value in a test).
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Input } from './Input'
import { SearchSelect } from './SearchSelect'
import type { ParamDef } from '../types/connectors'
import { lookupCellColumns, lookupKey, lookupOptions, useLookupTables } from '../services/lookups'
import { paramLookupSpec } from '../services/paramLookup'

export function ParamInput({
  def, value, onChange, values, names, disabled, title,
}: {
  def: ParamDef
  value: string
  onChange: (value: string) => void
  /** the other parameters' current values, by name: what narrows a list */
  values?: Record<string, string | null | undefined>
  /** the names of every parameter declared beside this one */
  names?: string[]
  disabled?: boolean
  title?: string
}) {
  const { t } = useTranslation()
  const lookup = def.lookup ?? null
  const { spec, waiting } = useMemo(
    () => (lookup ? paramLookupSpec(lookup, names ?? [], values ?? {}) : { spec: null, waiting: [] as string[] }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- the values a bind reads are in the key
    [lookup, (names ?? []).join('|'), lookup ? Object.values(lookup.binds ?? {}).map((s) => `${s}=${values?.[s] ?? ''}`).join('|') : ''],
  )
  const tables = useLookupTables(useMemo(() => (spec ? [spec] : []), [spec]))
  const data = spec ? tables.get(lookupKey(spec)) : undefined
  const options = useMemo(() => (data ? lookupOptions(data, undefined, lookup?.display_fields, lookup?.hide_value) : []), [data, lookup])
  const hint = def.default != null && def.default !== '' ? t('table.defaultPrefix', { v: def.default }) : def.example ? t('table.examplePrefix', { v: def.example }) : ''

  if (lookup) {
    return (
      <SearchSelect
        value={value} disabled={disabled} options={options} allowCustom clearable loading={!!spec && !data}
        cellColumns={lookupCellColumns(lookup)} title={def.label || lookup.title || def.name}
        placeholder={waiting.length ? t('paramInput.pickFirst', { name: waiting.join(', ') }) : hint}
        onChange={(v) => onChange(v ?? '')}
      />
    )
  }
  if (def.choices?.length) {
    return (
      <SearchSelect
        value={value} disabled={disabled} options={def.choices.map((c) => ({ value: c, label: c }))} clearable
        placeholder={def.default ?? ''} onChange={(v) => onChange(v ?? '')}
      />
    )
  }
  return <Input type="text" value={value} disabled={disabled} placeholder={hint} title={title} onChange={(e) => onChange(e.target.value)} />
}
