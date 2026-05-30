// Monitoring page — the live runtime view (connected users, held locks, per-pool SQLAlchemy
// stats, AI / license / runtime info, log tail). Wraps the existing TechnicalDashboard component
// (previously a Settings sub-tab) in a PageLayout so it can live as its own workspace tab,
// reachable from the sidebar above Settings.
import { Suspense, lazy } from 'react'
import { Activity } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { PageLayout, Centered } from '../../common'

const TechnicalDashboard = lazy(() => import('../Settings/TechnicalDashboard'))

export default function Monitoring() {
  const { t } = useTranslation()
  return (
    <PageLayout icon={<Activity size={18} />} title={t('nav.monitoring', 'Monitoring')}>
      <Suspense fallback={<Centered />}>
        <TechnicalDashboard />
      </Suspense>
    </PageLayout>
  )
}
