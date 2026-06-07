// Find the friendly menu label for a screen tab — so the tab strip / page header can show
// "Segregation of duties — process" instead of `nomasx1.settings_sod_process_get`. Walks every
// app's menu tree (GET /api/menus, already localized) for a leaf whose kind + connector +
// target match; returns undefined when the screen isn't reachable from any menu (then the
// caller falls back to the query's `label`/`description` or its technical name).
//
// Dashboards have no connector — match on the dashboard id alone.
import type { MenuNode, MenusByApp } from '../types/menus'

interface ScreenKey {
  kind: 'sql' | 'screen' | 'http' | 'dashboard' | 'page'
  /** Empty for dashboard / page tabs; the screen's APP for screen tabs (its `/screen/<app>/<id>` route). */
  connector: string
  target: string
}

function walk(nodes: MenuNode[], appName: string, key: ScreenKey): string | undefined {
  const wantType = key.kind === 'sql' ? 'query' : key.kind === 'http' ? 'endpoint' : key.kind === 'screen' ? 'screen' : key.kind === 'page' ? 'page' : 'dashboard'
  for (const n of nodes) {
    if (n.type === wantType && n.target === key.target) {
      // Dashboard + page leaves have no connector — match on target alone. A screen leaf is scoped
      // to its menu app (the tab's `connector` carries that app). Others must agree on the resolved
      // connector (the menu defaults `connector` to the app name when blank).
      if (key.kind === 'dashboard' || key.kind === 'page') return n.label
      if (key.kind === 'screen') { if (appName === key.connector) return n.label }
      else if ((n.connector ?? appName) === key.connector) return n.label
    }
    if (n.items) {
      const found = walk(n.items, appName, key)
      if (found) return found
    }
  }
  return undefined
}

export function findMenuLabel(menus: MenusByApp | null | undefined, key: ScreenKey): string | undefined {
  if (!menus) return undefined
  for (const app of Object.values(menus)) {
    const found = walk(app.items, app.app, key)
    if (found) return found
  }
  return undefined
}

/** Sibling to :func:`findMenuLabel` that also returns the owning app's label —
 *  used by TabStrip to render two-line tabs (app name on top, screen label below)
 *  so duplicates like "Users" under nomasx1 + ldap stay distinguishable. Returns
 *  ``undefined`` when no matching menu leaf exists. ``appLabel`` falls back to the
 *  app's key when the AppMenu didn't declare a ``label``. */
export function findMenuLabelWithApp(
  menus: MenusByApp | null | undefined, key: ScreenKey,
): { label: string; app: string; appLabel: string } | undefined {
  if (!menus) return undefined
  for (const app of Object.values(menus)) {
    const found = walk(app.items, app.app, key)
    if (found !== undefined) return { label: found, app: app.app, appLabel: app.label || app.app }
  }
  return undefined
}
