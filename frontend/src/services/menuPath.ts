// The route a menu leaf opens, and a lookup of a menu item by id — shared by the sidebar (its
// links) and the dashboards' links widget (a card of entry points pointing at menu items, so the
// same permission-pruned, localized tree decides what shows).
import type { MenuNode, MenusByApp } from '../types/menus'

/** The route of a leaf; null for a folder or an unresolvable leaf. */
export function leafPath(node: Pick<MenuNode, 'type' | 'target' | 'connector'>, app: string): string | null {
  if (!node.target) return null
  const t = encodeURIComponent(node.target)
  // Dashboards have no connector segment — the catalog is flat (keyed by id).
  if (node.type === 'dashboard') return `/dashboard/${t}`
  // A job leaf opens the job's own page (run it, follow the log).
  if (node.type === 'job') return `/job/${t}`
  // A `page` leaf's target IS the route — navigate straight to it (e.g. /nomaflow).
  if (node.type === 'page') return node.target
  // A `screen` leaf's target is a screen id, scoped to this menu's app — the dedicated route
  // resolves it (its own columns / dialog), distinct from any other screen on the same query.
  if (node.type === 'screen') return `/screen/${encodeURIComponent(app)}/${t}`
  if (!node.connector) return null
  const c = encodeURIComponent(node.connector)
  return node.type === 'endpoint' ? `/http/${c}/${t}` : `/sql/${c}/${t}`
}

/** The item *id* in *app*'s (permission-pruned) tree, at any depth; undefined when the user
 *  cannot see it or the app has no menu. */
export function findMenuItem(menus: MenusByApp | null | undefined, app: string, id: string): MenuNode | undefined {
  const tree = menus?.[app]
  if (!tree) return undefined
  const walk = (nodes: MenuNode[]): MenuNode | undefined => {
    for (const n of nodes) {
      if (n.id === id) return n
      if (n.items) { const hit = walk(n.items); if (hit) return hit }
    }
    return undefined
  }
  return walk(tree.items)
}
