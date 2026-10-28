import { describe, expect, it } from 'vitest'
import { describeCron, weekdayName } from './cronText'

describe('describeCron', () => {
  it('reads the common shapes', () => {
    expect(describeCron('*/15 * * * *')).toEqual({ key: 'cron.everyMinutes', values: { n: 15 } })
    expect(describeCron('5 * * * *')).toEqual({ key: 'cron.hourly', values: { m: '05' } })
    expect(describeCron('0 3 * * *')).toEqual({ key: 'cron.daily', values: { time: '03:00' } })
    expect(describeCron('30 2 * * 1-5')).toEqual({ key: 'cron.weekdays', values: { time: '02:30' } })
    expect(describeCron('0 6 * * 1')).toEqual({ key: 'cron.weekly', values: { day: 'Monday', time: '06:00' } })
    expect(describeCron('0 6 1 * *')).toEqual({ key: 'cron.monthly', values: { day: 1, time: '06:00' } })
  })
  it('gives up on the rest', () => {
    expect(describeCron('0 6,18 * * *')).toBeNull()
    expect(describeCron('0 6 1 1 *')).toBeNull()
    expect(describeCron('bad')).toBeNull()
    expect(describeCron(null)).toBeNull()
  })
  it('names weekdays, Sunday as 0 or 7', () => {
    expect(weekdayName(0, 'en')).toBe('Sunday')
    expect(weekdayName(7, 'en')).toBe('Sunday')
    expect(weekdayName(3, 'fr')).toBe('mercredi')
  })
})
