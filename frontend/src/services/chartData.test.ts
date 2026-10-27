import { describe, expect, it } from 'vitest'
import { buildChartData, seriesKeys } from './chartData'
import type { QueryResult } from '../types/connectors'
import type { ChartSpec } from '../types/charts'

const result = {
  columns: [{ name: 'refresh' }, { name: 'process' }, { name: 'lines' }],
  rows: [
    { refresh: '2026-05', process: 'P2P', lines: 10 }, { refresh: '2026-05', process: 'O2C', lines: 4 }, { refresh: '2026-05', process: 'P2P', lines: 2 },
    { refresh: '2026-04', process: 'O2C', lines: 7 },
  ],
} as unknown as QueryResult

describe('chartData', () => {
  it('pivots a series column into one series per value', () => {
    const spec: ChartSpec = { type: 'bar', x: 'REFRESH', series: 'PROCESS', y: ['LINES'], aggregation: 'sum', sortByX: true }
    const data = buildChartData(result, spec)
    expect(data).toEqual([{ x: '2026-04', P2P: null, O2C: 7 }, { x: '2026-05', P2P: 12, O2C: 4 }])
    expect(seriesKeys(spec, data)).toEqual(['P2P', 'O2C'])
    expect(seriesKeys({ ...spec, series: undefined }, data)).toEqual(['LINES'])
  })
  it('without a series column, one series per y column', () => {
    const data = buildChartData(result, { type: 'bar', x: 'refresh', y: ['lines'], aggregation: 'sum' })
    expect(data).toEqual([{ x: '2026-05', lines: 16 }, { x: '2026-04', lines: 7 }])
  })
})
