// The Liberty Next workspace (superuser): the framework's own pages as a menu tree, offered in the app
// picker beside the applications. Built here, not in menus.toml — it ships with the framework and is the
// same on every install — so the applications' sidebars keep their space for the end users' menus.
// Each configuration editor is its own page, `/liberty/<page>` (they were tabs of one Settings page).
import type { AppMenuTree, MenuNode } from '../types/menus'

/** The workspace's id in the app picker — `@` cannot start a connector name, so it never clashes. */
export const LIBERTY_APP = '@liberty'

export const isLibertyApp = (app: string | null | undefined): boolean => app === LIBERTY_APP

/** The pages served at `/liberty/<page>`, in menu order. */
export const LIBERTY_PAGES = [
  'overview', 'studio',
  'connectors', 'dictionary',
  'screens', 'menus',
  'actions', 'tests', 'notifications', 'ai',
  'dashboards', 'charts', 'explorer', 'report-templates',
  'app', 'pools', 'access', 'theme',
  'test-results',
  'integrity', 'package', 'changes', 'versions',
  'reports', 'release-notes',
] as const
export type LibertyPage = typeof LIBERTY_PAGES[number]

const PAGE_PATH = /^\/liberty\/([a-z-]+)$/

/** The Liberty Next page a route path opens, else null. */
export function libertyPageOf(pathname: string): LibertyPage | null {
  const m = PAGE_PATH.exec(pathname)
  return m && (LIBERTY_PAGES as readonly string[]).includes(m[1]) ? (m[1] as LibertyPage) : null
}

export const libertyPath = (page: LibertyPage): string => `/liberty/${page}`

// The former Settings page's `?tab=` ids that were renamed as pages.
const RENAMED: Record<string, LibertyPage> = { reports: 'report-templates', releasenotes: 'release-notes' }

/** Where a former `/settings?tab=<tab>&…` link now goes: the editor's page, its other parameters kept
 *  (`app`, `screen`, … the editors read on their first mount). A missing / unknown tab → Connectors. */
export function settingsRedirect(search: string): string {
  const qp = new URLSearchParams(search)
  const tab = qp.get('tab') ?? ''
  qp.delete('tab')
  const page = RENAMED[tab] ?? tab
  const target = (LIBERTY_PAGES as readonly string[]).includes(page) ? (page as LibertyPage) : 'connectors'
  const rest = qp.toString()
  return libertyPath(target) + (rest ? `?${rest}` : '')
}

type T = (key: string) => string

const route = (id: string, label: string, target: string, icon: string): MenuNode => ({ id, label, type: 'page', target, icon })
const page = (id: LibertyPage, label: string, icon: string): MenuNode => route(id, label, libertyPath(id), icon)

export function libertyMenu(t: T): AppMenuTree {
  const tab = (k: string) => t(`settings.tabs.${k}`)
  return {
    app: LIBERTY_APP,
    label: t('liberty.name'),
    items: [
      // the first thing seen: the applications and how one is built
      page('overview', t('overview.title'), 'layoutdashboard'),
      // Build was one long folder; it is now the steps an application is built in, as the overview tells them
      { id: 'build', label: t('liberty.build'), icon: 'wrench', items: [
        // the Studio: an application as a whole, edited in place — and the assistant that starts a screen
        page('studio', t('studio.title'), 'workflow'),
        { id: 'assistant', label: t('nav.assistant'), action: 'assistant', icon: 'wand' },
      ] },
      { id: 'data', label: t('liberty.data'), icon: 'database', items: [
        // where the data is and how it is read, then the names of its fields
        page('connectors', tab('connectors'), 'database'),
        page('dictionary', tab('dictionary'), 'book'),
      ] },
      { id: 'screens', label: t('liberty.screens'), icon: 'grid', items: [
        // what the users see, and the menu that leads to it
        page('screens', tab('screens'), 'grid'),
        page('menus', tab('menus'), 'folder'),
      ] },
      { id: 'logic', label: t('liberty.logic'), icon: 'workflow', items: [
        // what runs: services made of steps, the tests that check them, who is told, what the assistant is given
        page('actions', tab('actions'), 'play'),
        page('tests', t('tests.title'), 'shieldcheck'),
        page('notifications', tab('notifications'), 'bell'),
        page('ai', tab('ai'), 'sparkles'),
      ] },
      { id: 'insight', label: t('liberty.insight'), icon: 'barchart', items: [
        page('dashboards', tab('dashboards'), 'layoutdashboard'),
        page('charts', tab('charts'), 'barchart'),
        page('explorer', tab('explorer'), 'compass'),
        page('report-templates', t('liberty.reportTemplates'), 'filetext'),
      ] },
      { id: 'administer', label: t('liberty.administer'), icon: 'settings', items: [
        // the install, then its connections, who may sign in, how it looks
        page('app', tab('app'), 'settings'),
        page('pools', tab('pools'), 'network'),
        page('access', tab('access'), 'users'),
        page('theme', tab('theme'), 'palette'),
      ] },
      { id: 'operate', label: t('liberty.operate'), icon: 'activity', items: [
        page('test-results', t('testResults.title'), 'shieldcheck'),
        route('monitoring', t('nav.monitoring'), '/monitoring', 'activity'),
        route('jobs', t('liberty.jobs'), '/nomaflow', 'clock'),
      ] },
      { id: 'deploy', label: t('liberty.deploy'), icon: 'rocket', items: [
        // check first (what is broken), then deliver
        page('integrity', tab('integrity'), 'shieldcheck'),
        page('package', tab('package'), 'package'),
        page('changes', tab('changes'), 'gitcompare'),
        page('versions', tab('versions'), 'history'),
      ] },
      { id: 'document', label: t('liberty.document'), icon: 'book', items: [
        route('developer', t('developer.title'), '/developer', 'code'),
        page('reports', t('docReports.title'), 'filetext'),
        page('release-notes', tab('releasenotes'), 'tag'),
      ] },
    ],
  }
}

/** The label of the Liberty Next leaf that opens *path*, for a tab title. */
export function libertyLabel(menu: AppMenuTree | null, path: string): string | undefined {
  const walk = (ns: MenuNode[]): string | undefined => {
    for (const n of ns) {
      if (n.items) { const hit = walk(n.items); if (hit) return hit }
      else if (n.type === 'page' && n.target === path) return n.label
    }
    return undefined
  }
  return menu ? walk(menu.items) : undefined
}

/** The icon name of the Liberty Next leaf that opens *path* (the tab strip shows it). */
export function libertyIcon(menu: AppMenuTree | null, path: string): string | undefined {
  const walk = (ns: MenuNode[]): string | undefined => {
    for (const n of ns) {
      if (n.items) { const hit = walk(n.items); if (hit) return hit }
      else if (n.type === 'page' && n.target === path) return n.icon
    }
    return undefined
  }
  return menu ? walk(menu.items) : undefined
}
