import { describe, expect, it } from 'vitest'
import { formatBytes, logQuery } from './serverLog'

describe('serverLog', () => {
  it('builds the search query, leaving blank filters out', () => {
    expect(logQuery({ level: 'WARNING', module: ' liberty.jobs ', q: '', hours: '24' })).toBe('level=WARNING&module=liberty.jobs&hours=24')
    expect(logQuery({ level: '', module: '', q: 'disk full', hours: '' }, { limit: '50' })).toBe('q=disk+full&hours=0&limit=50')
  })
  it('writes sizes', () => {
    expect(formatBytes(900)).toBe('900 B')
    expect(formatBytes(1536)).toBe('1.5 KB')
    expect(formatBytes(3_250_000)).toBe('3.1 MB')
  })
})
