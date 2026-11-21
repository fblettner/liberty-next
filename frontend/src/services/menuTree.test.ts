import { describe, expect, it } from 'vitest'
import { dropInTree, dropZone, itemKind, subtree } from './menuTree'

const MENU = [
  { id: 'overview', type: 'dashboard' },
  { id: 'sales' },
  { id: 'lines', parent: 'sales', type: 'screen' },
  { id: 'periods', parent: 'sales', type: 'screen' },
  { id: 'ref' },
  { id: 'items', parent: 'ref', type: 'screen' },
  { id: 'jobs', type: 'page' },
]
const shape = (items: { id: string; parent?: string | null }[]) => items.map((it) => (it.parent ? `${it.parent}/${it.id}` : it.id))

describe('a menu as a tree', () => {
  it('takes an item with everything under it', () => {
    expect(subtree(MENU, 'sales').map((it) => it.id)).toEqual(['sales', 'lines', 'periods'])
    expect(subtree(MENU, 'jobs').map((it) => it.id)).toEqual(['jobs'])
    expect(subtree(MENU, 'gone')).toEqual([])
  })
  it('drops an item before or after another, at that one\'s level', () => {
    expect(shape(dropInTree(MENU, 'jobs', 'overview', 'before'))).toEqual(['jobs', 'overview', 'sales', 'sales/lines', 'sales/periods', 'ref', 'ref/items'])
    expect(shape(dropInTree(MENU, 'items', 'lines', 'after'))).toEqual(['overview', 'sales', 'sales/lines', 'sales/items', 'sales/periods', 'ref', 'jobs'])
    // after a folder = after everything it holds
    expect(shape(dropInTree(MENU, 'overview', 'sales', 'after'))).toEqual(['sales', 'sales/lines', 'sales/periods', 'overview', 'ref', 'ref/items', 'jobs'])
    // out of its folder, to the top level
    expect(shape(dropInTree(MENU, 'lines', 'jobs', 'before'))).toEqual(['overview', 'sales', 'sales/periods', 'ref', 'ref/items', 'lines', 'jobs'])
    expect(dropInTree(MENU, 'lines', 'jobs', 'before').find((it) => it.id === 'lines')).toEqual({ id: 'lines', type: 'screen' })
  })
  it('drops an item into a folder, at its end, with its own children', () => {
    expect(shape(dropInTree(MENU, 'jobs', 'sales', 'into'))).toEqual(['overview', 'sales', 'sales/lines', 'sales/periods', 'sales/jobs', 'ref', 'ref/items'])
    expect(shape(dropInTree(MENU, 'ref', 'sales', 'into'))).toEqual(['overview', 'sales', 'sales/lines', 'sales/periods', 'sales/ref', 'ref/items', 'jobs'])
  })
  it('refuses a drop that means nothing', () => {
    expect(dropInTree(MENU, 'sales', 'sales', 'into')).toBe(MENU)
    expect(dropInTree(MENU, 'sales', 'lines', 'into')).toBe(MENU)       // into its own subtree
    expect(dropInTree(MENU, 'lines', 'periods', 'before')).toBe(MENU)   // where it already is
    expect(dropInTree(MENU, 'periods', 'sales', 'into')).toBe(MENU)     // already the last of that folder
    expect(dropInTree(MENU, 'gone', 'sales', 'into')).toBe(MENU)
  })
  it('reads the pointer over a row', () => {
    expect(dropZone(102, 100, 40, true)).toBe('before')
    expect(dropZone(120, 100, 40, true)).toBe('into')
    expect(dropZone(138, 100, 40, true)).toBe('after')
    expect(dropZone(115, 100, 40, false)).toBe('before')
    expect(dropZone(125, 100, 40, false)).toBe('after')
  })
  it('names what an item is', () => {
    expect(itemKind({ id: 'a' })).toBe('folder')
    expect(itemKind({ id: 'a', type: 'screen' })).toBe('screen')
  })
})
