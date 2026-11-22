import { describe, expect, it } from 'vitest'
import { cronToPlan, planToCron, startPlan, type SchedulePlan } from './schedulePlan'

describe('planToCron', () => {
  it('writes each plan as the scheduler takes it', () => {
    expect(planToCron({ kind: 'manual' })).toBeNull()
    expect(planToCron({ kind: 'minutes', every: 15 })).toBe('*/15 * * * *')
    expect(planToCron({ kind: 'minutes', every: 1 })).toBe('* * * * *')
    expect(planToCron({ kind: 'hours', every: 1, minute: 30 })).toBe('30 * * * *')
    expect(planToCron({ kind: 'hours', every: 4, minute: 0 })).toBe('0 */4 * * *')
    expect(planToCron({ kind: 'days', days: [], time: '02:00' })).toBe('0 2 * * *')
    expect(planToCron({ kind: 'days', days: [4, 1], time: '06:30' })).toBe('30 6 * * 1,4')
    expect(planToCron({ kind: 'month', day: 1, time: '02:00' })).toBe('0 2 1 * *')
    expect(planToCron({ kind: 'custom', cron: ' 0 8-18 * * 1-5 ' })).toBe('0 8-18 * * 1-5')
  })
  it('keeps what is typed inside what a clock allows', () => {
    expect(planToCron({ kind: 'minutes', every: 500 })).toBe('*/59 * * * *')
    expect(planToCron({ kind: 'month', day: 40, time: '25:99' })).toBe('59 23 31 * *')
    expect(planToCron({ kind: 'month', day: 3, time: 'soon' })).toBe('0 2 3 * *')
    expect(planToCron({ kind: 'days', days: [7, 0], time: '7:05' })).toBe('5 7 * * 0')      // 7 is Sunday too
    expect(planToCron({ kind: 'days', days: [0, 1, 2, 3, 4, 5, 6], time: '07:05' })).toBe('5 7 * * *')
  })
})

describe('cronToPlan', () => {
  const cases: [string, SchedulePlan][] = [
    ['', { kind: 'manual' }],
    ['*/15 * * * *', { kind: 'minutes', every: 15 }],
    ['30 * * * *', { kind: 'hours', every: 1, minute: 30 }],
    ['0 */4 * * *', { kind: 'hours', every: 4, minute: 0 }],
    ['0 2 * * *', { kind: 'days', days: [], time: '02:00' }],
    ['30 6 * * 1,4', { kind: 'days', days: [1, 4], time: '06:30' }],
    ['0 7 * * 1-5', { kind: 'days', days: [1, 2, 3, 4, 5], time: '07:00' }],
    ['0 2 1 * *', { kind: 'month', day: 1, time: '02:00' }],
  ]
  it('reads the shapes the plans write', () => {
    for (const [cron, plan] of cases) expect(cronToPlan(cron)).toEqual(plan)
  })
  it('gives back the same cron (the week of working days is written out)', () => {
    for (const [cron] of cases) expect(planToCron(cronToPlan(cron))).toBe(cron === '0 7 * * 1-5' ? '0 7 * * 1,2,3,4,5' : cron || null)
  })
  it('keeps anything else as it is typed', () => {
    for (const cron of ['0 8-18 * * 1-5', '0 2 * 1 *', '0 2 1 * 1', '@daily', '0 2,14 * * *']) {
      expect(cronToPlan(cron)).toEqual({ kind: 'custom', cron })
    }
  })
})

describe('startPlan', () => {
  it('gives each kind a usable start and keeps the time already set', () => {
    expect(startPlan('minutes', { kind: 'manual' })).toEqual({ kind: 'minutes', every: 15 })
    expect(startPlan('month', { kind: 'days', days: [1], time: '06:30' })).toEqual({ kind: 'month', day: 1, time: '06:30' })
    expect(startPlan('days', { kind: 'manual' })).toEqual({ kind: 'days', days: [], time: '02:00' })
    expect(startPlan('custom', { kind: 'days', days: [1], time: '06:30' })).toEqual({ kind: 'custom', cron: '30 6 * * 1' })
  })
})
