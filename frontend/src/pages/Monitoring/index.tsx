// Monitoring page — the live runtime view (connected users, held locks, per-pool SQLAlchemy
// stats, AI / license / runtime info, the live log tail) and the Server log (the log file:
// search, export, download, levels per module) and Retention (every store's policy, size, preview
// and purge). Its own workspace tab, above Settings.
import { Suspense, lazy, useState } from 'react'
import styled from '@emotion/styled'
import { Activity } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { PageLayout, Centered } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

const TechnicalDashboard = lazy(() => import('../Settings/TechnicalDashboard'))
const ServerLog = lazy(() => import('./ServerLog'))
const RetentionCenter = lazy(() => import('./RetentionCenter'))
const TAB_KEY = 'liberty.monitoring.tab'

const Tabs = styled.div`display: flex; gap: 6px; margin-bottom: 14px;`
const TabBtn = styled.button<{ $on: boolean }>`
  padding: 5px 14px; border-radius: ${radius.md}; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  border: 1px solid ${({ $on }) => ($on ? colors.blue.border : colors.border)};
  background: ${({ $on }) => ($on ? colors.blue.bg : colors.bg.card)};
  color: ${({ $on }) => ($on ? colors.text.primary : colors.text.secondary)};
`

export default function Monitoring() {
  const { t } = useTranslation()
  type Tab = 'live' | 'log' | 'retention'
  const [tab, setTab] = useState<Tab>(() => { try { const v = localStorage.getItem(TAB_KEY); return v === 'log' || v === 'retention' ? v : 'live' } catch { return 'live' } })
  const pick = (v: Tab) => { setTab(v); try { localStorage.setItem(TAB_KEY, v) } catch { /* ignore */ } }
  return (
    <PageLayout icon={<Activity size={18} />} title={t('nav.monitoring')}>
      <Tabs>
        <TabBtn type="button" $on={tab === 'live'} onClick={() => pick('live')}>{t('serverLog.tabLive')}</TabBtn>
        <TabBtn type="button" $on={tab === 'log'} onClick={() => pick('log')}>{t('serverLog.tabLog')}</TabBtn>
        <TabBtn type="button" $on={tab === 'retention'} onClick={() => pick('retention')}>{t('retentionCenter.tab')}</TabBtn>
      </Tabs>
      <Suspense fallback={<Centered />}>
        {tab === 'live' ? <TechnicalDashboard /> : tab === 'log' ? <ServerLog /> : <RetentionCenter />}
      </Suspense>
    </PageLayout>
  )
}
