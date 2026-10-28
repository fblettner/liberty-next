// Liberty Next → Document → Documentation reports (C12, superuser): the framework documented as PDF or
// Markdown — the implementation report (this install) and the technical report (the release), picked
// from two cards. Framework-wide, so it lives in the Liberty Next workspace, not in an application's
// Reports page.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { BookOpen, ClipboardList } from 'lucide-react'
import { Banner, PageLayout, ViewCards } from '../../common'
import { ApiError } from '../../api/client'
import { colors } from '../../theme'
import { getDocReports } from '../../services/docReports'
import type { DocReport, DocReportKind } from '../../types/docReports'
import DocReportView from './DocReportView'

export default function DocReportsPage() {
  const { t, i18n } = useTranslation()
  const [kind, setKind] = useState<DocReportKind>('implementation')
  const [reports, setReports] = useState<DocReport[]>([])
  const [error, setError] = useState<string | null>(null)
  const lang = i18n.language?.startsWith('en') ? 'en' : 'fr'
  useEffect(() => {
    getDocReports(lang).then(setReports).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [lang])
  const doc = (k: DocReportKind) => reports.find((r) => r.kind === k)

  return (
    <PageLayout icon={<BookOpen size={18} />} title={t('docReports.title')} description={t('docReports.intro')}>
      {error && <Banner $tone="error">{error}</Banner>}
      <div style={{ marginBottom: 16 }}>
        <ViewCards value={kind} onChange={setKind} items={[
          { id: 'implementation', label: doc('implementation')?.title ?? '…', desc: doc('implementation')?.subtitle ?? '', icon: <ClipboardList size={18} />, color: colors.purple.main },
          { id: 'technical', label: doc('technical')?.title ?? '…', desc: doc('technical')?.subtitle ?? '', icon: <BookOpen size={18} />, color: colors.green.main },
        ]} />
      </div>
      <DocReportView kind={kind} report={doc(kind)} />
    </PageLayout>
  )
}
