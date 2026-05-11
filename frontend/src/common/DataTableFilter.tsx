// Type-aware column filters for DataTable. A column declares its filter kind via
// `columnDef.meta.filter` ({ kind, options? }); this module gives each kind its
// control (ColumnFilterControl) and — for text/number/date — a single operator-based
// FilterFn (genericFilterFn). Boolean / enum columns just use TanStack's built-in
// "equals" (the control is a <select>). All theme-driven.
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import type { Column, FilterFn } from '@tanstack/react-table'
import { colors, radius, fontSize, fonts } from '../theme'

export type FilterKind = 'text' | 'number' | 'date' | 'boolean' | 'enum'

export interface FilterMeta {
  /** what control to show + how to interpret the filter value */
  filter?: { kind: FilterKind; options?: { value: string; label: string }[] }
}

/** The filter value for a text/number/date column: an operator + up to two operands.
 *  (Boolean/enum columns store a plain string and use the built-in `equals` filterFn.) */
export interface OpFilter {
  op: 'contains' | 'equals' | 'notEquals' | 'lt' | 'le' | 'gt' | 'ge' | 'between' | 'empty' | 'notEmpty'
  a?: string
  b?: string
}

const OPS_BY_KIND: Record<'text' | 'number' | 'date', OpFilter['op'][]> = {
  text: ['contains', 'equals', 'notEquals', 'empty', 'notEmpty'],
  number: ['equals', 'notEquals', 'lt', 'le', 'gt', 'ge', 'between', 'empty', 'notEmpty'],
  date: ['equals', 'notEquals', 'lt', 'le', 'gt', 'ge', 'between', 'empty', 'notEmpty'],
}
// Short symbols shown in the operator <select> (kept compact for the filter row).
const OP_SYMBOL: Record<OpFilter['op'], string> = {
  contains: '∋', equals: '=', notEquals: '≠', lt: '<', le: '≤', gt: '>', ge: '≥',
  between: '↔', empty: '∅', notEmpty: '≠∅',
}
const NEEDS_B = (op: OpFilter['op']) => op === 'between'
const NEEDS_A = (op: OpFilter['op']) => op !== 'empty' && op !== 'notEmpty'

const isEmpty = (v: unknown) => v === null || v === undefined || v === ''
const numOf = (v: unknown) => (isEmpty(v) ? NaN : Number(v))
const dateOf = (v: unknown) => (isEmpty(v) ? NaN : Date.parse(String(v)))

/** A `FilterFn` for text/number/date columns — interprets an {@link OpFilter}. The compare
 *  domain (string / number / date) comes from `column.columnDef.meta.filter.kind`. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const genericFilterFn: FilterFn<any> = (row, columnId, value) => {
  const f = value as OpFilter | undefined
  if (!f || !f.op) return true
  const v = row.getValue(columnId)
  if (f.op === 'empty') return isEmpty(v)
  if (f.op === 'notEmpty') return !isEmpty(v)
  if (f.a == null || f.a === '') return true // nothing typed yet — don't filter
  const kind: FilterKind = ((row.getAllCells().find((c) => c.column.id === columnId)?.column.columnDef.meta as
    | FilterMeta
    | undefined)?.filter?.kind) ?? 'text'

  if (kind === 'number' || kind === 'date') {
    const conv = kind === 'date' ? dateOf : numOf
    const x = conv(v)
    const a = conv(f.a)
    if (Number.isNaN(x) || Number.isNaN(a)) return false
    switch (f.op) {
      case 'equals': return x === a
      case 'notEquals': return x !== a
      case 'lt': return x < a
      case 'le': return x <= a
      case 'gt': return x > a
      case 'ge': return x >= a
      case 'between': {
        const b = f.b == null || f.b === '' ? a : conv(f.b)
        return Number.isNaN(b) ? false : x >= Math.min(a, b) && x <= Math.max(a, b)
      }
      default: return true
    }
  }
  // text
  const s = isEmpty(v) ? '' : String(v).toLowerCase()
  const a = f.a.toLowerCase()
  switch (f.op) {
    case 'contains': return s.includes(a)
    case 'equals': return s === a
    case 'notEquals': return s !== a
    case 'between': return f.b ? s >= a.toLowerCase() && s <= f.b.toLowerCase() : s.includes(a)
    case 'lt': return s < a
    case 'le': return s <= a
    case 'gt': return s > a
    case 'ge': return s >= a
    default: return true
  }
}
genericFilterFn.autoRemove = (value) => {
  const f = value as OpFilter | undefined
  return !f || !f.op || (NEEDS_A(f.op) && (f.a == null || f.a === ''))
}

// ── controls ────────────────────────────────────────────────────────────────
const Box = styled.div`display: flex; gap: 3px; align-items: center; min-width: 0;`
const Sel = styled.select`
  background: transparent; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  padding: 2px 4px; cursor: pointer; flex-shrink: 0; max-width: 100%;
  option { background: ${colors.bg.dropdown}; color: ${colors.text.secondary}; }
`
const OpSel = styled(Sel)`width: 44px; text-align: center;`
const Inp = styled.input`
  min-width: 0; flex: 1; box-sizing: border-box; background: transparent; border: 1px solid ${colors.border};
  border-radius: ${radius.sm}; color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  padding: 2px 6px; outline: none;
  &:focus { border-color: ${colors.blue.border}; }
  &::placeholder { color: ${colors.text.muted}; }
`

function inputType(kind: FilterKind): string {
  return kind === 'date' ? 'date' : kind === 'number' ? 'number' : 'text'
}

/** Renders the right filter control for `column`, reading `column.columnDef.meta.filter`.
 *  The column is loosely typed so DataTable (generic over its row type) can pass it through. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function ColumnFilterControl({ column }: { column: Column<any, unknown> }) {
  const { t } = useTranslation()
  if (!column.getCanFilter()) return null
  const meta = (column.columnDef.meta as FilterMeta | undefined)?.filter
  const kind: FilterKind = meta?.kind ?? 'text'

  if (kind === 'boolean' || kind === 'enum') {
    const cur = (column.getFilterValue() as string | undefined) ?? ''
    const opts =
      kind === 'boolean'
        ? [{ value: 'true', label: t('common.true', 'true') }, { value: 'false', label: t('common.false', 'false') }]
        : meta?.options ?? []
    return (
      <Sel value={cur} onChange={(e) => column.setFilterValue(e.target.value || undefined)} onClick={(e) => e.stopPropagation()} style={{ width: '100%' }}>
        <option value="">{t('table.filterAny', '(any)')}</option>
        {opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </Sel>
    )
  }

  const f = (column.getFilterValue() as OpFilter | undefined) ?? { op: kind === 'text' ? 'contains' : 'equals' }
  const set = (next: Partial<OpFilter>) => column.setFilterValue({ ...f, ...next })
  return (
    <Box onClick={(e) => e.stopPropagation()}>
      <OpSel value={f.op} onChange={(e) => set({ op: e.target.value as OpFilter['op'] })} title={t('table.filterOp', 'Operator')}>
        {OPS_BY_KIND[kind].map((op) => <option key={op} value={op}>{OP_SYMBOL[op]}</option>)}
      </OpSel>
      {NEEDS_A(f.op) && (
        <Inp type={inputType(kind)} value={f.a ?? ''} onChange={(e) => set({ a: e.target.value })} placeholder="…" />
      )}
      {NEEDS_B(f.op) && (
        <Inp type={inputType(kind)} value={f.b ?? ''} onChange={(e) => set({ b: e.target.value })} placeholder="…" />
      )}
    </Box>
  )
}
