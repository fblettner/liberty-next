// Renaming a dict key in place (the Settings inspectors' `id` field) while keeping key order,
// and the rename validation that gates it.
import { describe, expect, it } from 'vitest'
import { renameKey, validateRename } from './keyRename'

describe('renameKey', () => {
  it('renames in place and keeps the key order', () => {
    const out = renameKey({ a: 1, b: 2, c: 3 }, 'b', 'bee')
    expect(Object.keys(out)).toEqual(['a', 'bee', 'c'])
    expect(out.bee).toBe(2)
  })
  it('returns the same object when nothing changes or the key is unknown', () => {
    const obj = { a: 1 }
    expect(renameKey(obj, 'a', 'a')).toBe(obj)
    expect(renameKey(obj, 'zzz', 'y')).toBe(obj)
  })
})

describe('validateRename', () => {
  it('empty → empty; same → unchanged; another existing key → exists; else ok', () => {
    const keys = ['users', 'roles']
    expect(validateRename('users', '  ', keys)).toBe('empty')
    expect(validateRename('users', 'users', keys)).toBe('unchanged')
    expect(validateRename('users', 'roles', keys)).toBe('exists')
    expect(validateRename('users', 'people', keys)).toBeNull()
  })
})
