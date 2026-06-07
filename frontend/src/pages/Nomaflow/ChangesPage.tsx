// Change packages, now its own nomaflow page (captured data changes + promotion — a flow, not a
// setting). Reached via the nomaflow sidebar menu.
import { Suspense, lazy } from 'react'
import { useTranslation } from 'react-i18next'
import { GitCompare } from 'lucide-react'
import { PageLayout, Centered } from '../../common'

const ChangePackagesBuilder = lazy(() => import('../Settings/ChangePackagesBuilder'))

export default function ChangesPage() {
  const { t } = useTranslation()
  return (
    <PageLayout icon={<GitCompare size={18} />} title={t('settings.tabs.changes', 'Changes')}>
      <Suspense fallback={<Centered />}><ChangePackagesBuilder /></Suspense>
    </PageLayout>
  )
}
