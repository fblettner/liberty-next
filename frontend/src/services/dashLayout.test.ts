import { describe, expect, it } from 'vitest'
import { dropSide, dropped, resized, stepped } from './dashLayout'

describe("a dashboard's cards", () => {
  it('drops a card before or after another', () => {
    const cards = ['a', 'b', 'c', 'd']
    expect(dropped(cards, 0, 2, false)).toEqual(['b', 'a', 'c', 'd'])   // a before c
    expect(dropped(cards, 0, 2, true)).toEqual(['b', 'c', 'a', 'd'])    // a after c
    expect(dropped(cards, 3, 0, false)).toEqual(['d', 'a', 'b', 'c'])   // d before a
    expect(dropped(cards, 3, 1, true)).toEqual(['a', 'b', 'd', 'c'])    // d after b
    expect(dropped(cards, 1, 0, true)).toBe(cards)                      // b after a: where it is
    expect(dropped(cards, 1, 2, false)).toBe(cards)                     // b before c: where it is
    expect(dropped(cards, 1, 1, true)).toBe(cards)
    expect(dropped(cards, 9, 0, false)).toBe(cards)
  })
  it('says on which side of the card under the pointer the dragged one would land', () => {
    expect(dropSide(0, 2, 110, 100, 100)).toBe('before')   // the left half
    expect(dropSide(0, 2, 190, 100, 100)).toBe('after')    // the right half
    expect(dropSide(2, 2, 150, 100, 100)).toBeNull()       // on itself
    expect(dropSide(1, 2, 110, 100, 100)).toBeNull()       // before its next: where it is
    expect(dropSide(2, 1, 190, 100, 100)).toBeNull()       // after its previous: where it is
  })
  it('sizes a card by its corner, in whole columns and rows', () => {
    expect(resized(6, 2, 210, 0, 100, 164)).toEqual({ col_span: 8, row_span: 2 })
    expect(resized(6, 2, -40, 170, 100, 164)).toEqual({ col_span: 6, row_span: 3 })
    expect(resized(6, 2, 49, -400, 100, 164)).toEqual({ col_span: 6, row_span: 1 })      // never less than one row
    expect(resized(10, 7, 900, 900, 100, 164)).toEqual({ col_span: 12, row_span: 8 })    // nor more than the grid
    expect(resized(2, 2, -900, 0, 100, 164)).toEqual({ col_span: 1, row_span: 2 })
  })
  it('sizes a card by steps', () => {
    expect(stepped(6, 2, 1, 0)).toEqual({ col_span: 7, row_span: 2 })
    expect(stepped(12, 8, 1, 1)).toEqual({ col_span: 12, row_span: 8 })
    expect(stepped(1, 1, -1, -1)).toEqual({ col_span: 1, row_span: 1 })
  })
})
