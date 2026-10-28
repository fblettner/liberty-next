import { describe, expect, it } from 'vitest'
import { basePathOf, joinBase, routerBasename } from './basePath'

describe('basePathOf', () => {
  it('defaults to the root', () => {
    expect(basePathOf(null)).toBe('/')
    expect(basePathOf('')).toBe('/')
  })
  it('reads the path of the base href, trailing slash added', () => {
    expect(basePathOf('https://host/liberty/')).toBe('/liberty/')
    expect(basePathOf('https://host/liberty')).toBe('/liberty/')
    expect(basePathOf('/apps/liberty/')).toBe('/apps/liberty/')
    expect(basePathOf('http://localhost:5173/')).toBe('/')
  })
})

describe('joinBase', () => {
  it('prefixes absolute app paths', () => {
    expect(joinBase('/', '/api/x')).toBe('/api/x')
    expect(joinBase('/liberty/', '/api/x?a=1')).toBe('/liberty/api/x?a=1')
  })
  it('leaves relative and full URLs alone', () => {
    expect(joinBase('/liberty/', 'favicon.svg')).toBe('favicon.svg')
    expect(joinBase('/liberty/', 'https://elsewhere/x')).toBe('https://elsewhere/x')
    expect(joinBase('/liberty/', '//cdn/x')).toBe('//cdn/x')
  })
})

describe('routerBasename', () => {
  it('drops the trailing slash; empty at the root', () => {
    expect(routerBasename('/')).toBe('')
    expect(routerBasename('/liberty/')).toBe('/liberty')
  })
})
