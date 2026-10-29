// The data grid's inline-edit cell (batch edit mode): the control a column edits with — text / number / date input,
// enum or lookup dropdown, boolean checkbox. Moved verbatim from ResultTable.tsx.
import { useState } from 'react'
import styled from '@emotion/styled'
import type { Column } from '../../types/connectors'
import { Checkbox, DatePicker, SearchSelect } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { EditCtrl } from './resultColumns'

const EditInput = styled.input`
  width: 100%; box-sizing: border-box; background: ${colors.bg.input};
  border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  padding: 2px 6px; outline: none;
  &:focus { border-color: ${colors.blue.border}; box-shadow: 0 0 0 2px ${colors.blue.bg}; }
`

export type LookupOpt = { value: string; label: string; mono: string; cells?: string[] }

// The bulk-edit date: it keeps its own value, like the uncontrolled inputs around it — the grid
// re-renders its cells on every edit and must not reset what was typed or picked.
function CellDate({ initial, onChange }: { initial: string; onChange: (iso: string) => void }) {
  const [value, setValue] = useState(initial)
  return <DatePicker size="sm" value={value} onChange={(v) => { setValue(v); onChange(v) }} />
}

export function EditCell({
  ctrl, column, defaultText, onChange, lookupOptions, lookupRows, onLookupReturnValues, narrowBy, displayFields, cellColumns, prebuiltOptions,
}: {
  ctrl: EditCtrl; column: Column; defaultText: string; onChange: (v: unknown) => void
  /** A LOOKUP column's non-cascading dropdown options, built ONCE per (spec, display_fields) by the
   *  parent and shared across every row's cell — see ``editOptsCache``. When present (and the cell
   *  has no active cascade), it's used as-is so we don't rebuild the full option list per cell. */
  prebuiltOptions?: LookupOpt[]
  /** The lookup's ``display_fields`` — extra row columns appended (dimmed, " · ") after each
   *  option's label so similar codes are distinguishable, same as the dialog dropdown. */
  displayFields?: string[]
  /** Per-cell-column metadata (label + filterable) for the dropdown's facet-chip filter. */
  cellColumns?: { label: string; filterable?: boolean }[]
  /** ``{value: label}`` map for LOOKUP columns — already resolved by the surrounding
   *  ``lookupMaps`` (one fetch per unique spec; v2 mirrors v1's "fetch the lookup once,
   *  populate every cell from the same set"). Undefined → not yet loaded → render a
   *  loading state so the user sees the dropdown is coming. */
  lookupOptions?: Map<string, string>
  /** Raw rows from the lookup query — used to resolve ``return_params`` (v1's
   *  ly_lkp_params lkp_dir='OUT'): after a pick, the matching row's other columns flow
   *  back through ``onLookupReturnValues``. Same cache as ``lookupOptions``. */
  lookupRows?: Record<string, unknown>[]
  /** Hook fired on a LOOKUP pick when the rule has ``return_params`` — receives a
   *  ``{dd_id: value}`` map of the picked row's matching columns. The parent
   *  (ResultTable) maps each dd to a sibling grid column and writes them via
   *  ``editChange`` in the same row. */
  onLookupReturnValues?: (returnValues: Record<string, unknown>) => void
  /** Cascading filter for LOOKUP cells (v1's ly_tbl_filters / Column.filter_from). When
   *  the cell's column declares ``filter_from = [{source: "SY_COL", column: "SY"}, …]``
   *  and the same row's ``SY_COL`` cell has a value, this carries
   *  ``[{column: "SY", value: <SY_COL's row value>}, …]`` — the lookup options narrow to
   *  the rows whose ``SY`` matches. v1 had this for bulk-edit DROPDOWNS too (e.g.
   *  DRRT depends on DRSY); v2 wires it through here so the per-row dropdowns cascade
   *  exactly like the form FieldRow's lookup_param_binds do, but client-side over the
   *  once-fetched rows (no per-row fetches). */
  narrowBy?: { column: string; value: unknown }[]
}) {
  // ENUM and LOOKUP use the same searchable dropdown as the dialog's FieldRow —
  // ``SearchSelect`` from common. v1's lookups for UDC / role-id-style tables had
  // hundreds of entries; a native ``<select>`` is unusable at that size (no search,
  // no keyboard filter beyond first-char jump). SearchSelect portals out of the
  // grid cell so the panel isn't clipped by the table's overflow. Both branches are
  // *controlled* — ``value={defaultText}`` reads the current edits-aware value on
  // every TanStack re-render, so a programmatic edit (paste, copy, fill-down) flows
  // through. ``mono`` shows the raw code beside the label so codes stay scannable.
  if (ctrl === 'enum' && column.rule?.kind === 'enum') {
    const opts = column.rule.values.map((v) => ({ value: v.value, label: v.label, mono: v.value }))
    return (
      <SearchSelect
        value={defaultText}
        onChange={(v) => onChange(v === '' ? null : v)}
        options={opts}
        anyLabel={column.rule.title || '—'}
        placeholder=""
      />
    )
  }
  if (ctrl === 'lookup' && column.rule?.kind === 'lookup') {
    const rule = column.rule
    const ready = !!lookupOptions
    // When the cell has active cascading deps (filter_from + same-row sibling values),
    // build options from the *filtered* lookup rows so the dropdown narrows. Otherwise
    // use the pre-projected value→label map directly — faster, no per-row scan.
    const active = (narrowBy ?? []).filter((n) => n.value != null && n.value !== '')
    const useCascade = ready && active.length > 0 && !!lookupRows
    type Opt = LookupOpt
    // ``display_fields`` → code: [cell, cell] map, built from the raw rows; each becomes an extra
    // table column in the dropdown (same as the dialog picker). Only needed when we build options
    // HERE (an active cascade, or the no-prebuilt fallback) — ``prebuiltOptions`` already bakes its
    // cells in, so we skip this O(lookup rows) scan per cell on the common non-cascading path.
    const cellsByCode = (useCascade || (ready && !prebuiltOptions)) && displayFields?.length && lookupRows
      ? (() => {
          const valKey = rule.value
          const m = new Map<string, string[]>()
          for (const r of lookupRows) {
            const v = r[valKey] ?? r[valKey.toLowerCase()] ?? r[valKey.toUpperCase()]
            if (v == null) continue
            m.set(String(v), displayFields.map((f) => {
              const x = r[f] ?? r[f.toLowerCase()] ?? r[f.toUpperCase()]
              return x == null ? '' : String(x).trim()
            }))
          }
          return m
        })()
      : undefined
    let opts: Opt[]
    if (useCascade && lookupRows) {
      // Case-insensitive column lookup on the raw rows: Postgres folds unquoted columns
      // to lowercase, the dictionary uses uppercase. Compare as strings (lookups are
      // code-based: "01" matches "01", coercion stays out of the picture).
      const valKey = rule.value
      const labKey = rule.label
      const matches = lookupRows.filter((r) => active.every(({ column: col, value }) => {
        const rv = r[col] ?? r[col.toLowerCase()] ?? r[col.toUpperCase()]
        // Trim-tolerant: JDE pads / right-justifies UDC codes, so the dependent column and the
        // sibling cell value can differ in padding for the same logical code (matches the
        // filter-panel cascade fix in services/lookups.ts).
        return rv != null && String(rv).trim() === String(value).trim()
      }))
      opts = matches
        .map((r): Opt | null => {
          const v = r[valKey] ?? r[valKey.toLowerCase()] ?? r[valKey.toUpperCase()]
          const l = r[labKey] ?? r[labKey.toLowerCase()] ?? r[labKey.toUpperCase()]
          return v == null
            ? null
            : { value: String(v), label: l == null ? String(v) : String(l), mono: String(v), cells: cellsByCode?.get(String(v)) }
        })
        .filter((o): o is Opt => o !== null)
    } else if (ready && prebuiltOptions) {
      // Common path: a non-cascading lookup reuses the list the parent built once for this column.
      opts = prebuiltOptions
    } else if (ready) {
      opts = [...lookupOptions!.entries()]
        .map(([value, label]) => ({ value, label, mono: value, cells: cellsByCode?.get(value) }))
    } else {
      opts = []
    }
    // Lookup-pick handler — writes the picked value, then applies the column's ``return_binds``:
    // for each {param, column}, read the picked row's ``param`` column (case-insensitive) and fire
    // ``onLookupReturnValues`` with a ``{targetColumn: value}`` map. The parent (ResultTable)
    // writes each to the sibling cell on the same row. Explicit replacement for v1's auto-by-dd.
    const handlePick = (picked: string) => {
      onChange(picked === '' ? null : picked)
      const binds = column.return_binds ?? []
      if (!picked || binds.length === 0 || !lookupRows || !onLookupReturnValues) return
      const valueCol = rule.value
      const row = lookupRows.find((r) => {
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
      if (Object.keys(aux).length > 0) onLookupReturnValues(aux)
    }
    return (
      <SearchSelect
        value={defaultText}
        onChange={handlePick}
        options={opts}
        cellColumns={cellColumns}
        anyLabel={rule.title || '—'}
        loading={!ready}
        placeholder=""
      />
    )
  }
  if (ctrl === 'boolean' && column.rule?.kind === 'boolean') {
    // v1 parity: boolean columns render as a checkbox in edit mode. The rule's
    // ``true_value`` / ``false_value`` (explicit or inferred via DictionaryFile) drive
    // what we actually send: NOMASX1's CSI_STATUS goes "Y" / "N"; user status goes
    // "01" / null. Mirrors the dialog's FieldRow boolean branch.
    const trueV = column.rule.true_value
    const falseV = column.rule.false_value ?? null
    const checked = defaultText === trueV
    return (
      <Checkbox
        checked={checked}
        onChange={(v) => onChange(v ? trueV : falseV)}
        label={checked ? trueV : (falseV ?? '—')}
      />
    )
  }
  const type = ctrl === 'date' ? 'date' : ctrl === 'number' ? 'number' : 'text'
  const dv = ctrl === 'date' && defaultText ? defaultText.slice(0, 10) : defaultText
  if (ctrl === 'date') return <CellDate initial={dv} onChange={(v) => onChange(v === '' ? null : v)} />
  return (
    <EditInput type={type} defaultValue={dv} onChange={(e) => {
      const raw = e.target.value
      if (raw === '') onChange(null)
      else if (ctrl === 'number') onChange(Number(raw))
      else onChange(raw)
    }} />
  )
}
