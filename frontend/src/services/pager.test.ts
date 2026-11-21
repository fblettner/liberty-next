import { describe, expect, it } from 'vitest'
import { pageWindow } from './pager'

describe('pageWindow', () => {
  it('says which rows a page holds', () => {
    expect(pageWindow(1, 50, 992)).toEqual({ page: 1, pages: 20, from: 1, to: 50, total: 992 })
    expect(pageWindow(20, 50, 992)).toEqual({ page: 20, pages: 20, from: 951, to: 992, total: 992 })
  })
  it('keeps the page inside what exists', () => {
    expect(pageWindow(99, 50, 120).page).toBe(3)
    expect(pageWindow(0, 50, 120).page).toBe(1)
  })
  it('shows an empty result as one page, 0 to 0', () => {
    expect(pageWindow(1, 50, 0)).toEqual({ page: 1, pages: 1, from: 0, to: 0, total: 0 })
  })
  it('counts a full last page once', () => {
    expect(pageWindow(2, 50, 100)).toEqual({ page: 2, pages: 2, from: 51, to: 100, total: 100 })
  })
})
