import { describe, expect, it } from 'vitest'
import { defaultSections, docReportPath } from './docReports'
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
