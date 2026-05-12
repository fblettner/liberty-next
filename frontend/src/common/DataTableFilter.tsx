// Type-aware column filters for DataTable. A column declares its filter kind via
// `columnDef.meta.filter` ({ kind, options? }); this module gives each kind its
// control (ColumnFilterControl) and — for text/number/date — a single operator-based
// FilterFn (genericFilterFn). Boolean / enum columns just use TanStack's built-in
// "equals" (the control is a <select>). All theme-driven.
import { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'
import type { Column, FilterFn } from '@tanstack/react-table'
import { colors, radius, fontSize, fonts, shadow } from '../theme'

export type FilterKind = 'text' | 'number' | 'date' | 'boolean' | 'enum'

/** Per-column extras carried in TanStack's `columnDef.meta`. */
export interface FilterMeta {
  /** what filter control to show + how to interpret the filter value */
  filter?: { kind: FilterKind; options?: { value: string; label: string }[] }
  /** header + cell text alignment (numbers → right, booleans → center, …); default left */
  align?: 'left' | 'right' | 'center'
  /** an internal helper column (the edit-mode select / status columns) — excluded from search, the Columns menu, etc. */
  internal?: boolean
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
// What the operator <select> shows (compact, but legible) + a `title` per option with the
// full name. Plain ASCII-ish glyphs so they render in any font.
const OP_LABEL: Record<OpFilter['op'], string> = {
  contains: '~', equals: '=', notEquals: '≠', lt: '<', le: '≤', gt: '>', ge: '≥',
  between: '↔', empty: '∅', notEmpty: '≠∅',
}
const OP_NAME: Record<OpFilter['op'], string> = {
  contains: 'contains', equals: 'equals', notEquals: 'not equals',
  lt: 'less than', le: 'less than or equal', gt: 'greater than', ge: 'greater than or equal',
  between: 'between', empty: 'is empty', notEmpty: 'is not empty',
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
// Only drop the filter when there's no operator at all — *not* when the operand is still
// empty: that lets you pick an operator first and type the value afterwards (an empty operand
// just means the filter matches everything for now).
genericFilterFn.autoRemove = (value) => {
  const f = value as OpFilter | undefined
  return !f || !f.op
}

// ── controls ────────────────────────────────────────────────────────────────
const Box = styled.div`display: flex; gap: 3px; align-items: center; min-width: 0;`
const Sel = styled.select`
  background: transparent; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  padding: 2px 4px; cursor: pointer; flex-shrink: 0; max-width: 100%;
  option { background: ${colors.bg.dropdown}; color: ${colors.text.secondary}; }
`
// The operator picker: a compact glyph button that opens a labelled menu (the native <select>
// could only show the glyph or the name, not both, and styled badly next to the rest of the UI).
const OpWrap = styled.div`position: relative; flex-shrink: 0;`
const OpBtn = styled.button<{ $open?: boolean }>`
  display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 24px;
  border: 1px solid ${({ $open }) => ($open ? colors.blue.border : colors.border)}; border-radius: ${radius.sm};
  background: ${({ $open }) => ($open ? colors.blue.bg : 'transparent')};
  color: ${({ $open }) => ($open ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.base}; font-family: ${fonts.sans}; cursor: pointer; line-height: 1; padding: 0;
  &:hover { border-color: ${colors.blue.border}; color: ${colors.text.primary}; }
`
const OpMenu = styled.div`
  position: absolute; top: calc(100% + 4px); left: 0; z-index: 200; min-width: 168px;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  padding: 4px; box-shadow: ${shadow.lg};
`
const OpItem = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 5px 8px;
  border: none; border-radius: ${radius.md}; cursor: pointer; text-align: left;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
  & .g { width: 16px; text-align: center; flex-shrink: 0; font-family: ${fonts.sans}; color: ${colors.text.muted}; }
  & .n { flex: 1; }
  & svg { flex-shrink: 0; }
`
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

/** The operator picker — a glyph button that opens a labelled menu of the operators valid for `kind`. */
function OpPicker({ kind, value, onChange }: { kind: 'text' | 'number' | 'date'; value: OpFilter['op']; onChange: (op: OpFilter['op']) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  return (
    <OpWrap ref={ref}>
      <OpBtn type="button" $open={open} onClick={() => setOpen((v) => !v)} title={`${t('table.filterOp', 'Operator')}: ${OP_NAME[value]}`}>
        {OP_LABEL[value]}
      </OpBtn>
      {open && (
        <OpMenu>
          {OPS_BY_KIND[kind].map((op) => (
            <OpItem key={op} type="button" $active={op === value} onClick={() => { onChange(op); setOpen(false) }}>
              <span className="g">{OP_LABEL[op]}</span>
              <span className="n">{t(`table.op_${op}`, OP_NAME[op])}</span>
              {op === value && <Check size={12} />}
            </OpItem>
          ))}
        </OpMenu>
      )}
    </OpWrap>
  )
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
      <OpPicker kind={kind} value={f.op} onChange={(op) => set({ op })} />
      {NEEDS_A(f.op) && (
        <Inp type={inputType(kind)} value={f.a ?? ''} onChange={(e) => set({ a: e.target.value })} placeholder="…" />
      )}
      {NEEDS_B(f.op) && (
        <Inp type={inputType(kind)} value={f.b ?? ''} onChange={(e) => set({ b: e.target.value })} placeholder="…" />
      )}
    </Box>
  )
}
