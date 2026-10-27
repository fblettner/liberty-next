import { describe, expect, it, vi } from 'vitest'
import { cached, clearWidgetCache, lastFetchedAt } from './widgetCache'

describe('widgetCache', () => {
  it('reuses a result within the TTL, refetches after it or on force, drops a failure', async () => {
    clearWidgetCache()
    vi.useFakeTimers({ now: 1_000_000 })
    let calls = 0
    const load = () => { calls += 1; return Promise.resolve(calls) }
    expect(await cached('k', 60, load)).toBe(1)
    expect(await cached('k', 60, load)).toBe(1)                 // within the TTL
    expect(lastFetchedAt('k')).toBe(1_000_000)
    expect(await cached('k', 60, load, true)).toBe(2)           // force
    vi.setSystemTime(1_000_000 + 61_000)
    expect(await cached('k', 60, load)).toBe(3)                 // expired
    expect(await cached('k', 0, load)).toBe(4)                  // TTL 0: always fresh
    await expect(cached('bad', 60, () => Promise.reject(new Error('x')))).rejects.toThrow('x')
    expect(lastFetchedAt('bad')).toBeNull()                      // dropped, so the next call retries
    vi.useRealTimers()
  })
})
