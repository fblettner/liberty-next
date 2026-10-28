import { describe, expect, it } from 'vitest'
import { bindsToSearchParams } from './drill'

describe('bindsToSearchParams', () => {
  it('keeps the non-empty binds as query-string entries', () => {
    expect(bindsToSearchParams({ USR_ID: '42', EMPTY: '', ROLE: 'ADMIN' }).toString()).toBe('USR_ID=42&ROLE=ADMIN')
    expect(bindsToSearchParams({}).toString()).toBe('')
  })
})
