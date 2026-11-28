// The data grid's pure column helpers (no React): a column's header, alignment, filter kind and inline-edit
// control, the filter props for TanStack, and a column's visible_when check against the active filters.
// Moved verbatim from ResultTable.tsx.
import type { CSSProperties } from 'react'
import type { Column } from '../../types/connectors'
import type { FilterKind } from '../../common/DataTableFilter'

export type DataRow = Record<string, unknown>
export type Align = CSSProperties['textAlign']
export type EditCtrl = 'enum' | 'lookup' | 'boolean' | 'date' | 'number' | 'text'
/** A select-filter option (code value + display label + the mono code shown beside it). */
export type FilterOpt = { value: string; label: string; mono?: string }

export function colHeader(c: Column): string { return c.label ?? c.name }
// Column alignment: an explicit `align` hint wins; otherwise the natural default — booleans
// (✓/✗) centered, numbers right-aligned (like every spreadsheet), everything else left.
export function cellAlign(c: Column): 'left' | 'right' | 'center' | undefined {
  if (c.align === 'left' || c.align === 'right' || c.align === 'center') return c.align
  if (c.rule?.kind === 'boolean') return 'center'
  if (isNumericish((c.format ?? '').toLowerCase(), (c.type ?? '').toLowerCase())) return 'right'
  return undefined
}
export function isNumericish(fmt: string, typ: string) { return fmt === 'number' || fmt === 'integer' || /int|numeric|decimal|float|double|real/.test(typ) }
// jdedate = JDE Julian date: integer SQL type, but read as ISO YYYY-MM-DD and re-encoded to Julian
// on Save — so it edits with a calendar, not as a number. ``datetime`` / ``timestamp`` formats also
// get the date control (the filter compares day-grain, so a TIMESTAMP filters *by date*). Keep in
// sync with FieldRow.isDateish.
export function isDateish(fmt: string, typ: string) {
  return /^(date|datetime|timestamp|jdedate)$/.test(fmt) || /date|timestamp/.test(typ)
}
export function filterKindOf(c: Column): FilterKind {
  if (c.rule?.kind === 'boolean') return 'boolean'
  if (c.rule?.kind === 'enum') return 'enum'
  const fmt = (c.format ?? '').toLowerCase(), typ = (c.type ?? '').toLowerCase()
  if (isDateish(fmt, typ)) return 'date'
  if (isNumericish(fmt, typ)) return 'number'
  return 'text'
}
export function editCtrlOf(c: Column): EditCtrl {
  // Rule-driven widgets first — match the dialog's FieldRow conventions so inline-edit
  // and dialog-edit feel identical (v1 parity: every form-control surface used dropdowns
  // for ENUM / LOOKUP and a checkbox for BOOLEAN, never a plain text input).
  if (c.rule?.kind === 'enum') return 'enum'
  if (c.rule?.kind === 'lookup') return 'lookup'
  if (c.rule?.kind === 'boolean') return 'boolean'
  const fmt = (c.format ?? '').toLowerCase(), typ = (c.type ?? '').toLowerCase()
  if (isDateish(fmt, typ)) return 'date'
  if (isNumericish(fmt, typ)) return 'number'
  return 'text'
}
// the column filter of a kind: what the shared grid's filter control and match need (common/DataTableFilter)
export { filterPropsFor } from '../../common/DataTableFilter'

export function columnVisibleNow(c: Column, activeFilters: Record<string, string>): boolean {
  const vw = c.visible_when
  if (!vw) return true
  const conds = Array.isArray(vw) ? vw : [vw]
  return conds.every(({ field, value }) => {
    const raw = activeFilters[field]
    if (raw == null || raw === '') return true
    // Trim both sides: the active filter value can come from a JDE UDC lookup whose code reads
    // back space-padded ("1         "), while the visible_when allowed values are clean config
    // ("1"). Without trimming, a padded filter hides every conditional column (the bug after
    // moving the security-type lookup to read JDE directly).
    const v = String(raw).trim()
    return Array.isArray(value) ? value.some((x) => String(x).trim() === v) : String(value).trim() === v
  })
}
