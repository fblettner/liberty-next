import { describe, expect, it } from 'vitest'
import { isPicked, toggleChoice, toMulti } from './multiChoice'

describe('multiChoice', () => {
  it('reads the shapes a form may hold', () => {
    expect(toMulti('all')).toBe('all')
    expect(toMulti(['1', 'all'])).toBe('all')
    expect(toMulti([1, 3])).toEqual(['1', '3'])
    expect(toMulti('1, 3')).toEqual(['1', '3'])
    expect(toMulti(2)).toEqual(['2'])
    expect(toMulti(null)).toEqual([])
  })
  it('ticks and unticks in the choices order', () => {
    const all = ['1', '2', '3']
    expect(toggleChoice(['3'], '1', all)).toEqual(['1', '3'])
    expect(toggleChoice(['1', '3'], '3', all)).toEqual(['1'])
    expect(toggleChoice('all', '2', all)).toEqual(['1', '3'])
    expect(isPicked('all', '2')).toBe(true)
    expect(isPicked(['1'], '2')).toBe(false)
  })
})
