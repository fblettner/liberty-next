// A page of the Liberty Next workspace, `/liberty/<page>` (superuser): one configuration editor, a
// deployment tool or a documentation page, framed with its menu label and icon. They were the tabs of
// the Settings page (and, for Package / Changes / Integrity, pages of the nomaflow application); the
// editors themselves are unchanged — the configuration ones get the "reload the configuration" button.
import { lazy, Suspense, useMemo, useState, type ComponentType, type LazyExoticComponent } from 'react'
import { useTranslation } from 'react-i18next'
import { Centered, HeaderSlot, HeaderSlotProvider, PageLayout } from '../../common'
import { iconFor } from '../../components/SidebarMenu'
import { libertyIcon, libertyLabel, libertyMenu, libertyPath, type LibertyPage } from '../../services/libertyMenu'
import ReloadConfigButton from './ReloadConfigButton'

type Entry = { C: LazyExoticComponent<ComponentType>; reload?: boolean; framed?: boolean }
const editor = (C: Entry['C']): Entry => ({ C, reload: true })

const PAGES: Record<LibertyPage, Entry> = {
  overview: { C: lazy(() => import('../Settings/LibertyOverview')) },
  studio: { C: lazy(() => import('../Settings/StudioHome')) },
  screens: editor(lazy(() => import('../Settings/ScreensBuilder'))),
  connectors: editor(lazy(() => import('../Settings/ConnectorsBuilder'))),
  dictionary: editor(lazy(() => import('../Settings/DictionaryBuilder'))),
  menus: editor(lazy(() => import('../Settings/MenusBuilder'))),
  actions: editor(lazy(() => import('../Settings/ActionsBuilder'))),
  tests: { C: lazy(() => import('../Settings/TestsBuilder')) },
  'test-results': { C: lazy(() => import('../Settings/TestResults')) },
  charts: editor(lazy(() => import('../Settings/ChartsBuilder'))),
  dashboards: editor(lazy(() => import('../Settings/DashboardsBuilder'))),
  explorer: editor(lazy(() => import('../Settings/ExplorerBuilder'))),
  ai: editor(lazy(() => import('../Settings/AiBuilder'))),
  // operator-authored Jinja markdown templates; the install-wide PDF branding is under App
  'report-templates': editor(lazy(() => import('../Settings/ReportsBuilder'))),
  notifications: editor(lazy(() => import('../Settings/NotificationsBuilder'))),
  integrity: { C: lazy(() => import('../Settings/IntegrityBuilder')) },
  app: editor(lazy(() => import('../Settings/AppBuilder'))),
  pools: editor(lazy(() => import('../Settings/PoolsBuilder'))),
  access: editor(lazy(() => import('../Settings/AccessBuilder'))),
  theme: editor(lazy(() => import('../Settings/ThemeBuilder'))),
  package: { C: lazy(() => import('../Settings/PackageBuilder')) },
  changes: { C: lazy(() => import('../Settings/ChangePackagesBuilder')) },
  versions: { C: lazy(() => import('../Settings/ConfigVersions')) },
  // its own PageLayout (the report cards)
  reports: { C: lazy(() => import('../DocReports')), framed: true },
  'release-notes': { C: lazy(() => import('../Settings/ReleaseNotes')) },
}

export default function LibertyPageView({ page }: { page: LibertyPage }) {
  const { t } = useTranslation()
  const menu = useMemo(() => libertyMenu(t), [t])
  const { C, reload, framed } = PAGES[page]
  const [slot, setSlot] = useState<HTMLElement | null>(null)
  const body = <Suspense fallback={<Centered />}><C /></Suspense>
  if (framed) return body
  const path = libertyPath(page)
  const Icon = iconFor(libertyIcon(menu, path))
  // the page's own buttons (Save, Discard) come to the header, before Reload config — common/HeaderActions
  return (
    <PageLayout icon={Icon ? <Icon size={18} /> : undefined} title={libertyLabel(menu, path) ?? page}
      headerRight={<><HeaderSlot ref={setSlot} />{reload && <ReloadConfigButton />}</>}>
      <HeaderSlotProvider value={slot}>{body}</HeaderSlotProvider>
    </PageLayout>
  )
}
