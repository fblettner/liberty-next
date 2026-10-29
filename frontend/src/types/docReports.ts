// The documentation reports (/admin/dev/reports — C12): the implementation report (this install) and the
// technical report (the release), their sections and options.
export type DocReportKind = 'implementation' | 'technical'

export interface DocReportSection { id: string; title: string; description: string; default: boolean }
export interface DocReportOption { id: string; section: string; label: string }

export interface DocReport {
  kind: DocReportKind
  title: string
  subtitle: string
  sections: DocReportSection[]
  options: DocReportOption[]
}

export interface DocReportRequest {
  lang: 'fr' | 'en'
  format: 'pdf' | 'markdown'
  sections: string[]
  date?: string
  options?: Record<string, boolean>
}

/** A kept copy of a generated report (``<config dir>/.reports/``). */
export interface ReportCopy {
  name: string
  kind: DocReportKind | null
  lang: string | null
  format: 'pdf' | 'markdown'
  size: number
  created_at: string | null
  title: string | null
  author: string | null
  sections: string[]
  date_label: string | null
}
