import { describe, expect, it } from 'vitest'
import { average, cachedShare, formatCost, formatDuration, formatTokens, groupActivity, tokensIn, type Turn } from './aiUsage'

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
  it('shows an estimated cost, or a dash when there is none', () => {
    expect(formatCost(0)).toBe('$0')
    expect(formatCost(0.0042)).toBe('$0.004')
    expect(formatCost(1.254)).toBe('$1.25')
    expect(formatCost(312.4)).toBe('$312')
    expect(formatCost(null)).toBe('—')
    expect(formatCost(undefined)).toBe('—')
  })
  it('shows a run of MCP tool calls as one line', () => {
    const turn = (id: string, channel: string, username: string, at: string): Turn => ({
      id, at, username, channel, app: null, model: channel === 'chat' ? 'claude-opus-5-5' : channel,
      input_tokens: 0, output_tokens: 0, cache_read_tokens: 0, cache_creation_tokens: 0,
      tool_calls: 1, tool_errors: 0, duration_ms: 5, stop_reason: 'end_turn', error: null, tools: [],
    })
    // newest first, as the server gives them
    const out = groupActivity([
      turn('a', 'mcp', 'bob', '2026-11-19T10:05:30Z'),
      turn('b', 'mcp', 'bob', '2026-11-19T10:05:00Z'),
      turn('c', 'mcp', 'bob', '2026-11-19T10:01:00Z'),       // more than two minutes before: another run
      turn('d', 'chat', 'bob', '2026-11-19T10:00:30Z'),
      turn('e', 'mcp', 'bob', '2026-11-19T10:00:00Z'),       // cut by the chat turn between
      turn('f', 'mcp', 'ann', '2026-11-19T09:59:50Z'),       // another user
      turn('g', 'apply', 'ann', '2026-11-19T09:59:00Z'),
    ])
    expect(out.map((x) => (x.kind === 'mcp' ? x.turns.map((t) => t.id).join('+') : x.turn.id))).toEqual(['a+b', 'c', 'd', 'e', 'f', 'g'])
    expect(groupActivity([])).toEqual([])
  })
})
