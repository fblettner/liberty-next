import { describe, expect, it } from 'vitest'
import { LIBERTY_APP, isLibertyApp, libertyLabel, libertyMenu } from './libertyMenu'
import { leafPath } from './menuPath'
import type { MenuNode } from '../types/menus'

const t = (k: string) => k
const leaves = (ns: MenuNode[]): MenuNode[] => ns.flatMap((n) => (n.items ? leaves(n.items) : [n]))

describe('libertyMenu', () => {
  it('groups the framework pages under folders, each leaf a route or an action', () => {
    const m = libertyMenu(t)
    expect(m.app).toBe(LIBERTY_APP)
    expect(m.items.every((n) => n.items && n.items.length > 0)).toBe(true)
    for (const l of leaves(m.items)) expect(l.action ?? leafPath(l, m.app)).toBeTruthy()
    expect(leaves(m.items).map((l) => l.target)).toEqual(expect.arrayContaining(['/developer', '/liberty/reports', '/monitoring']))
  })
  it('names a tab from its route', () => {
    const m = libertyMenu(t)
    expect(libertyLabel(m, '/liberty/reports')).toBe('docReports.title')
    expect(libertyLabel(m, '/nowhere')).toBeUndefined()
    expect(isLibertyApp('@liberty')).toBe(true)
    expect(isLibertyApp('nomasx1')).toBe(false)
  })
})
