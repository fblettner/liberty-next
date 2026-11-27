import { describe, expect, it } from 'vitest'
import { mainPoolOf, poolChoices } from './pools'

describe('the pool picker', () => {
  const jde = { type: 'sql', pool: 'jde_bf_prd', pools: ['jde_bf_prd', 'jde_uf_prd'] }
  it('names the main database of the connectors that offer several', () => {
    expect(mainPoolOf([jde, { type: 'sql', pool: 'nomajde', pools: ['nomajde'] }, { type: 'api' }])).toBe('jde_bf_prd')
    expect(mainPoolOf([{ type: 'sql', pool: 'nomajde', pools: ['nomajde'] }])).toBeNull()
    expect(mainPoolOf([jde, { type: 'sql', pool: 'other', pools: ['other', 'jde_uf_prd'] }])).toBeNull()
  })
  it('offers the main database under its name, once, then the others by name', () => {
    expect(poolChoices(['jde_uf_prd', 'jde_bf_prd', 'jde_dev'], 'jde_bf_prd', 'Default')).toEqual([
      { value: '', label: 'jde_bf_prd' }, { value: 'jde_dev', label: 'jde_dev' }, { value: 'jde_uf_prd', label: 'jde_uf_prd' }])
    expect(poolChoices(['b', 'a'], null, 'Default')).toEqual([{ value: '', label: 'Default' }, { value: 'a', label: 'a' }, { value: 'b', label: 'b' }])
  })
})
