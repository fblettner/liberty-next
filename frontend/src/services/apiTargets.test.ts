import { describe, expect, it } from 'vitest'
import { cleanTarget, environmentPools, missingTargets } from './apiTargets'

const SQL = [
  { pool: 'jde_prod', pools: ['jde_dev', 'jde_cv'] },
  { pool: 'other' },
  { pools: ['x'] },                     // pool defaults to `default`
]

describe('apiTargets', () => {
  it('the environments are the pool and every pool of a SQL connector on it', () => {
    expect(environmentPools('jde_prod', SQL)).toEqual(['jde_cv', 'jde_dev', 'jde_prod'])
    expect(environmentPools('default', SQL)).toEqual(['default', 'x'])
    expect(environmentPools('alone', SQL)).toEqual(['alone'])
    expect(environmentPools(null, SQL)).toEqual([])
  })
  it('lists the environments without a server, once targets exist', () => {
    expect(missingTargets('jde_prod', { jde_dev: {} }, SQL)).toEqual(['jde_cv'])
    expect(missingTargets('jde_prod', { jde_dev: {}, jde_cv: {} }, SQL)).toEqual([])
    expect(missingTargets('jde_prod', {}, SQL)).toEqual([])          // no targets: one server, as before
    expect(missingTargets(null, { jde_dev: {} }, SQL)).toEqual([])
  })
  it('drops blank fields', () => {
    expect(cleanTarget({ base_url: 'https://x', auth_username: '', auth_password: undefined, verify_ssl: false }))
      .toEqual({ base_url: 'https://x', verify_ssl: false })
  })
})
