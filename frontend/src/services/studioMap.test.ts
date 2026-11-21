import { describe, expect, it } from 'vitest'
import { type AppMap, dataKey, mapLinks, matches, menuKey, reachOf, screenKey, serviceKey, usageOf } from './studioMap'

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
  it('follows the links all the way from a selection: down to the data, up to the menu', () => {
    expect(reachOf(links, null)).toBeNull()
    const keys = (key: string) => [...reachOf(links, key)!.nodes].sort()
    // a screen: its menu entry above; below, its data, its service, the screen inside its form AND that screen's data
    expect(keys(screenKey('codes'))).toEqual(
      [dataKey('main.code_put'), dataKey('main.codes_get'), dataKey('main.notes_get'), dataKey('studio.describe'), menuKey('codes'),
        screenKey('codes'), screenKey('notes'), serviceKey('describe')].sort())
    // a query: the screens that use it, and their menu entries
    expect(keys(dataKey('main.codes_get'))).toEqual([dataKey('main.codes_get'), menuKey('codes'), screenKey('codes')].sort())
    // the data of a screen opened inside another's form: that screen, the one that opens it, its menu entry
    expect(keys(dataKey('main.notes_get'))).toEqual([dataKey('main.notes_get'), menuKey('codes'), screenKey('codes'), screenKey('notes')].sort())
    // a menu entry: everything its screen leads to
    expect(keys(menuKey('codes'))).toContain(serviceKey('describe'))
    // a service: the endpoint it serves, the screens that call it, their menu — not those screens' other data
    expect(keys(serviceKey('describe'))).toEqual([dataKey('studio.describe'), menuKey('codes'), screenKey('codes'), serviceKey('describe')].sort())
    // the links to draw are those on the way, each once
    expect(reachOf(links, dataKey('main.codes_get'))!.links).toEqual([[menuKey('codes'), screenKey('codes')], [screenKey('codes'), dataKey('main.codes_get')]])
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
