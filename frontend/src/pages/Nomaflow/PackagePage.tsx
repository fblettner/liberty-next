// Deployment package builder, now its own nomaflow page (package management, not a "setting").
// Reached via the nomaflow sidebar menu. The builder component still lives under pages/Settings (it
// shares FindDependenciesModal with the other config editors); this page just frames it.
import { Suspense, lazy } from 'react'
import { useTranslation } from 'react-i18next'
import { Package } from 'lucide-react'
import { PageLayout, Centered } from '../../common'

const PackageBuilder = lazy(() => import('../Settings/PackageBuilder'))

export default function PackagePage() {
  const { t } = useTranslation()
  return (
    <PageLayout icon={<Package size={18} />} title={t('settings.tabs.package', 'Package')}>
      <Suspense fallback={<Centered />}><PackageBuilder /></Suspense>
    </PageLayout>
  )
}
