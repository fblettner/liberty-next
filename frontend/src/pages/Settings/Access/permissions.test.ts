// The role editor's model: menu forest, the backend's glob matcher, and the round trip between
// permission strings and the tree's inherit / allow / deny states.
import { describe, expect, it } from 'vitest'
import { buildMenuForest, buildPermissions, hasPermission, isDenied, leafVisible, nodeEffects, parsePermissions, segmentMatch } from './permissions'

const menus = {
  nomasx1: { items: [
    { id: 'security', label: 'Security' },
    { id: 'users', parent: 'security', label: 'Users', type: 'screen', target: 'users' },
    { id: 'roles', parent: 'security', label: 'Roles', type: 'query', target: 'security_roles_get' },
    { id: 'board', label: 'Board', type: 'dashboard', target: 'nomasx1.employees' },
    { id: 'help', label: 'Help', type: 'page', target: '/help' },
  ] },
  nomajde: { items: [
    { id: 'ais', label: 'AIS', type: 'endpoint', connector: 'ais_connection', target: 'report' },
  ] },
}
const screens = { nomasx1: [{ id: 'users', connector: 'nomasx1', read_query: 'security_users_get' }] }
const forest = buildMenuForest(menus, screens)

describe('buildMenuForest', () => {
  it('nests children under their parent and resolves every leaf target like the backend', () => {
    expect(forest.nomasx1.map((n) => n.id)).toEqual(['security', 'board', 'help'])
    const sec = forest.nomasx1[0]
    expect(sec.children.map((n) => n.id)).toEqual(['users', 'roles'])
    expect(sec.children[0].targetPerm).toBe('sql:nomasx1:security_users_get')     // screen → its read query
    expect(sec.children[1].targetPerm).toBe('sql:nomasx1:security_roles_get')
    expect(forest.nomasx1[1].targetPerm).toBe('dashboard:nomasx1.employees')
    expect(forest.nomasx1[2].targetPerm).toBeNull()
    expect(forest.nomajde[0].targetPerm).toBe('api:ais_connection:report')
  })
})

describe('segmentMatch / hasPermission', () => {
  it('mirrors Principal.has_permission: trailing * covers the rest, deny wins', () => {
    expect(segmentMatch('sql:*', 'sql:a:b')).toBe(true)
    expect(segmentMatch('sql:*:b', 'sql:a:b')).toBe(true)
    expect(segmentMatch('sql:a', 'sql:a:b')).toBe(false)
    expect(segmentMatch('*', 'anything:at:all')).toBe(true)
    expect(hasPermission(['*', '!sql:a:*'], 'sql:a:b')).toBe(false)
    expect(hasPermission(['sql:a:*'], 'sql:a:b')).toBe(true)
    expect(isDenied(['!menu:x:y'], 'menu:x:y')).toBe(true)
  })
})

describe('parsePermissions / buildPermissions', () => {
  it('reads a chip-era role (menu + target per leaf) as explicit leaf states', () => {
    const m = parsePermissions(['menu:nomasx1:users', 'sql:nomasx1:security_users_get', 'ai:chat'], forest)
    expect(m.full).toBe(false)
    expect(m.states).toEqual({ 'menu:nomasx1:users': 'allow' })
    expect(m.other).toEqual(['ai:chat'])
    expect(buildPermissions(m, forest)).toEqual(['menu:nomasx1:users', 'sql:nomasx1:security_users_get', 'ai:chat'])
  })

  it('a folder state expands to its leaves and is stable through a round trip', () => {
    const m = { full: false, states: { 'menu:nomasx1:security': 'allow' as const }, other: [] }
    const perms = buildPermissions(m, forest)
    expect(perms).toEqual([
      'menu:nomasx1:security',
      'menu:nomasx1:users', 'sql:nomasx1:security_users_get',
      'menu:nomasx1:roles', 'sql:nomasx1:security_roles_get',
    ])
    expect(parsePermissions(perms, forest)).toEqual(m)   // the leaves' entries are inherited, not explicit
  })

  it('full access minus a folder, with one leaf allowed back', () => {
    const m = { full: true, states: { 'menu:nomasx1:security': 'deny' as const, 'menu:nomasx1:roles': 'allow' as const }, other: [] }
    const perms = buildPermissions(m, forest)
    expect(perms).toEqual(['*', '!menu:nomasx1:security', '!menu:nomasx1:users', '!sql:nomasx1:security_users_get', 'menu:nomasx1:roles', 'sql:nomasx1:security_roles_get'])
    expect(parsePermissions(perms, forest)).toEqual(m)
    const eff = nodeEffects(m, forest)
    expect(eff['menu:nomasx1:users']).toEqual({ own: null, inherited: 'deny', visible: false })
    expect(eff['menu:nomasx1:roles']).toEqual({ own: 'allow', inherited: 'deny', visible: true })
    expect(eff['menu:nomasx1:board']).toEqual({ own: null, inherited: 'allow', visible: true })
  })

  it('an explicit allow equal to the baseline is dropped; a hand-written target grant shows on its leaf', () => {
    expect(parsePermissions(['*', 'menu:nomasx1:users'], forest).states).toEqual({})
    const m = parsePermissions(['sql:nomasx1:security_roles_get'], forest)
    expect(m.states).toEqual({ 'menu:nomasx1:roles': 'allow' })
    expect(m.other).toEqual([])
  })

  it('globs stay in "other" and still decide what a leaf shows', () => {
    const m = parsePermissions(['sql:nomasx1:*'], forest)
    expect(m.states).toEqual({})
    expect(m.other).toEqual(['sql:nomasx1:*'])
    const eff = nodeEffects(m, forest)
    expect(eff['menu:nomasx1:users'].visible).toBe(true)     // runnable → the sidebar shows it
    expect(eff['menu:nomajde:ais'].visible).toBe(false)
    expect(leafVisible(['!dashboard:nomasx1.employees'], forest.nomasx1[1])).toBe(false)
    expect(leafVisible([], forest.nomasx1[2])).toBe(true)     // a page is always shown
  })
})
