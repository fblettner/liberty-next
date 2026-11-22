// The shared id validator behind every "Add / Rename / Clone" prompt in Settings, plus the
// clone-name suggester.
import { describe, expect, it } from 'vitest'
import { freeId, idFromLabel, newObjectFromLabel, suggestCloneId, validateId } from './idValidator'

describe('an id made from a label', () => {
  it('keeps letters and digits, drops accents, joins the rest with _', () => {
    expect(idFromLabel('Customer orders (late)')).toBe('customer_orders_late')
    expect(idFromLabel('Écritures à valider')).toBe('ecritures_a_valider')
    expect(idFromLabel('  2026 budget ')).toBe('_2026_budget')
    expect(idFromLabel('???')).toBe('')
  })
  it('is made free among the existing ones', () => {
    expect(freeId('orders', [])).toBe('orders')
    expect(freeId('orders', ['orders', 'orders_2'])).toBe('orders_3')
  })
  it('gives the label back with its id', () => {
    expect(newObjectFromLabel(' Customer orders ', ['customer_orders'])).toEqual({ id: 'customer_orders_2', label: 'Customer orders' })
    expect(newObjectFromLabel('!!', [])).toBeNull()
  })
  it('says what to type when a typed id is refused', () => {
    expect(validateId({ kind: 'screen', proposed: 'Users List', existing: [] }).error).toMatch(/^Use small letters, digits and _ only/)
    expect(validateId({ kind: 'entries', proposed: 'usr name', existing: [] }).error).toMatch(/^Use capital letters, digits and _ only/)
  })
})

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
