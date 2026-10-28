import { describe, expect, it } from 'vitest'
import { findMenuItem, jobTarget, leafPath, splitJobTarget } from './menuPath'

describe('menuPath', () => {
  it('routes every leaf kind', () => {
    expect(leafPath({ type: 'dashboard', target: 'nomajde.overview' }, 'nomajde')).toBe('/dashboard/nomajde.overview')
    expect(leafPath({ type: 'job', target: 'nomajde-dictionary-refresh' }, 'nomajde')).toBe('/job/nomajde-dictionary-refresh')
    expect(leafPath({ type: 'page', target: '/explorer' }, 'nomajde')).toBe('/explorer')
    expect(leafPath({ type: 'screen', target: 'f0092' }, 'nomajde')).toBe('/screen/nomajde/f0092')
    expect(leafPath({ type: 'query', target: 'f0092_get', connector: 'jdedwards' }, 'nomajde')).toBe('/sql/jdedwards/f0092_get')
    expect(leafPath({ type: 'endpoint', target: 'ping', connector: 'ais' }, 'nomajde')).toBe('/http/ais/ping')
    expect(leafPath({ target: undefined }, 'nomajde')).toBeNull()
  })
  it('carries a pinned preset in a job leaf', () => {
    const node = { type: 'job' as const, target: 'nomasx1-collect-all', params: { preset: 'Security' } }
    expect(jobTarget(node)).toBe('nomasx1-collect-all::Security')
    expect(leafPath(node, 'nomasx1')).toBe('/job/nomasx1-collect-all%3A%3ASecurity')
    expect(splitJobTarget('nomasx1-collect-all::Security')).toEqual({ jobId: 'nomasx1-collect-all', preset: 'Security' })
    expect(splitJobTarget('nomasx1-roles')).toEqual({ jobId: 'nomasx1-roles', preset: null })
  })
  it('finds an item at any depth, only in the asked app', () => {
    const menus = { nomajde: { app: 'nomajde', label: 'JDE', items: [{ id: 'sec', label: 'Security', items: [{ id: 'users', label: 'Users', type: 'screen' as const, target: 'f0092' }] }] } }
    expect(findMenuItem(menus, 'nomajde', 'users')?.target).toBe('f0092')
    expect(findMenuItem(menus, 'nomajde', 'ghost')).toBeUndefined()
    expect(findMenuItem(menus, 'other', 'users')).toBeUndefined()
  })
})
