import { describe, expect, it } from 'vitest'
import { checkMenus, cleanMenus } from './menuChecks'

describe('cleanMenus', () => {
  it('drops a fixed value that has no name, and an empty list of them', () => {
    const cleaned = cleanMenus({ demo: { label: 'Demo', items: [
      { id: 'a', label: 'A', type: 'screen', target: 'a', params: { PERIOD: '2026-01', '': 'x', ' ': 'y' } },
      { id: 'b', label: 'B', type: 'screen', target: 'b', params: { '': '' } },
      { id: 'c', label: 'C' },
    ] } })
    expect(cleaned.demo.label).toBe('Demo')
    expect(cleaned.demo.items).toEqual([
      { id: 'a', label: 'A', type: 'screen', target: 'a', params: { PERIOD: '2026-01' } },
      { id: 'b', label: 'B', type: 'screen', target: 'b' },
      { id: 'c', label: 'C' },
    ])
  })
})

describe('checkMenus', () => {
  it('finds nothing in a menu that is whole', () => {
    expect(checkMenus({ demo: { items: [
      { id: 'sales', label: 'Sales' },
      { id: 'orders', label: 'Orders', parent: 'sales', type: 'screen', target: 'orders' },
    ] } })).toEqual([])
  })
  it('finds an entry that opens nothing — a folder is not one', () => {
    expect(checkMenus({ demo: { items: [
      { id: 'sales', label: 'Sales' },
      { id: 'orders', label: 'Orders', type: 'screen', target: '  ' },
      { id: 'home', label: 'Home', type: 'dashboard' },
    ] } })).toEqual([
      { code: 'target', app: 'demo', index: 1, name: 'orders' },
      { code: 'target', app: 'demo', index: 2, name: 'home' },
    ])
  })
  it('finds two entries with the same id, in one application only', () => {
    expect(checkMenus({
      demo: { items: [{ id: 'a', label: 'A' }, { id: 'a', label: 'A again' }] },
      other: { items: [{ id: 'a', label: 'A' }] },
    })).toEqual([{ code: 'duplicate', app: 'demo', index: 1, name: 'a' }])
  })
  it('finds an entry under a folder that is gone', () => {
    expect(checkMenus({ demo: { items: [{ id: 'orders', label: 'Orders', parent: 'sales', type: 'screen', target: 'orders' }] } }))
      .toEqual([{ code: 'parent', app: 'demo', index: 0, name: 'sales' }])
  })
  it('takes an application with no entry', () => {
    expect(checkMenus({ demo: {} })).toEqual([])
  })
})
