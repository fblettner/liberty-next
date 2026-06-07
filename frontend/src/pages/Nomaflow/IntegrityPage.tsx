// Config integrity diagnostics, now its own nomaflow page (a health/operations check, not a
// setting). Reached via the nomaflow sidebar menu.
import { Suspense, lazy } from 'react'
import { useTranslation } from 'react-i18next'
import { ShieldCheck } from 'lucide-react'
import { PageLayout, Centered } from '../../common'

const IntegrityBuilder = lazy(() => import('../Settings/IntegrityBuilder'))

export default function IntegrityPage() {
  const { t } = useTranslation()
  return (
    <PageLayout icon={<ShieldCheck size={18} />} title={t('settings.tabs.integrity', 'Integrity')}>
      <Suspense fallback={<Centered />}><IntegrityBuilder /></Suspense>
    </PageLayout>
  )
}
