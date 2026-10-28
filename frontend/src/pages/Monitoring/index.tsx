// Monitoring page — the live runtime view (connected users, held locks, per-pool SQLAlchemy
// stats, AI / license / runtime info, the live log tail) and the Server log (the log file:
// search, export, download, levels per module) and Retention (every store's policy, size, preview
// and purge). Its own workspace tab, above Settings.
import { Suspense, lazy, useState } from 'react'
import { Activity, Archive, ScrollText } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { PageLayout, Centered, ViewCards } from '../../common'
import { colors } from '../../theme'

const TechnicalDashboard = lazy(() => import('../Settings/TechnicalDashboard'))
const ServerLog = lazy(() => import('./ServerLog'))
const RetentionCenter = lazy(() => import('./RetentionCenter'))
const TAB_KEY = 'liberty.monitoring.tab'

export default function Monitoring() {
  const { t } = useTranslation()
  type Tab = 'live' | 'log' | 'retention'
  const [tab, setTab] = useState<Tab>(() => { try { const v = localStorage.getItem(TAB_KEY); return v === 'log' || v === 'retention' ? v : 'live' } catch { return 'live' } })
  const pick = (v: Tab) => { setTab(v); try { localStorage.setItem(TAB_KEY, v) } catch { /* ignore */ } }
  return (
    <PageLayout icon={<Activity size={18} />} title={t('nav.monitoring')}>
      <div style={{ marginBottom: 14 }}>
        <ViewCards<Tab> compact value={tab} onChange={pick} items={[
          { id: 'live', label: t('serverLog.tabLive'), desc: t('serverLog.tabLiveDesc'), icon: <Activity size={15} />, color: colors.green.main },
          { id: 'log', label: t('serverLog.tabLog'), desc: t('serverLog.tabLogDesc'), icon: <ScrollText size={15} />, color: colors.blue.main },
          { id: 'retention', label: t('retentionCenter.tab'), desc: t('retentionCenter.tabDesc'), icon: <Archive size={15} />, color: colors.orange.main },
        ]} />
      </div>
      <Suspense fallback={<Centered />}>
        {tab === 'live' ? <TechnicalDashboard /> : tab === 'log' ? <ServerLog /> : <RetentionCenter />}
      </Suspense>
    </PageLayout>
  )
}
