import { afterEach, describe, expect, it, vi } from 'vitest'
import { anyDirty, clearDirty, dirtyTabs, isTabDirty, markDirty, watchDirty } from './dirtyGuard'

describe('unsaved changes known to the shell', () => {
  afterEach(() => { for (const t of ['a', 'b', null]) for (const s of ['x', 'y']) clearDirty(t, s) })

  it('a tab is dirty while one of its sources is', () => {
    expect(anyDirty()).toBe(false)
    markDirty('a', 'x'); markDirty('a', 'y'); markDirty('b', 'x')
    expect(isTabDirty('a')).toBe(true)
    expect(dirtyTabs().sort()).toEqual(['a', 'b'])
    clearDirty('a', 'x')
    expect(isTabDirty('a')).toBe(true)                 // the editor window on top still holds edits
    clearDirty('a', 'y')
    expect(isTabDirty('a')).toBe(false)
    expect(dirtyTabs(['a', 'b'])).toEqual(['b'])
    expect(dirtyTabs(['a'])).toEqual([])
    clearDirty('a', 'never-marked'); clearDirty('zz', 'x')   // nothing to clear: no error
    expect(anyDirty()).toBe(true)
  })

  it('says when the set of unsaved tabs changes, and only then', () => {
    let told = 0
    const stop = watchDirty(() => { told += 1 })
    markDirty('a', 'x')
    expect(told).toBe(1)
    markDirty('a', 'y')                                // a second source in the same tab: the set is the same
    expect(told).toBe(1)
    clearDirty('a', 'x')
    expect(told).toBe(1)
    clearDirty('a', 'y')
    expect(told).toBe(2)
    stop()
    markDirty('b', 'x')
    expect(told).toBe(2)
  })

  it('a page outside a tab counts for the browser, not as a tab', () => {
    markDirty(null, 'x')
    expect(anyDirty()).toBe(true)
    expect(dirtyTabs()).toEqual([])
  })

  it('the browser is asked to confirm a reload only while something is unsaved', () => {
    const add = vi.spyOn(window, 'addEventListener'), remove = vi.spyOn(window, 'removeEventListener')
    markDirty('a', 'x')
    expect(add.mock.calls.some(([type]) => type === 'beforeunload')).toBe(true)
    const handler = add.mock.calls.find(([type]) => type === 'beforeunload')![1] as (e: BeforeUnloadEvent) => void
    const event = { preventDefault: vi.fn(), returnValue: 'unset' } as unknown as BeforeUnloadEvent
    handler(event)
    expect(event.preventDefault).toHaveBeenCalled()
    expect(event.returnValue).toBe('')
    clearDirty('a', 'x')
    expect(remove.mock.calls.some(([type]) => type === 'beforeunload')).toBe(true)
    add.mockRestore(); remove.mockRestore()
  })
})
