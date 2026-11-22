import { describe, expect, it } from 'vitest'
import { apiFigures, connectorFigures, queryFigures, tableSlots } from './connectorFigures'

describe('what a connector says', () => {
  it('counts what it holds', () => {
    expect(connectorFigures({ type: 'sql', pool: 'main', tables: [{}, {}], queries: [{}], sequences: [], lookups: [{}, {}, {}] })).toEqual({
      api: false, pool: 'main', tables: 2, queries: 1, sequences: 0, lookups: 3, endpoints: 0 })
    expect(connectorFigures({ type: 'api', endpoints: [{}, {}] })).toMatchObject({ api: true, endpoints: 2, tables: 0 })
    expect(connectorFigures(undefined)).toMatchObject({ api: false, pool: '', tables: 0 })
  })
  it("says which of a table's statements are set, writable, or left blank", () => {
    const slots = tableSlots({ name: 't', get: { sql: 'SELECT 1' }, put: { sql: 'UPDATE t SET a = 1', writable: true }, post: { sql: '  ' }, delete: { sql: { oracle: 'DELETE FROM t' } } })
    expect(slots).toEqual([
      { crud: 'get', set: true, writable: false, blank: false },
      { crud: 'put', set: true, writable: true, blank: false },
      { crud: 'post', set: true, writable: false, blank: true },
      { crud: 'delete', set: true, writable: false, blank: false },
    ])
    expect(tableSlots({ name: 't' }).every((s) => !s.set && !s.blank)).toBe(true)
  })
})

describe('what a query says', () => {
  it('reads the statement: its verb, its databases, its placeholders', () => {
    const f = queryFigures({ name: 'q', sql: { default: "-- :NOT_ME\nSELECT * FROM t WHERE a = :CO AND b = ':skip' AND c::text = :MCU AND d = :CO", oracle: 'SELECT * FROM t WHERE x = :ORA' }, params: [{ name: 'CO' }] })
    expect(f.verb).toBe('SELECT')
    expect(f.dialects).toEqual(['default', 'oracle'])
    expect(f.placeholders).toEqual(['CO', 'MCU', 'ORA'])
    expect(f.declared).toBe(1)
    expect(f.writes).toBe(false)
    expect(f.blank).toBe(false)
  })
  it('tells a statement that writes, and whether it is allowed to', () => {
    expect(queryFigures({ sql: 'update t set a = :A' })).toMatchObject({ verb: 'UPDATE', writes: true, writable: false })
    expect(queryFigures({ sql: 'DELETE FROM t', writable: true })).toMatchObject({ writes: true, writable: true })
    expect(queryFigures({ sql: 'WITH x AS (SELECT 1) SELECT * FROM x' }).writes).toBe(false)
  })
  it('tells an empty statement', () => {
    expect(queryFigures({ sql: '  ' })).toMatchObject({ blank: true, verb: '', writes: false })
    expect(queryFigures(undefined)).toMatchObject({ blank: true, dialects: [], placeholders: [] })
  })
})

describe('what an API connector says', () => {
  it('reads the host, the endpoints and the environments', () => {
    const f = apiFigures({ type: 'api', base_url: 'https://ais.example.com:8080/jderest/v2', auth_type: 'basic', auth_username: 'u', pool: 'prod', targets: { test: {}, dev: {} },
      endpoints: [{ name: 'a', group: 'g1', writable: true }, { name: 'b', group: 'g1' }, { name: 'c' }] })
    expect(f).toMatchObject({ host: 'ais.example.com:8080', auth: 'basic', authMissing: '', pool: 'prod', targets: 2, endpoints: 3, writable: 1, groups: 1 })
  })
  it('tells what the chosen authentication lacks', () => {
    expect(apiFigures({ auth_type: 'basic' }).authMissing).toBe('username')
    expect(apiFigures({ auth_type: 'oauth2' }).authMissing).toBe('tokenEndpoint')
    expect(apiFigures({ auth_type: 'bearer' }).authMissing).toBe('token')
    expect(apiFigures({ auth_type: 'bearer', auth_token_endpoint: '/token' }).authMissing).toBe('')
    expect(apiFigures({ auth_type: 'user_jwt' }).authMissing).toBe('key')
    expect(apiFigures({ auth_type: 'user_jwt', auth_jwt_key: '/etc/liberty/jde.key' })).toMatchObject({ auth: 'user_jwt', authMissing: '' })
    expect(apiFigures({}).auth).toBe('none')
    expect(apiFigures({}).host).toBe('')
  })
})
