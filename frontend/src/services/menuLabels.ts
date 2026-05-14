// Find the friendly menu label for a screen tab — so the tab strip / page header can show
// "Segregation of duties — process" instead of `nomasx1.settings_sod_process_get`. Walks every
// app's menu tree (GET /api/menus, already localized) for a leaf whose kind + connector +
// target match; returns undefined when the screen isn't reachable from any menu (then the
// caller falls back to the query's `label`/`description` or its technical name).
//
// Dashboards have no connector — match on the dashboard id alone.
import type { MenuNode, MenusByApp } from '../types/menus'

interface ScreenKey {
  kind: 'sql' | 'http' | 'dashboard'
  /** Empty for dashboard tabs. */
  connector: string
  target: string
}

function walk(nodes: MenuNode[], appName: string, key: ScreenKey): string | undefined {
  const wantType = key.kind === 'sql' ? 'query' : key.kind === 'http' ? 'endpoint' : 'dashboard'
  for (const n of nodes) {
    if (n.type === wantType && n.target === key.target) {
      // Dashboard leaves have no connector — match on target alone. Others must agree on the
      // resolved connector (the menu defaults `connector` to the app name when blank).
      if (key.kind === 'dashboard' || (n.connector ?? appName) === key.connector) return n.label
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
