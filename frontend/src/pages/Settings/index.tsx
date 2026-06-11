// Settings page = a tab switcher over the config editors: one structured builder per config
// section (Pools, Connectors, Dictionary, Menus, Screens, Dashboards). The raw TOML escape
// hatch was removed — too easy to write invalid configs that don't validate against the
// schema, and every section has a structured editor now.
//
// Settings is itself a workspace tab (kept mounted in TabHost), so the active sub-tab lives in
// internal state — it survives switching to another tab and back. It's seeded once from a `?tab=`
// deep link (e.g. the Connectors → Screens cross-link opens
// `/settings?tab=screens&app=nomasx1&screen=security_users`; the per-tab `app`/`screen` selectors
// are still consumed from the URL by ScreensBuilder on its first mount). We don't write the
// sub-tab back to the URL — switching workspace tabs changes the URL, which would clobber it.
import { lazy, Suspense, useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { SlidersHorizontal, RefreshCw } from 'lucide-react'
import { PageLayout, Centered, Button } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'

const PoolsBuilder = lazy(() => import('./PoolsBuilder'))
const ConnectorsBuilder = lazy(() => import('./ConnectorsBuilder'))
const DictionaryBuilder = lazy(() => import('./DictionaryBuilder'))
const MenusBuilder = lazy(() => import('./MenusBuilder'))
const ScreensBuilder = lazy(() => import('./ScreensBuilder'))
const DashboardsBuilder = lazy(() => import('./DashboardsBuilder'))
const ChartsBuilder = lazy(() => import('./ChartsBuilder'))
const ThemeBuilder = lazy(() => import('./ThemeBuilder'))
const AccessBuilder = lazy(() => import('./AccessBuilder'))
const AppBuilder = lazy(() => import('./AppBuilder'))
// Custom reports — operator-authored Jinja markdown templates rendered through
// the framework. PDF branding (the install-wide defaults that wrap every
// report) lives inside AppBuilder now since it's stored in app.toml.
const ReportsBuilder = lazy(() => import('./ReportsBuilder'))
const ActionsBuilder = lazy(() => import('./ActionsBuilder'))
const ConfigVersions = lazy(() => import('./ConfigVersions'))
// NOTE: Package, Changes and Integrity moved OUT of Settings into the nomaflow area — they're
// package/change management + config health (operations flows), not configuration. The builder
// components still live in this folder (they share FindDependenciesModal / FindUsagesModal with the
// other config editors); the nomaflow pages import them. See pages/Nomaflow/{Package,Changes,Integrity}Page.

const TABS = ['pools', 'connectors', 'dictionary', 'actions', 'menus', 'screens', 'charts', 'dashboards', 'reports', 'theme', 'access', 'app', 'versions'] as const
type Tab = typeof TABS[number]
const isTab = (v: string | null): v is Tab => v != null && (TABS as readonly string[]).includes(v)

const Tabs = styled.div`display: flex; flex-wrap: wrap; align-items: center; gap: 4px; margin-bottom: 14px;`
const TabGroupLabel = styled.span`
  font-size: ${fontSize.micro}; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em;
  color: ${colors.text.muted}; padding: 0 6px 0 2px;
`
const TabSep = styled.span`width: 1px; height: 18px; background: ${colors.border}; margin: 0 8px;`
const ReloadWrap = styled.div`margin-left: auto; display: flex; align-items: center; gap: 10px;`
const ReloadMsg = styled.span<{ $tone: 'ok' | 'err' }>`
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: ${(p) => (p.$tone === 'ok' ? colors.green.main : colors.red.main)};
`
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
  // Active sub-tab is internal state (seeded once from a `?tab=` deep link) so it survives
  // workspace-tab switches; a missing/invalid `tab` param defaults to `connectors` (the most-used).
  const [params] = useSearchParams()
  const [tab, setTab] = useState<Tab>(() => {
    const raw = params.get('tab')
    return isTab(raw) ? raw : 'connectors'
  })
  // Deep-link: when a ?tab= arrives (e.g. Connectors' "Open in Screens" link) switch to it even if
  // Settings is already open. We only act when the param is present — switching workspace tabs drops
  // it from the URL, and we must NOT reset the sub-tab then (it lives in state to survive switches).
  useEffect(() => {
    const raw = params.get('tab')
    if (isTab(raw)) setTab(raw)
  }, [params])

  // Force-reload all TOML. The editors save the file but don't re-read it into the running server (and
  // the opt-in file-watcher is partial + swallows errors), so this is the deterministic "apply now":
  // POST /admin/reload re-reads every config, rebuilds the connector registry, and swaps app.state.
  const [reloading, setReloading] = useState(false)
  const [reloadMsg, setReloadMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const reloadAll = async () => {
    setReloading(true); setReloadMsg(null)
    try {
      const r = await api.post<{ connectors: string[]; dictionary_entries: number; screen_apps: string[] }>('/admin/reload')
      setReloadMsg({ tone: 'ok', text: t('settings.reloadDone', { c: r.connectors.length, d: r.dictionary_entries, s: r.screen_apps.length, defaultValue: 'Reloaded — {{c}} connectors, {{d}} dictionary entries, {{s}} apps.' }) })
    } catch (e) {
      setReloadMsg({ tone: 'err', text: e instanceof ApiError ? e.message : String(e) })
    } finally {
      setReloading(false)
      window.setTimeout(() => setReloadMsg(null), 6000)
    }
  }
  return (
    <PageLayout icon={<SlidersHorizontal size={18} />} title={t('settings.title')}>
      <Tabs>
        {/* Per-connector editors — each scopes to a connector (an app or a data source). */}
        <TabGroupLabel>{t('settings.tabGroups.perConnector', 'Per connector')}</TabGroupLabel>
        <TabBtn $active={tab === 'connectors'} onClick={() => setTab('connectors')}>{t('settings.tabs.connectors')}</TabBtn>
        <TabBtn $active={tab === 'dictionary'} onClick={() => setTab('dictionary')}>{t('settings.tabs.dictionary')}</TabBtn>
        <TabBtn $active={tab === 'actions'} onClick={() => setTab('actions')}>{t('settings.tabs.actions', 'Actions')}</TabBtn>
        <TabBtn $active={tab === 'menus'} onClick={() => setTab('menus')}>{t('settings.tabs.menus')}</TabBtn>
        <TabBtn $active={tab === 'screens'} onClick={() => setTab('screens')}>{t('settings.tabs.screens')}</TabBtn>
        <TabBtn $active={tab === 'charts'} onClick={() => setTab('charts')}>{t('settings.tabs.charts', 'Charts')}</TabBtn>
        <TabBtn $active={tab === 'dashboards'} onClick={() => setTab('dashboards')}>{t('settings.tabs.dashboards')}</TabBtn>
        {/* Reports — operator-authored Jinja markdown templates rendered through
            the framework. Sits next to dashboards as the other "visualization
            output" surface. Install-wide PDF branding is in the App tab now. */}
        <TabBtn $active={tab === 'reports'} onClick={() => setTab('reports')}>{t('settings.tabs.reports', 'Reports')}</TabBtn>
        <TabSep />
        {/* Shared — install-wide, not tied to any connector/app. */}
        <TabGroupLabel>{t('settings.tabGroups.shared', 'Shared')}</TabGroupLabel>
        <TabBtn $active={tab === 'pools'} onClick={() => setTab('pools')}>{t('settings.tabs.pools')}</TabBtn>
        <TabBtn $active={tab === 'theme'} onClick={() => setTab('theme')}>{t('settings.tabs.theme', 'Theme')}</TabBtn>
        <TabBtn $active={tab === 'access'} onClick={() => setTab('access')}>{t('settings.tabs.access', 'Access')}</TabBtn>
        <TabBtn $active={tab === 'app'} onClick={() => setTab('app')}>{t('settings.tabs.app', 'App')}</TabBtn>
        <TabBtn $active={tab === 'versions'} onClick={() => setTab('versions')}>{t('settings.tabs.versions', 'History')}</TabBtn>
        {/* Deterministic "apply all TOML now" — see reloadAll. Right-pushed so it reads as a global action. */}
        <ReloadWrap>
          {reloadMsg && <ReloadMsg $tone={reloadMsg.tone}>{reloadMsg.text}</ReloadMsg>}
          <Button $size="sm" $variant="ghost" onClick={reloadAll} disabled={reloading} title={t('settings.reloadHint', 'Re-read every config file into the running server')}>
            <RefreshCw size={13} /> {reloading ? t('settings.reloading', 'Reloading…') : t('settings.reloadConfig', 'Reload config')}
          </Button>
        </ReloadWrap>
      </Tabs>
      <Suspense fallback={<Centered />}>
        {tab === 'pools' ? <PoolsBuilder />
          : tab === 'connectors' ? <ConnectorsBuilder />
          : tab === 'dictionary' ? <DictionaryBuilder />
          : tab === 'menus' ? <MenusBuilder />
          : tab === 'screens' ? <ScreensBuilder />
          : tab === 'charts' ? <ChartsBuilder />
          : tab === 'dashboards' ? <DashboardsBuilder />
          : tab === 'reports' ? <ReportsBuilder />
          : tab === 'actions' ? <ActionsBuilder />
          : tab === 'theme' ? <ThemeBuilder />
          : tab === 'access' ? <AccessBuilder />
          : tab === 'versions' ? <ConfigVersions />
          : <AppBuilder />}
      </Suspense>
    </PageLayout>
  )
}
