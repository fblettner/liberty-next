import { describe, expect, it } from 'vitest'
import { joinDateTime, normaliseTime, splitDateTime } from './dateTime'

describe('dateTime', () => {
  it('splits a date-time into a day and a time', () => {
    expect(splitDateTime('2026-10-28 14:05:09')).toEqual({ date: '2026-10-28', time: '14:05:09' })
    expect(splitDateTime('2026-10-28T14:05:09.123')).toEqual({ date: '2026-10-28', time: '14:05:09' })
    expect(splitDateTime('2026-10-28T14:05')).toEqual({ date: '2026-10-28', time: '14:05:00' })
    expect(splitDateTime('2026-10-28')).toEqual({ date: '2026-10-28', time: '' })
    expect(splitDateTime(null)).toEqual({ date: '', time: '' })
    expect(splitDateTime('soon')).toEqual({ date: '', time: '' })
  })
  it('a zoned value in local time', () => {
    const d = new Date('2026-10-28T23:54:08Z'), p = (n: number) => String(n).padStart(2, '0')
    expect(splitDateTime('2026-10-28 23:54:08.859444+00:00')).toEqual({
      date: `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`,
      time: `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`,
    })
  })
  it('normalises a typed time, refuses an impossible one', () => {
    expect(normaliseTime('9:05')).toBe('09:05:00')
    expect(normaliseTime('23:59:59')).toBe('23:59:59')
    expect(normaliseTime('24:00')).toBeNull()
    expect(normaliseTime('12:60')).toBeNull()
    expect(normaliseTime('noon')).toBeNull()
  })
  it('joins back, midnight when no time', () => {
    expect(joinDateTime('2026-10-28', '14:05')).toBe('2026-10-28 14:05:00')
    expect(joinDateTime('2026-10-28', '')).toBe('2026-10-28 00:00:00')
    expect(joinDateTime('', '14:05')).toBe('')
  })
})
