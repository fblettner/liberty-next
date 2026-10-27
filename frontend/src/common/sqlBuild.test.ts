import { describe, expect, it } from 'vitest'
import { buildCrudSql } from './sqlBuild'

describe('buildCrudSql', () => {
  const base = { schema: '#SCHEMA.DTA#', table: 'F0101', selectCols: ['abalph', 'abupmj', 'abuser'], keyCols: ['aban8'] }
  it('binds every column by default', () => {
    expect(buildCrudSql({ crud: 'put', ...base })).toBe('UPDATE #SCHEMA.DTA#.F0101\nSET\n  ABALPH = :ABALPH,\n  ABUPMJ = :ABUPMJ,\n  ABUSER = :ABUSER\nWHERE\n  ABAN8 = :ABAN8_ORIGINAL')
  })
  it('writes the audit tokens in place of the bind on insert and update', () => {
    const tokens = { abupmj: '{{JDEDATE}}', ABUSER: '{{LOGIN}}' }
    expect(buildCrudSql({ crud: 'put', ...base, tokens })).toContain('ABUPMJ = {{JDEDATE}},\n  ABUSER = {{LOGIN}}')
    const post = buildCrudSql({ crud: 'post', ...base, selectCols: [...base.selectCols, 'aban8'], tokens })
    expect(post).toContain('{{JDEDATE}},\n  {{LOGIN}},\n  :ABAN8')
    expect(buildCrudSql({ crud: 'get', ...base, tokens })).toBe('SELECT\n  ABALPH,\n  ABUPMJ,\n  ABUSER\nFROM #SCHEMA.DTA#.F0101')
  })
})
