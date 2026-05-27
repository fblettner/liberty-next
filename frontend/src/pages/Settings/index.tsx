// Settings page = a tab switcher over the config editors: one structured builder per config
// section (Pools, Connectors, Dictionary, Menus, Screens, Dashboards). The raw TOML escape
// hatch was removed — too easy to write invalid configs that don't validate against the
// schema, and every section has a structured editor now. The active tab + the per-tab
// selection (app/screen) ride on the URL search params so a deep link like
// `/settings?tab=screens&app=nomasx1&screen=security_users` opens straight to the right
// place — used by the Connectors → Screens cross-link.
import { lazy, Suspense } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { SlidersHorizontal } from 'lucide-react'
import { PageLayout, Centered } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

const PoolsBuilder = lazy(() => import('./PoolsBuilder'))
const ConnectorsBuilder = lazy(() => import('./ConnectorsBuilder'))
const DictionaryBuilder = lazy(() => import('./DictionaryBuilder'))
const MenusBuilder = lazy(() => import('./MenusBuilder'))
const ScreensBuilder = lazy(() => import('./ScreensBuilder'))
const DashboardsBuilder = lazy(() => import('./DashboardsBuilder'))
const ChartsBuilder = lazy(() => import('./ChartsBuilder'))
const TechnicalDashboard = lazy(() => import('./TechnicalDashboard'))

const TABS = ['pools', 'connectors', 'dictionary', 'menus', 'screens', 'charts', 'dashboards', 'technical'] as const
type Tab = typeof TABS[number]
const isTab = (v: string | null): v is Tab => v != null && (TABS as readonly string[]).includes(v)

const Tabs = styled.div`display: flex; gap: 4px; margin-bottom: 14px;`
const TabBtn = styled.button<{ $active?: boolean }>`
  height: 30px; padding: 0 12px; border-radius: ${radius.md}; cursor: pointer;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`

export default function Settings() {
  const { t } = useTranslation()
  // Tab + per-tab selection live in the URL search params. Switching tab clears the per-tab
  // selection (it's tab-scoped) — the builders read the params on mount to pre-select. A wholly
  // missing `tab` param defaults to `pools`. Anything else (invalid value) is silently coerced.
  const [params, setParams] = useSearchParams()
  const rawTab = params.get('tab')
  const tab: Tab = isTab(rawTab) ? rawTab : 'pools'
  const setTab = (next: Tab) => {
    const np = new URLSearchParams(params)
    np.set('tab', next)
    // Drop the tab-scoped selectors when switching — they belong to the previous tab.
    for (const k of ['app', 'screen']) np.delete(k)
    setParams(np, { replace: true })
  }
  return (
    <PageLayout icon={<SlidersHorizontal size={18} />} title={t('settings.title')}>
      <Tabs>
        <TabBtn $active={tab === 'pools'} onClick={() => setTab('pools')}>{t('settings.tabs.pools')}</TabBtn>
        <TabBtn $active={tab === 'connectors'} onClick={() => setTab('connectors')}>{t('settings.tabs.connectors')}</TabBtn>
        <TabBtn $active={tab === 'dictionary'} onClick={() => setTab('dictionary')}>{t('settings.tabs.dictionary')}</TabBtn>
        <TabBtn $active={tab === 'menus'} onClick={() => setTab('menus')}>{t('settings.tabs.menus')}</TabBtn>
        <TabBtn $active={tab === 'screens'} onClick={() => setTab('screens')}>{t('settings.tabs.screens')}</TabBtn>
        <TabBtn $active={tab === 'charts'} onClick={() => setTab('charts')}>{t('settings.tabs.charts', 'Charts')}</TabBtn>
        <TabBtn $active={tab === 'dashboards'} onClick={() => setTab('dashboards')}>{t('settings.tabs.dashboards')}</TabBtn>
        <TabBtn $active={tab === 'technical'} onClick={() => setTab('technical')}>{t('settings.tabs.technical')}</TabBtn>
      </Tabs>
      <Suspense fallback={<Centered />}>
        {tab === 'pools' ? <PoolsBuilder />
          : tab === 'connectors' ? <ConnectorsBuilder />
          : tab === 'dictionary' ? <DictionaryBuilder />
          : tab === 'menus' ? <MenusBuilder />
          : tab === 'screens' ? <ScreensBuilder />
          : tab === 'charts' ? <ChartsBuilder />
          : tab === 'dashboards' ? <DashboardsBuilder />
          : <TechnicalDashboard />}
      </Suspense>
    </PageLayout>
  )
}
