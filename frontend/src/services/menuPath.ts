// The route a menu leaf opens, and a lookup of a menu item by id — shared by the sidebar (its
// links) and the dashboards' links widget (a card of entry points pointing at menu items, so the
// same permission-pruned, localized tree decides what shows).
import type { MenuNode, MenusByApp } from '../types/menus'

/** A job tab's target: the job id, or ``<job>::<preset>`` when the menu item pins one of the job's
 *  presets (``params.preset``) — ``::`` cannot occur in a job id. */
const PRESET_SEP = '::'
export function jobTarget(node: Pick<MenuNode, 'target' | 'params'>): string {
  const preset = node.params?.preset
  return typeof preset === 'string' && preset ? `${node.target}${PRESET_SEP}${preset}` : (node.target ?? '')
}
export function splitJobTarget(target: string): { jobId: string; preset: string | null } {
  const i = target.indexOf(PRESET_SEP)
  return i < 0 ? { jobId: target, preset: null } : { jobId: target.slice(0, i), preset: target.slice(i + PRESET_SEP.length) }
}

/** The route of a leaf; null for a folder or an unresolvable leaf. */
export function leafPath(node: Pick<MenuNode, 'type' | 'target' | 'connector' | 'params'>, app: string): string | null {
  if (!node.target) return null
  const t = encodeURIComponent(node.target)
  // Dashboards have no connector segment — the catalog is flat (keyed by id).
  if (node.type === 'dashboard') return `/dashboard/${t}`
  // A job leaf opens the job's own page (run it, follow the log).
  if (node.type === 'job') return `/job/${encodeURIComponent(jobTarget(node))}`
  // A `page` leaf's target IS the route — navigate straight to it (e.g. /nomaflow).
  if (node.type === 'page') return node.target
  // A `screen` leaf's target is a screen id, scoped to this menu's app — the dedicated route
  // resolves it (its own columns / dialog), distinct from any other screen on the same query.
  if (node.type === 'screen') return `/screen/${encodeURIComponent(app)}/${t}`
  if (!node.connector) return null
  const c = encodeURIComponent(node.connector)
  return node.type === 'endpoint' ? `/http/${c}/${t}` : `/sql/${c}/${t}`
}

/** Where a leaf leads when it is opened: its route, with the entry's fixed parameters (`params` in menus.toml)
 *  as the query string of a screen, a query or an endpoint — the page fills its inputs from it, as it does for
 *  a link that opens it on something. A job keeps its own use of `params` (the preset, in its route). */
export function leafRoute(node: Pick<MenuNode, 'type' | 'target' | 'connector' | 'params'>, app: string): string | null {
  const path = leafPath(node, app)
  if (!path || !(node.type === 'screen' || node.type === 'query' || node.type === 'endpoint')) return path
  const fixed = Object.entries(node.params ?? {}).filter(([, v]) => v != null && v !== '' && typeof v !== 'object')
  return fixed.length ? `${path}?${new URLSearchParams(fixed.map(([k, v]) => [k, String(v)])).toString()}` : path
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

/** The app a page address belongs to — ``/screen/<app>/…``, ``/explorer/<app>/…``,
 *  ``/dashboard/<app>.<id>``, ``/sql|http/<connector>/…`` — or null (``/``, Settings…). Picks the
 *  app of a link opened in a fresh browser; the caller checks it is one of the user's apps. */
export function appOfPath(pathname: string): string | null {
  const parts = pathname.split('/').filter(Boolean).map((p) => decodeURIComponent(p))
  const [kind, first] = parts
  if (!first) return null
  if (kind === 'screen' || kind === 'explorer' || kind === 'sql' || kind === 'http') return first
  if (kind === 'dashboard') return first.includes('.') ? first.slice(0, first.indexOf('.')) : null
  return null
}
