import { describe, expect, it } from 'vitest'
import { groupModules, screenTree, writes } from './modules'
import type { ModulePlan, ModuleSummary } from '../types/modules'

const ab: ModuleSummary = {
  id: 'address_book', label: 'Address Book', group: 'JDE', connector: 'jdedwards', app: 'nomajde', description: 'Master and contacts',
  screens: [
    { id: 'f0101', table: 'F0101', label: 'Address Book', details: ['f0111', 'f0401'] },
    { id: 'f0111', table: 'F0111', label: "Who's Who", details: ['f0115'] },
    { id: 'f0115', table: 'F0115', label: 'Phones', details: [] },
    { id: 'f0401', table: 'F0401', label: 'Supplier', details: [] },
  ],
}
const other: ModuleSummary = { ...ab, id: 'x', label: 'Another', group: null, description: null, screens: [{ id: 'f9', table: 'F9', label: 'Nine', details: [] }] }

describe('modules', () => {
  it('groups and searches the catalogue', () => {
    expect(groupModules([other, ab]).map(([g, l]) => [g, l.map((m) => m.id)])).toEqual([['JDE', ['address_book']], ['Other', ['x']]])
    expect(groupModules([other, ab], 'f0115').map(([, l]) => l[0].id)).toEqual(['address_book'])
    expect(groupModules([other, ab], 'contacts')).toHaveLength(1)
    expect(groupModules([other, ab], 'nothing')).toEqual([])
  })
  it('lays the screens out as a tree', () => {
    expect(screenTree(ab)).toEqual([{ id: 'f0101', depth: 0 }, { id: 'f0111', depth: 1 }, { id: 'f0115', depth: 2 }, { id: 'f0401', depth: 1 }])
  })
  it('counts what a plan writes', () => {
    const plan = { new: { queries: ['a_get'], screens: ['a'], menu_items: [] } } as unknown as ModulePlan
    expect(writes(plan)).toBe(2)
  })
})
