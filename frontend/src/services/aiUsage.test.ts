import { describe, expect, it } from 'vitest'
import { average, cachedShare, formatDuration, formatTokens, tokensIn } from './aiUsage'

describe('aiUsage helpers', () => {
  it('adds up everything the model read', () => {
    expect(tokensIn({ input_tokens: 100, cache_read_tokens: 800, cache_creation_tokens: 100 })).toBe(1000)
  })
  it('gives the share read from the cache', () => {
    expect(cachedShare({ input_tokens: 100, cache_read_tokens: 800, cache_creation_tokens: 100 })).toBe(80)
    expect(cachedShare({ input_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0 })).toBe(0)
  })
  it('shortens token counts', () => {
    expect(formatTokens(950)).toBe('950')
    expect(formatTokens(1234)).toBe('1.2 k')
    expect(formatTokens(45_678)).toBe('46 k')
    expect(formatTokens(1_234_567)).toBe('1.23 M')
    expect(formatTokens(12_345_678)).toBe('12.3 M')
  })
  it('shortens durations', () => {
    expect(formatDuration(850)).toBe('850 ms')
    expect(formatDuration(4200)).toBe('4.2 s')
    expect(formatDuration(65_000)).toBe('1 min 05 s')
  })
  it('averages without dividing by zero', () => {
    expect(average(9000, 3)).toBe(3000)
    expect(average(0, 0)).toBe(0)
  })
})
