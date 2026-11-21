import { describe, expect, it } from 'vitest'
import { edgeLabel, link, modelDiagram, place } from './explorerDiagram'

const MODEL = {
  hub: 'sales',
  entities: {
    sales: {}, item: {}, company: {}, unit: {}, lines: { cardinality: 'many' as const }, taxes: {}, orphan: {}, lonely: {},
  },
  relations: [
    { from: 'sales', to: 'item', on: [['ITEM', 'ITEM']] },
    { from: 'sales', to: 'company', on: [['CO', 'COMPANY_CODE'], ['BU', 'BU']] },
    { from: 'company', to: 'unit', on: [['BU', 'BU']] },
    { from: 'lines', to: 'sales', on: [['DOC', 'DOC']] },
    { from: 'lines', to: 'taxes', on: [] },
    { from: 'orphan', to: 'lonely', on: [['A', 'A']] },
    { from: 'sales', to: 'gone', on: [['X', 'X']] },
  ],
}

describe('an Explorer model as a diagram', () => {
  const d = modelDiagram(MODEL)
  const node = (id: string) => d.nodes.find((n) => n.id === id)!
  it('puts the hub in the middle, the details on its left, what it has one of on its right', () => {
    expect(node('sales')).toMatchObject({ role: 'hub', distance: 0, column: 2 })
    expect(node('lines')).toMatchObject({ role: 'many', distance: 1, column: 1 })
    expect(node('item')).toMatchObject({ role: 'one', distance: 1, column: 3, row: 0 })
    expect(node('company')).toMatchObject({ role: 'one', distance: 1, column: 3, row: 1 })
    expect(node('unit')).toMatchObject({ distance: 2, column: 4 })
    // what hangs from a detail stays on the detail's side
    expect(node('taxes')).toMatchObject({ role: 'one', distance: 2, column: 0 })
    expect(d.columns).toEqual([1, 1, 1, 2, 1])
  })
  it('sets apart what does not reach the hub', () => {
    expect(d.loose).toEqual(['orphan', 'lonely'])
    expect(node('orphan').role).toBe('loose')
  })
  it('says what each relation is worth', () => {
    expect(d.edges.map((e) => [e.from, e.to, e.complete, e.known])).toEqual([
      ['sales', 'item', true, true], ['sales', 'company', true, true], ['company', 'unit', true, true], ['lines', 'sales', true, true],
      ['lines', 'taxes', false, true], ['orphan', 'lonely', true, true], ['sales', 'gone', true, false],
    ])
    expect(d.edges.map(edgeLabel)).toEqual(['ITEM', '2 ×', 'BU', 'DOC', '?', 'A', 'X'])
    expect(edgeLabel({ index: 0, from: 'a', to: 'b', pairs: [['CO', 'COMPANY']], complete: true, known: true })).toBe('CO = COMPANY')
  })
  it('places the cards: a column per distance, centred, the loose ones in a row below', () => {
    const p = place(d, { w: 200, h: 60, gapX: 80, gapY: 20 })
    expect(p.boxes.sales).toEqual({ x: 560, y: 40, w: 200, h: 60 })        // alone in its column: centred on the two-row one
    expect(p.boxes.item).toEqual({ x: 840, y: 0, w: 200, h: 60 })
    expect(p.boxes.company).toEqual({ x: 840, y: 80, w: 200, h: 60 })
    expect(p.width).toBe(5 * 200 + 4 * 80)
    expect(p.looseTop).toBe(140 + 40 + 22)
    expect(p.boxes.orphan.y).toBe(202)
    expect(p.height).toBe(262)
  })
  it('draws a relation from the sides that face each other', () => {
    const { path, mid } = link({ x: 0, y: 0, w: 100, h: 40 }, { x: 200, y: 100, w: 100, h: 40 })
    expect(path).toBe('M 100 20 C 150 20, 150 120, 200 120')
    expect(mid).toEqual({ x: 150, y: 70 })
    expect(link({ x: 200, y: 0, w: 100, h: 40 }, { x: 0, y: 0, w: 100, h: 40 }).path.startsWith('M 200 20')).toBe(true)
    expect(link({ x: 0, y: 0, w: 100, h: 40 }, { x: 0, y: 100, w: 100, h: 40 }).path).toBe('M 100 20 C 146 20, 146 120, 100 120')
  })
  it('stands when the model has no hub yet', () => {
    const empty = modelDiagram({ hub: '', entities: { a: {}, b: {} }, relations: [] })
    expect(empty.columns).toEqual([])
    expect(empty.loose).toEqual(['a', 'b'])
    expect(place(empty, { w: 200, h: 60, gapX: 80, gapY: 20 })).toMatchObject({ looseTop: 22, height: 82 })
  })
})
