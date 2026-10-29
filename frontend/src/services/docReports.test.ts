import { describe, expect, it } from 'vitest'
import { copiesOf, defaultSections, docReportPath, reportCopyPath } from './docReports'
import type { DocReport } from '../types/docReports'

const tech: DocReport = {
  kind: 'technical', title: 'Technical report', subtitle: '',
  sections: [
    { id: 'scope', title: 'Scope', description: '', default: true },
    { id: 'api', title: 'API', description: '', default: true },
    { id: 'appendix', title: 'Appendix', description: '', default: false },
  ],
  options: [{ id: 'api_detail', section: 'api', label: '' }, { id: 'snippets', section: 'appendix', label: '' }],
}

describe('doc reports', () => {
  it('ticks the default sections', () => {
    expect(defaultSections(tech)).toEqual(['scope', 'api'])
  })
  it('builds the path: catalogue order, date, options of ticked sections only', () => {
    const path = docReportPath('technical', tech, {
      lang: 'en', format: 'pdf', sections: ['api', 'scope'], date: ' Q4 2026 ', options: { api_detail: true, snippets: true },
    })
    expect(path).toBe('/admin/dev/reports/technical?lang=en&format=pdf&sections=scope%2Capi&date=Q4+2026&api_detail=true')
  })
})

describe('kept copies', () => {
  const copy = (name: string, kind: 'implementation' | 'technical') =>
    ({ name, kind, lang: 'fr', format: 'pdf' as const, size: 1, created_at: null, title: null, author: null, sections: [], date_label: null })
  it('filters by kind and quotes the name in the path', () => {
    const all = [copy('20261029-010203-technical-fr.pdf', 'technical'), copy('20261029-010000-implementation-fr.pdf', 'implementation')]
    expect(copiesOf(all, 'technical').map((c) => c.name)).toEqual(['20261029-010203-technical-fr.pdf'])
    expect(reportCopyPath('a b.pdf')).toBe('/admin/dev/report-copies/a%20b.pdf')
  })
})
