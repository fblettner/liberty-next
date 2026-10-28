import { describe, expect, it } from 'vitest'
import { sharedToGridView } from './gridViews'

describe('sharedToGridView', () => {
  const all = ['aban8', 'abalph', 'abat1', 'abat1__lookup', 'abmcu', 'abtaxc', 'abtaxc__enum']
  it('keeps a lookup / enum column\'s label right after its code', () => {
    const v = sharedToGridView({ name: 'Default', columns: ['ABAN8', 'ABAT1', 'ABTAXC'] }, all)
    expect(v.order).toEqual(['aban8', 'abat1', 'abat1__lookup', 'abtaxc', 'abtaxc__enum'])
    expect(v.visibility).toMatchObject({ abat1__lookup: true, abtaxc__enum: true, abalph: false, abmcu: false })
  })
  it('lets the view place (or leave out) the label itself', () => {
    const v = sharedToGridView({ name: 'Codes', columns: ['abat1__lookup', 'aban8', 'abat1'] }, all)
    expect(v.order).toEqual(['abat1__lookup', 'aban8', 'abat1'])
  })
})
