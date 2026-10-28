import { describe, expect, it } from 'vitest'
import { tabApp } from './TabsContext'

describe('tabApp', () => {
  it('names the application a tab belongs to', () => {
    expect(tabApp({ kind: 'screen', connector: 'nomajde', target: 'f0092' })).toBe('nomajde')
    expect(tabApp({ kind: 'sql', connector: 'jdedwards', target: 'f0092_get' })).toBe('jdedwards')
    expect(tabApp({ kind: 'dashboard', connector: '', target: 'nomajde.overview' })).toBe('nomajde')
    expect(tabApp({ kind: 'page', connector: '', target: '/explorer/nomajde/security' })).toBe('nomajde')
    expect(tabApp({ kind: 'page', connector: '', target: '/explorer' })).toBeNull()
    expect(tabApp({ kind: 'settings', connector: '', target: '' })).toBeNull()
    expect(tabApp({ kind: 'job', connector: '', target: 'nomajde-dictionary-refresh' })).toBe('')
  })
})
