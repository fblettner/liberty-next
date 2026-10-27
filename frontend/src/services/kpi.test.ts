import { describe, expect, it } from 'vitest'
import { formatKpi, kpiDelta } from './kpi'

describe('kpi', () => {
  it('formats by kind', () => {
    expect(formatKpi(1234, undefined, 'en')).toBe('1,234')
    expect(formatKpi(0.1234, 'percent', 'en')).toBe('12.3%')
    expect(formatKpi(12.5, 'currency', 'en')).toMatch(/12\.50/)
    expect(formatKpi(1.23456, undefined, 'en')).toBe('1.235')
  })
  it('compares with the previous period', () => {
    expect(kpiDelta(125, 100, undefined, 'en')).toEqual({ direction: 'up', text: '+25%' })
    expect(kpiDelta(75, 100, undefined, 'en')).toEqual({ direction: 'down', text: '−25%' })
    expect(kpiDelta(100, 100, undefined, 'en')).toEqual({ direction: 'flat', text: '0%' })
    expect(kpiDelta(0.5, 0.4, 'percent', 'en')).toEqual({ direction: 'up', text: '+10 pts' })
    expect(kpiDelta(40, 0, undefined, 'en')).toEqual({ direction: 'up', text: '+40' })        // no previous to divide by
    expect(kpiDelta(null, 100)).toBeNull()
    expect(kpiDelta(100, null)).toBeNull()
  })
})
