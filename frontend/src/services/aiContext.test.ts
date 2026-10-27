import { describe, expect, it } from 'vitest'
import {
  CONTEXT_MAX_CHARS, filterLines, getPageContext, renderPageContext, setPageContext, subscribePageContext, summarizePageContext,
  type PageContext,
} from './aiContext'

const base: PageContext = {
  path: '/screen/nomajde/f0092', app: 'nomajde', kind: 'screen', title: 'Users', connector: 'jdedwards', query: 'f0092_get', screen: 'f0092',
  filters: { ULUSER: 'JDE', ULUSER_op: 'startsWith', ULAN8: '', ULAN8_op: 'eq' },
  rowCount: 120, truncated: true, columns: ['ULUSER', 'ULAN8', 'ULUGRP'],
  row: { ULUSER: 'JDE', ULAN8: 1001, NOTE: 'x'.repeat(300) },
}

describe('aiContext', () => {
  it('folds the _op companions into the filter lines and skips blanks', () => {
    expect(filterLines(base.filters)).toEqual(['ULUSER startsWith JDE'])
    expect(filterLines({ CO: '00001', CO_op: 'eq' })).toEqual(['CO = 00001'])
    expect(filterLines(undefined)).toEqual([])
  })

  it('renders one line per fact, clipping long cells', () => {
    const text = renderPageContext(base)
    expect(text.split('\n')).toEqual([
      'app: nomajde',
      'screen: f0092 — Users (connector jdedwards, read query f0092_get)',
      'filters: ULUSER startsWith JDE',
      'rows loaded: 120 (cut by the row cap — more exist)',
      'columns: ULUSER, ULAN8, ULUGRP',
      expect.stringMatching(/^current row \(the one the user clicked\): \{"ULUSER":"JDE","ULAN8":1001,"NOTE":"x{119}…"\}$/),
    ])
    const sql = renderPageContext({ path: '/sql/a/b', kind: 'sql', title: 'Ping', connector: 'liberty', query: 'ping' })
    expect(sql).toBe('app: liberty\nquery: liberty.ping — Ping\nfilters: none')
  })

  it('never exceeds the cap', () => {
    const wide = { ...base, columns: Array.from({ length: 500 }, (_, i) => `COLUMN_NUMBER_${i}`), row: Object.fromEntries(Array.from({ length: 300 }, (_, i) => [`C${i}`, 'v'.repeat(100)])) }
    const text = renderPageContext(wide)
    expect(text.length).toBe(CONTEXT_MAX_CHARS)
    expect(text.endsWith('…')).toBe(true)
    expect(text).toContain('… (500 in all)')
  })

  it('summarizes for the chip', () => {
    expect(summarizePageContext(base)).toEqual({ name: 'f0092', filters: 1, hasRow: true })
    expect(summarizePageContext({ ...base, screen: null, row: null, filters: {} })).toEqual({ name: 'f0092_get', filters: 0, hasRow: false })
  })

  it('is a store: set notifies subscribers, unsubscribe stops them', () => {
    const seen: (PageContext | null)[] = []
    const off = subscribePageContext((c) => seen.push(c))
    setPageContext(base)
    expect(getPageContext()).toBe(base)
    off()
    setPageContext(null)
    expect(seen).toEqual([base])
    expect(getPageContext()).toBeNull()
  })
})
