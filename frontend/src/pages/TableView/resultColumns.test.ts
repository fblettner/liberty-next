// The data grid's column helpers (resultColumns.ts): alignment, filter kind, inline-edit control, filter props
// and visible_when — the behaviour ResultTable had when they moved out of it.
import { describe, expect, it } from 'vitest'
import type { Column } from '../../types/connectors'
import { cellAlign, colHeader, columnVisibleNow, editCtrlOf, filterKindOf, filterPropsFor } from './resultColumns'

const col = (over: Partial<Column> = {}): Column => ({ name: 'C', type: 'varchar', ...over } as Column)

describe('column helpers', () => {
  it('header and alignment: an explicit align wins, booleans centred, numbers right', () => {
    expect(colHeader(col({ label: 'Code' }))).toBe('Code')
    expect(colHeader(col())).toBe('C')
    expect(cellAlign(col({ align: 'left', type: 'integer' }))).toBe('left')
    expect(cellAlign(col({ rule: { kind: 'boolean' } } as Partial<Column>))).toBe('center')
    expect(cellAlign(col({ type: 'numeric' }))).toBe('right')
    expect(cellAlign(col({ format: 'number' }))).toBe('right')
    expect(cellAlign(col())).toBeUndefined()
  })

  it('filter kind and edit control follow the rule, then the date / number type', () => {
    expect(filterKindOf(col({ rule: { kind: 'enum' } } as Partial<Column>))).toBe('enum')
    expect(filterKindOf(col({ format: 'jdedate', type: 'integer' }))).toBe('date')   // a JDE Julian date filters as a date
    expect(filterKindOf(col({ type: 'timestamp' }))).toBe('date')
    expect(filterKindOf(col({ type: 'decimal' }))).toBe('number')
    expect(filterKindOf(col({ rule: { kind: 'lookup' } } as Partial<Column>))).toBe('text')   // a lookup filters on its text
    expect(editCtrlOf(col({ rule: { kind: 'lookup' } } as Partial<Column>))).toBe('lookup')
    expect(editCtrlOf(col({ rule: { kind: 'boolean' } } as Partial<Column>))).toBe('boolean')
    expect(editCtrlOf(col({ format: 'jdedate', type: 'integer' }))).toBe('date')
    expect(editCtrlOf(col())).toBe('text')
  })

  it('filter props: enum multi + custom, lookup single, boolean strict equals', () => {
    const e = filterPropsFor('enum', [{ value: 'A', label: 'a' }])
    expect(e.meta.filter).toMatchObject({ kind: 'enum', multi: true, custom: true })
    expect(filterPropsFor('lookup').meta.filter).toMatchObject({ kind: 'lookup', multi: false })
    expect(filterPropsFor('boolean').filterFn).toBe('equals')
    expect(filterPropsFor('text', undefined, 'right', undefined, true).meta).toMatchObject({ filter: { kind: 'text' }, align: 'right', wrap: true })
  })

  it('visible_when: shown without the filter, trims a JDE-padded code, accepts a list', () => {
    const c = col({ visible_when: { field: 'TYPE', value: '1' } } as Partial<Column>)
    expect(columnVisibleNow(c, {})).toBe(true)
    expect(columnVisibleNow(c, { TYPE: '1         ' })).toBe(true)
    expect(columnVisibleNow(c, { TYPE: '2' })).toBe(false)
    const many = col({ visible_when: [{ field: 'TYPE', value: ['1', '3'] }] } as Partial<Column>)
    expect(columnVisibleNow(many, { TYPE: '3' })).toBe(true)
    expect(columnVisibleNow(col(), { TYPE: 'x' })).toBe(true)
  })
})
