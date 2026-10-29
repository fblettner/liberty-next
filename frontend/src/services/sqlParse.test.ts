// The SQL Wizard's pure logic (services/sqlParse.ts): the SELECT it generates and what it reads back
// from an existing statement — pinned on the behaviour the wizard had when the logic moved out of
// common/SqlWizardModal.tsx.
import { describe, expect, it } from 'vitest'
import { firstSqlKeyword, generateSql, parseExistingSelect, parseMultiSelect, parseOn, parseTableChain, suggestAlias, type JoinTable } from './sqlParse'

const cols = (...n: string[]) => n.map((name) => ({ name }))
const f0101: JoinTable = { schema: 'DTA', name: 'F0101', alias: 'F0101', columns: cols('ABAN8', 'ABALPH', 'ABAT1') }
const f0111: JoinTable = { schema: 'DTA', name: 'F0111', alias: 'F0111', columns: cols('WWAN8', 'WWMLNM'), join: { type: 'LEFT', conds: [{ left: 'F0101.ABAN8', right: 'F0111.WWAN8' }] } }

describe('generateSql', () => {
  it('writes a single table bare, a join alias-qualified', () => {
    expect(generateSql([f0101], new Set(['F0101.ABAN8', 'F0101.ABALPH']), [{ col: 'F0101.ABAT1', op: '=', value: 'C' }], [{ col: 'F0101.ABAN8', dir: 'DESC' }]))
      .toBe("SELECT\n    ABAN8,\n    ABALPH\nFROM\n    DTA.F0101\nWHERE\n    ABAT1 = 'C'\nORDER BY\n    ABAN8 DESC")
    expect(generateSql([f0101, f0111], new Set(['F0101.ABAN8', 'F0111.WWMLNM']), [{ col: 'F0101.ABAT1', op: 'IS NULL', value: '' }], []))
      .toBe('SELECT\n    F0101.ABAN8,\n    F0111.WWMLNM\nFROM\n    DTA.F0101 F0101\nLEFT JOIN DTA.F0111 F0111 ON F0101.ABAN8 = F0111.WWAN8\nWHERE\n    F0101.ABAT1 IS NULL')
    expect(generateSql([f0101], new Set(), [], [])).toBe('')                    // nothing ticked
  })
})

describe('reading a statement back', () => {
  it('sniffs the statement type and suggests aliases', () => {
    expect([firstSqlKeyword(' select 1'), firstSqlKeyword('WITH x AS (select 1) select 1'), firstSqlKeyword('')]).toEqual(['SELECT', 'OTHER', null])
    expect([suggestAlias('f0101', new Set()), suggestAlias('F0101', new Set(['F0101']))]).toEqual(['F0101', 'F01012'])
  })

  it('reads the FROM chain and its ON conditions', () => {
    expect(parseTableChain("SELECT A.X FROM DTA.F0101 A LEFT JOIN DTA.F0111 B ON A.ABAN8 = B.WWAN8 WHERE A.ABAT1 = 'C'")).toEqual([
      { schema: 'DTA', name: 'F0101', alias: 'A' },
      { schema: 'DTA', name: 'F0111', alias: 'B', joinType: 'LEFT', onText: 'A.ABAN8 = B.WWAN8' },
    ])
    expect(parseOn('F0101.ABAN8 = F0111.WWAN8', 'F0111', [f0101, f0111])).toEqual({ conds: [{ left: 'F0101.ABAN8', right: 'F0111.WWAN8' }] })
  })

  it('reads a single-table SELECT into columns, WHERE and ORDER BY', () => {
    const r = parseExistingSelect("SELECT ABAN8, ABALPH FROM F0101 WHERE ABAT1 = 'C' ORDER BY ABAN8 DESC", { name: 'F0101', kind: 'table', columns: cols('ABAN8', 'ABALPH', 'ABAT1') })
    expect(r && { cols: [...r.cols], wheres: r.wheres, orders: r.orders }).toEqual({
      cols: ['ABAN8', 'ABALPH'], wheres: [{ col: 'ABAT1', op: '=', value: 'C' }], orders: [{ col: 'ABAN8', dir: 'DESC' }],
    })
    expect(parseExistingSelect('', undefined)).toBeNull()
  })

  it('reads a joined SELECT, and leaves an aggregate as it is', () => {
    const r = parseMultiSelect("SELECT F0101.ABAN8, F0111.WWMLNM FROM F0101 LEFT JOIN F0111 ON F0101.ABAN8 = F0111.WWAN8 WHERE F0101.ABAT1 = 'C'", [f0101, f0111])
    expect(r && { cols: [...r.cols], wheres: r.wheres, orders: r.orders }).toEqual({
      cols: ['F0101.ABAN8', 'F0111.WWMLNM'], wheres: [{ col: 'F0101.ABAT1', op: '=', value: 'C' }], orders: [],
    })
    expect(parseMultiSelect('SELECT COUNT(*) FROM F0101 GROUP BY ABAT1', [f0101])).toBeNull()
  })
})
