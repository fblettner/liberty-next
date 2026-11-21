import { describe, expect, it } from 'vitest'
import { type AppMap, dataKey, mapLinks, matches, menuKey, relatedTo, screenKey, serviceKey, usageOf } from './studioMap'

const map: AppMap = {
  apps: ['main'], app: 'main',
  menu: [{ id: 'ref', label: 'Reference', depth: 0, type: null, target: null, connector: null },
    { id: 'codes', label: 'Codes', depth: 1, type: 'screen', target: 'codes', connector: null },
    { id: 'gone', label: 'Gone', depth: 0, type: 'screen', target: 'no_such_screen', connector: null }],
  screens: [
    { id: 'codes', label: 'Codes', connector: 'main', read: 'main.codes_get', writes: ['main.code_put'], calls: ['studio.describe', 'main.codes_get'],
      services: ['describe'], nested: ['notes'], form: true, read_only: false, in_menu: true, reached: true },
    { id: 'notes', label: 'Notes', connector: 'main', read: 'main.notes_get', writes: [], calls: [], services: [], nested: [], form: false, read_only: true, in_menu: false, reached: true }],
  data: [
    { key: 'main.codes_get', connector: 'main', name: 'codes_get', kind: 'query', used: true, label: null, writable: false, service: null, missing: false },
    { key: 'studio.describe', connector: 'studio', name: 'describe', kind: 'endpoint', used: true, label: null, writable: false, service: 'describe', missing: false }],
  services: [{ id: 'describe', label: null, steps: 1, screens: ['codes'], endpoints: [], missing: false }],
  dashboards: [],
}

describe('the map of an application', () => {
  const links = mapLinks(map)
  it('links menu → screen → data → service, a screen to the screens inside its form, once each', () => {
    expect(links).toContainEqual([menuKey('codes'), screenKey('codes')])
    expect(links.filter(([a, b]) => a === screenKey('codes') && b === dataKey('main.codes_get'))).toHaveLength(1)   // read and called: one link
    expect(links).toContainEqual([screenKey('codes'), serviceKey('describe')])
    expect(links).toContainEqual([screenKey('codes'), screenKey('notes')])
    expect(links).toContainEqual([dataKey('studio.describe'), serviceKey('describe')])
    expect(links.some(([a]) => a === menuKey('gone'))).toBe(false)                                                  // a target that does not exist
  })
  it('says what a selection touches, both ways', () => {
    expect(relatedTo(links, null)).toBeNull()
    expect([...relatedTo(links, screenKey('codes'))!].sort()).toEqual(
      [dataKey('main.code_put'), dataKey('main.codes_get'), dataKey('studio.describe'), menuKey('codes'), screenKey('codes'), screenKey('notes'), serviceKey('describe')].sort())
    expect([...relatedTo(links, serviceKey('describe'))!].sort()).toEqual([dataKey('studio.describe'), screenKey('codes'), serviceKey('describe')].sort())
  })
  it('names how a screen uses a piece of data, and filters by text', () => {
    expect(usageOf(map.screens[0], 'main.codes_get')).toBe('reads')
    expect(usageOf(map.screens[0], 'main.code_put')).toBe('saves')
    expect(usageOf(map.screens[0], 'studio.describe')).toBe('calls')
    expect(usageOf(map.screens[1], 'main.code_put')).toBeNull()
    expect(matches('', 'x')).toBe(true)
    expect(matches(' COD ', 'Codes', null)).toBe(true)
    expect(matches('zzz', 'Codes', 'codes')).toBe(false)
  })
})
