// The Liberty Next workspace (superuser): the framework's own pages as a menu tree, offered in the app
// picker beside the applications. Built here, not in menus.toml — it ships with the framework and is the
// same on every install — so the applications' sidebars keep their space for the end users' menus.
import type { AppMenuTree, MenuNode } from '../types/menus'

/** The workspace's id in the app picker — `@` cannot start a connector name, so it never clashes. */
export const LIBERTY_APP = '@liberty'

export const isLibertyApp = (app: string | null | undefined): boolean => app === LIBERTY_APP

type T = (key: string) => string

const page = (id: string, label: string, target: string, icon: string): MenuNode => ({ id, label, type: 'page', target, icon })

export function libertyMenu(t: T): AppMenuTree {
  return {
    app: LIBERTY_APP,
    label: t('liberty.name'),
    items: [
      { id: 'build', label: t('liberty.build'), icon: 'wrench', items: [
        { id: 'assistant', label: t('nav.assistant'), action: 'assistant', icon: 'wand' },
        page('settings', t('liberty.configuration'), '/settings', 'settings'),
      ] },
      { id: 'operate', label: t('liberty.operate'), icon: 'activity', items: [
        page('monitoring', t('nav.monitoring'), '/monitoring', 'activity'),
        page('jobs', t('liberty.jobs'), '/nomaflow', 'play'),
        page('integrity', t('settings.tabs.integrity'), '/nomaflow/integrity', 'shieldcheck'),
      ] },
      { id: 'deploy', label: t('liberty.deploy'), icon: 'rocket', items: [
        page('package', t('settings.tabs.package'), '/nomaflow/package', 'package'),
        page('changes', t('settings.tabs.changes'), '/nomaflow/changes', 'gitcompare'),
      ] },
      { id: 'document', label: t('liberty.document'), icon: 'book', items: [
        page('developer', t('developer.title'), '/developer', 'code'),
        page('docreports', t('docReports.title'), '/liberty/reports', 'filetext'),
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
