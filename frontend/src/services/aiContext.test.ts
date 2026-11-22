import { describe, expect, it } from 'vitest'
import {
  CONTEXT_MAX_CHARS, filterLines, getPageContext, pageFacts, renderPageContext, setPageContext, subscribePageContext, summarizePageContext,
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

  it('leaves the sensitive columns out when the installation hides them', () => {
    const page: PageContext = { ...base, filters: { ...base.filters, ULAN8: '1001', ULAN8_op: 'eq', ULUGRP_from: 'A' }, sensitive: ['ULAN8', 'note'] }
    const shown = renderPageContext(page)
    expect(shown).toContain('ULAN8 = 1001')
    expect(shown).toContain('"ULAN8":1001')
    const hidden = renderPageContext(page, true)
    expect(hidden).not.toContain('1001')
    expect(hidden).not.toContain('ULAN8')
    expect(hidden).not.toContain('NOTE')
    expect(hidden).toContain('columns: ULUSER, ULUGRP')
    expect(hidden).toContain('ULUSER startsWith JDE')
    expect(hidden).toContain('ULUGRP_from = A')
    expect(hidden).toContain('"ULUSER":"JDE"')
    expect(hidden).toContain('hidden: 2 column(s)')
    // nothing flagged: the same text either way
    expect(renderPageContext(base, true)).toBe(renderPageContext(base))
  })

  it('summarizes for the chip', () => {
    expect(summarizePageContext(base)).toEqual({ name: 'f0092', filters: 1, hasRow: true })
    expect(summarizePageContext({ ...base, screen: null, row: null, filters: {} })).toEqual({ name: 'f0092_get', filters: 0, hasRow: false })
  })

  it('keeps what a shipped page says of itself short: no blank fact, one line each, clipped, 20 at most', () => {
    expect(pageFacts({ object: 'P4210', kind: 'application', form: '', tab: null, custom: false, count: 0, title: 'Sales\r\n  Order Entry', filter: { status: 'To do' } }))
      .toEqual([['object', 'P4210'], ['kind', 'application'], ['count', '0'], ['title', 'Sales Order Entry'], ['filter', '{"status":"To do"}']])
    expect(pageFacts({ long: 'x'.repeat(500) })[0][1]).toHaveLength(240)
    expect(pageFacts(Object.fromEntries(Array.from({ length: 30 }, (_, i) => [`f${i}`, i + 1])))).toHaveLength(20)
    expect(pageFacts(undefined)).toEqual([])
  })

  it('renders a page an application ships: its title, its facts, and that it is no screen', () => {
    const page: PageContext = { path: '/app/nomajde/crossref', app: 'nomajde', kind: 'page', title: 'Cross Reference', connector: 'nomajde', query: 'crossref', screen: 'crossref',
      facts: [['object', 'P4210'], ['kind', 'application']] }
    const text = renderPageContext(page)
    expect(text.split('\n')).toEqual([
      'app: nomajde',
      "page: Cross Reference — a page of the application (crossref), not a screen: it has no query to read; use the application's own tools for its subject",
      'object: P4210', 'kind: application',
    ])
    expect(text).not.toContain('filters:')
    expect(summarizePageContext(page)).toEqual({ name: 'crossref', filters: 0, hasRow: false })
    const huge = renderPageContext({ ...page, facts: Array.from({ length: 20 }, (_, i) => [`fact ${i}`, 'y'.repeat(240)] as [string, string]) })
    expect(huge.length).toBeLessThanOrEqual(CONTEXT_MAX_CHARS)
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
