import { describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({ api: { get: vi.fn() } }))

import { callableOptions, callableParts, groupOf, plainDoc } from './callables'

describe('callableParts', () => {
  it('reads the application, the module and the name', () => {
    expect(callableParts('nomasx1.security:j_users')).toEqual({ app: 'nomasx1', module: 'security', name: 'j_users' })
    expect(callableParts('nomajde.jobs.nightly:j_run')).toEqual({ app: 'nomajde', module: 'jobs.nightly', name: 'j_run' })
    expect(callableParts('tools:j_one')).toEqual({ app: 'tools', module: '', name: 'j_one' })
    expect(callableParts('')).toEqual({ app: '', module: '', name: '' })
  })
})

describe('plainDoc', () => {
  it('drops the marks of the documentation', () => {
    expect(plainDoc('Refresh ``jde_objects`` for *apps_id* + apply the settings.')).toBe('Refresh jde_objects for apps_id + apply the settings.')
    expect(plainDoc('Play a suite (:mod:`liberty.apptests`) of **every** `case`')).toBe('Play a suite (liberty.apptests) of every case')
  })
  it('keeps a lone star and answers nothing for nothing', () => {
    expect(plainDoc('role-menu * user')).toBe('role-menu * user')
    expect(plainDoc(null)).toBe('')
  })
})

describe('callableOptions', () => {
  const catalog = [
    { callable: 'nomasx1.sod:j_conflicts', docstring: 'A list of conflicts.' },
    { callable: 'nomajde.xref:j_view', docstring: 'A business view.' },
    { callable: 'nomasx1.security:j_users', docstring: 'Refresh ``users``.' },
    { callable: 'nomasx1.security:j_roles', docstring: null },
    { callable: 'nomajde.xref:j_view', docstring: 'twice' },
  ]
  it('groups by application and module, by name inside — never by description', () => {
    const o = callableOptions(catalog)
    expect(o.map((x) => x.value)).toEqual([
      'nomajde.xref:j_view', 'nomasx1.security:j_roles', 'nomasx1.security:j_users', 'nomasx1.sod:j_conflicts',
    ])
    expect(o[1]).toEqual({ value: 'nomasx1.security:j_roles', label: 'j_roles', mono: 'j_roles', group: 'nomasx1 · security' })
    expect(o[2].label).toBe('Refresh users.')
  })
  it('puts first the application of the function already picked', () => {
    expect(callableOptions(catalog, 'nomasx1.other:j_x').map((x) => x.group)).toEqual([
      'nomasx1 · security', 'nomasx1 · security', 'nomasx1 · sod', 'nomajde · xref',
    ])
  })
  it('names the group of a function without module', () => {
    expect(groupOf('tools:j_one')).toBe('tools')
  })
})
