import { describe, expect, it } from 'vitest'
import { APP_SECTION_GROUPS, APP_SECTION_KEYS, groupedSections, pickedSection, splitLabel } from './appSections'

describe("the App editor's sections", () => {
  it('puts every section in one group, once', () => {
    expect([...APP_SECTION_KEYS].sort()).toEqual(['ai', 'app', 'auth', 'branding', 'changesets', 'display', 'jobs', 'license', 'links', 'log', 'notify', 'oidc', 'readonly'])
    expect(new Set(APP_SECTION_KEYS).size).toBe(APP_SECTION_KEYS.length)
    expect(APP_SECTION_GROUPS.map((g) => g.id)).toEqual(['installation', 'signin', 'features', 'operate'])
  })
  it('lists the sections the page has, in the groups order, and drops an empty group', () => {
    const got = groupedSections([{ key: 'oidc' as const }, { key: 'app' as const }, { key: 'auth' as const }])
    expect(got.map((g) => [g.id, g.sections.map((s) => s.key)])).toEqual([['installation', ['app']], ['signin', ['auth', 'oidc']]])
  })
  it('shows the remembered section when it is still there, else the first', () => {
    const sections = [{ key: 'log' as const }, { key: 'display' as const }]
    expect(pickedSection(sections, 'log')).toBe('log')
    expect(pickedSection(sections, 'readonly')).toBe('display')
    expect(pickedSection(sections, null)).toBe('display')
    expect(pickedSection([], 'log')).toBeNull()
  })
})

describe("a field's label and its explanation", () => {
  it('moves a long parenthesis under the field', () => {
    expect(splitLabel('Environment (production, test… — named in the licence usage report)')).toEqual({ label: 'Environment', help: 'production, test… — named in the licence usage report' })
    expect(splitLabel('Dashboard cache (seconds, 0 = none)')).toEqual({ label: 'Dashboard cache', help: 'seconds, 0 = none' })
  })
  it('keeps a unit or a short word in the label', () => {
    expect(splitLabel('Request timeout (s)')).toEqual({ label: 'Request timeout (s)', help: '' })
    expect(splitLabel('Default period (dashboards)')).toEqual({ label: 'Default period (dashboards)', help: '' })
    expect(splitLabel('Session (access token) lifetime (s)')).toEqual({ label: 'Session (access token) lifetime (s)', help: '' })
    expect(splitLabel('Port')).toEqual({ label: 'Port', help: '' })
  })
})
