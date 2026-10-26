// The shared id validator behind every "Add / Rename / Clone" prompt in Settings, plus the
// clone-name suggester.
import { describe, expect, it } from 'vitest'
import { suggestCloneId, validateId } from './idValidator'

describe('validateId', () => {
  it('requires a value and enforces the kind pattern', () => {
    expect(validateId({ kind: 'screen', proposed: '  ', existing: [] })).toEqual({ error: 'required' })
    expect(validateId({ kind: 'screen', proposed: 'Users-List', existing: [] }).error).toBeTruthy()
    expect(validateId({ kind: 'screen', proposed: 'users_list', existing: [] })).toEqual({})
    expect(validateId({ kind: 'entries', proposed: 'usr_name', existing: [] }).error).toBeTruthy() // dictionary keys are UPPER
    expect(validateId({ kind: 'entries', proposed: 'USR_NAME', existing: [] })).toEqual({})
  })

  it('flags a duplicate in the same kind as an error, except the current name on rename', () => {
    expect(validateId({ kind: 'query', proposed: 'users_get', existing: ['users_get'] }).error).toBeTruthy()
    expect(validateId({ kind: 'query', proposed: 'users_get', existing: ['users_get'], mode: 'rename', currentName: 'users_get' })).toEqual({})
  })

  it('a numeric lookup id is accepted (v1 migration shape) but warned about', () => {
    const r = validateId({ kind: 'lookups', proposed: '12', existing: [] })
    expect(r.error).toBeUndefined()
    expect(r.warning).toBeTruthy()
  })
})

describe('suggestCloneId', () => {
  it('appends _copy and counts up past taken names', () => {
    expect(suggestCloneId('users', [])).toBe('users_copy')
    expect(suggestCloneId('users', ['users_copy'])).toBe('users_copy2')
    expect(suggestCloneId('users', ['users_copy', 'users_copy2'])).toBe('users_copy3')
  })
})
