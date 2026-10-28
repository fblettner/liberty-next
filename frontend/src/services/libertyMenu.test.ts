import { describe, expect, it } from 'vitest'
import { LIBERTY_APP, LIBERTY_PAGES, isLibertyApp, libertyIcon, libertyLabel, libertyMenu, libertyPageOf, settingsRedirect } from './libertyMenu'
import { leafPath } from './menuPath'
import type { MenuNode } from '../types/menus'

const t = (k: string) => k
const leaves = (ns: MenuNode[]): MenuNode[] => ns.flatMap((n) => (n.items ? leaves(n.items) : [n]))

describe('libertyMenu', () => {
  it('groups the framework pages under folders, each leaf a route or an action', () => {
    const m = libertyMenu(t)
    expect(m.app).toBe(LIBERTY_APP)
    expect(m.items.map((n) => n.id)).toEqual(['build', 'administer', 'operate', 'deploy', 'document'])
    for (const l of leaves(m.items)) expect(l.action ?? leafPath(l, m.app)).toBeTruthy()
  })
  it('lists every Liberty page exactly once, in the menu order', () => {
    const targets = leaves(libertyMenu(t).items).map((l) => l.target)
    expect(targets.filter((x) => x?.startsWith('/liberty/'))).toEqual(LIBERTY_PAGES.map((p) => `/liberty/${p}`))
    for (const p of LIBERTY_PAGES) expect(targets.filter((x) => x === `/liberty/${p}`)).toHaveLength(1)
    expect(targets).toEqual(expect.arrayContaining(['/developer', '/monitoring', '/nomaflow']))
  })
  it('names a tab and its icon from its route', () => {
    const m = libertyMenu(t)
    expect(libertyLabel(m, '/liberty/reports')).toBe('docReports.title')
    expect(libertyIcon(m, '/liberty/integrity')).toBe('shieldcheck')
    expect(libertyLabel(m, '/nowhere')).toBeUndefined()
    expect(isLibertyApp('@liberty')).toBe(true)
    expect(isLibertyApp('nomasx1')).toBe(false)
  })
  it('reads a page from a path', () => {
    expect(libertyPageOf('/liberty/screens')).toBe('screens')
    expect(libertyPageOf('/liberty/nope')).toBeNull()
    expect(libertyPageOf('/liberty/screens/x')).toBeNull()
  })
  it('redirects the former Settings tabs, keeping their parameters', () => {
    expect(settingsRedirect('?tab=screens&app=nomasx1&screen=users')).toBe('/liberty/screens?app=nomasx1&screen=users')
    expect(settingsRedirect('?tab=reports')).toBe('/liberty/report-templates')
    expect(settingsRedirect('?tab=releasenotes')).toBe('/liberty/release-notes')
    expect(settingsRedirect('')).toBe('/liberty/connectors')
    expect(settingsRedirect('?tab=bogus')).toBe('/liberty/connectors')
  })
})
