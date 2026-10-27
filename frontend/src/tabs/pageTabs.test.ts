import { describe, expect, it } from 'vitest'
import { explorerModelOf, isPageTabPath } from './pageTabs'

describe('pageTabs', () => {
  it('recognises the fixed page paths and an Explorer model link', () => {
    expect(isPageTabPath('/explorer')).toBe(true)
    expect(isPageTabPath('/explorer/nomasx1/sod')).toBe(true)
    expect(isPageTabPath('/explorer/nomasx1')).toBe(false)
    expect(isPageTabPath('/sql/x/y')).toBe(false)
    expect(explorerModelOf('/explorer/nomasx1/sod')).toEqual({ app: 'nomasx1', model: 'sod' })
    expect(explorerModelOf('/explorer/my%20app/m')).toEqual({ app: 'my app', model: 'm' })
    expect(explorerModelOf('/explorer')).toBeNull()
  })
})
