import { describe, expect, it, vi } from 'vitest'

const tables = vi.fn()
vi.mock('./poolSchema', () => ({ getPoolSchemaTables: (...a: unknown[]) => tables(...a) }))

import { fileSourceOf, fileSourcePatch, fileSourceSummary, tableColumns } from './fileLoad'

describe('fileSourceOf', () => {
  it('reads the source the server would take', () => {
    expect(fileSourceOf({})).toBe('upload')
    expect(fileSourceOf({ path: '  ', watch_dir: '' })).toBe('upload')
    expect(fileSourceOf({ path: '/in/a.xlsx' })).toBe('path')
    expect(fileSourceOf({ watch_dir: '/in' })).toBe('folder')
    expect(fileSourceOf({ path: '/in/a.xlsx', watch_dir: '/in' })).toBe('path')
  })
})

describe('fileSourcePatch', () => {
  it('empties what belongs to another source', () => {
    expect(fileSourcePatch('upload', { path: '/in/a.xlsx', watch_dir: '/in', watch_pattern: '*.csv' }))
      .toEqual({ path: undefined, watch_dir: undefined, watch_pattern: undefined })
    expect(fileSourcePatch('folder', { path: '/in/a.xlsx', watch_dir: '/in', watch_pattern: '*.csv' }))
      .toEqual({ path: undefined, watch_dir: '/in', watch_pattern: '*.csv' })
    expect(fileSourcePatch('folder', {})).toEqual({ path: undefined, watch_dir: '', watch_pattern: undefined })
    expect(fileSourcePatch('path', { watch_dir: '/in' })).toEqual({ path: '', watch_dir: undefined, watch_pattern: undefined })
  })
})

describe('fileSourceSummary', () => {
  it('names the file, the folder, or nothing', () => {
    expect(fileSourceSummary({ path: '/data/in/sales-${params.period}.xlsx' })).toBe('sales-${params.period}.xlsx')
    expect(fileSourceSummary({ watch_dir: '/data/in/', watch_pattern: '*.csv' })).toBe('/data/in/*.csv')
    expect(fileSourceSummary({})).toBe('')
  })
})

describe('tableColumns', () => {
  it('answers the columns of the table asked, whatever its case', async () => {
    tables.mockResolvedValueOnce({ tables: [
      { name: 'FACT_SALES_OLD', columns: [{ name: 'x' }] },
      { name: 'FACT_SALES', columns: [{ name: 'period' }, { name: 'revenue' }] },
    ] })
    expect(await tableColumns('demo', '', ' fact_sales ')).toEqual(['period', 'revenue'])
    expect(tables).toHaveBeenLastCalledWith('demo', null, 'fact_sales')
  })
  it('answers nothing when the table cannot be read or is not named', async () => {
    tables.mockRejectedValueOnce(new Error('down'))
    expect(await tableColumns('demo', 'public', 'nope')).toEqual([])
    expect(await tableColumns('', '', 'fact')).toEqual([])
    expect(await tableColumns('demo', '', ' ')).toEqual([])
  })
})
