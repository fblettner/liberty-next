import { describe, expect, it } from 'vitest'
import { formatPairs, pairValue, parsePairs, setPair } from './pairs'

describe('pairs', () => {
  it('reads name = value lines; the first = separates', () => {
    expect(parsePairs('TABLE = F0004\n\n MAX=5 \nCOND = [{"a": "b=c"}]')).toEqual([
      { name: 'TABLE', value: 'F0004' }, { name: 'MAX', value: '5' }, { name: 'COND', value: '[{"a": "b=c"}]' }])
    expect(parsePairs('')).toEqual([])
    expect(parsePairs(null)).toEqual([])
    expect(parsePairs('KEPT = ')).toEqual([{ name: 'KEPT', value: '' }])
  })
  it('gives up on a line that is not a pair', () => {
    expect(parsePairs('TABLE = F0004\njust words')).toBeNull()
    expect(parsePairs('= no name')).toBeNull()
  })
  it('writes them back, a pair without a name left out', () => {
    const text = 'TABLE = F0004\nMAX = 5'
    expect(formatPairs(parsePairs(text)!)).toBe(text)
    expect(formatPairs([{ name: ' A ', value: ' 1 ' }, { name: '', value: 'x' }])).toBe('A = 1')
  })
  it('sets, adds and removes one value, whatever the case of the name', () => {
    const pairs = parsePairs('AN8 = 1\nAT1 = C')!
    expect(formatPairs(setPair(pairs, 'an8', '9999'))).toBe('AN8 = 9999\nAT1 = C')
    expect(formatPairs(setPair(pairs, 'MAX', '5'))).toBe('AN8 = 1\nAT1 = C\nMAX = 5')
    expect(formatPairs(setPair(pairs, 'AT1', ' '))).toBe('AN8 = 1')
    expect(formatPairs(setPair(pairs, 'NOPE', ''))).toBe('AN8 = 1\nAT1 = C')
    expect(pairValue(pairs, 'at1')).toBe('C')
    expect(pairValue(pairs, 'MAX')).toBe('')
  })
})
