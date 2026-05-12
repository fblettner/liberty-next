// Find the friendly menu label for a `/sql` / `/http` screen — so tabs and page headers can
// show "Segregation of duties — process" instead of `nomasx1.settings_sod_process_get`. Walks
// every app's menu tree (GET /api/menus, already localized) for a leaf whose connector + target
// match; returns undefined when the screen isn't reachable from any menu (then the caller falls
// back to the query's `label`/`description` or its technical name).
import type { MenuNode, MenusByApp } from '../types/menus'

interface ScreenKey {
  kind: 'sql' | 'http'
  connector: string
  target: string
}

function walk(nodes: MenuNode[], appName: string, key: ScreenKey): string | undefined {
  const wantType = key.kind === 'sql' ? 'query' : 'endpoint'
  for (const n of nodes) {
    if (n.type === wantType && n.target === key.target && (n.connector ?? appName) === key.connector) return n.label
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
