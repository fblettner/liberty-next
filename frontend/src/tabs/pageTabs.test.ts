import { describe, expect, it } from 'vitest'
import { explorerModelOf, isPageTabPath, appPageOf, callableRef, jobEditorOf } from './pageTabs'

describe('pageTabs', () => {
  it('recognises the fixed page paths and an Explorer model link', () => {
    expect(isPageTabPath('/nomaflow/runs')).toBe(true)
    expect(isPageTabPath('/nomaflow/runs/abc')).toBe(false)
    expect(isPageTabPath('/explorer')).toBe(true)
    expect(isPageTabPath('/explorer/nomasx1/sod')).toBe(true)
    expect(isPageTabPath('/explorer/nomasx1')).toBe(false)
    expect(isPageTabPath('/sql/x/y')).toBe(false)
    expect(explorerModelOf('/explorer/nomasx1/sod')).toEqual({ app: 'nomasx1', model: 'sod' })
    expect(explorerModelOf('/explorer/my%20app/m')).toEqual({ app: 'my app', model: 'm' })
    expect(explorerModelOf('/explorer')).toBeNull()
  })
})

describe("a page an application ships", () => {
  it('is a page tab at /app/<app>/<page>', () => {
    expect(appPageOf('/app/nomajde/crossref')).toEqual({ app: 'nomajde', page: 'crossref' })
    expect(isPageTabPath('/app/nomajde/crossref')).toBe(true)
    expect(appPageOf('/app/nomajde')).toBeNull()
    expect(appPageOf('/app/Bad-Name/x')).toBeNull()
    expect(appPageOf('/app/nomajde/x/y')).toBeNull()
    expect(isPageTabPath('/app/nomajde/../x')).toBe(false)
  })
  it("names a function of the application, or one given in full", () => {
    expect(callableRef('nomajde', 'xref:j_xref_search')).toBe('nomajde.xref:j_xref_search')
    expect(callableRef('nomajde', 'nomasx1.xref:j_x')).toBe('nomasx1.xref:j_x')
  })
})

describe('the job editor as a tab', () => {
  it('reads the job a path edits', () => {
    expect(jobEditorOf('/nomaflow/jobs/nomasx1-collect-all')).toBe('nomasx1-collect-all')
    expect(jobEditorOf('/nomaflow/jobs/new')).toBe('')                       // a job being created
    expect(jobEditorOf('/nomaflow/jobs/a%20b')).toBe('a b')
    expect(jobEditorOf('/nomaflow')).toBeNull()
    expect(jobEditorOf('/nomaflow/runs/abc')).toBeNull()
    expect(jobEditorOf('/nomaflow/jobs/x/y')).toBeNull()
    expect(isPageTabPath('/nomaflow/jobs/new')).toBe(true)
    expect(isPageTabPath('/nomaflow/jobs/nomasx1-collect-all')).toBe(true)
  })
})

describe('the Explorer opened on a saved analysis or dashboard', () => {
  it('reads what a published menu entry points at', () => {
    expect(explorerModelOf('/explorer/nomasx1/security/a/roles_per_user')).toEqual({ app: 'nomasx1', model: 'security', analysis: 'roles_per_user' })
    expect(explorerModelOf('/explorer/shop/sales/d/overview')).toEqual({ app: 'shop', model: 'sales', dashboard: 'overview' })
    expect(isPageTabPath('/explorer/shop/sales/a/revenue-by-company')).toBe(true)
    expect(explorerModelOf('/explorer/shop/sales/x/y')).toBeNull()
    expect(explorerModelOf('/explorer/shop/sales/a')).toBeNull()
  })
})

