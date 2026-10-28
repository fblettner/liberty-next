// Pure helpers behind the role editor (no React): the menu forest the tree renders, the
// backend's colon-segment glob matcher, and the two-way mapping between a role's permission
// strings and the tree's three-state model (inherit / allow / deny per menu node + the "other"
// rules menus don't cover). The strings written are exactly what the old chip editor wrote —
// `menu:<app>:<id>` for the node, `menu:` + the target permission for every leaf under an
// allowed / denied node — so existing roles and the backend's `_keeper` are untouched.

import { hasPermission, isDenied } from '../../../services/permissions'
// the backend's matcher lives in services/ (shared with the screens that gate an action)
export { hasPermission, isDenied, segmentMatch } from '../../../services/permissions'

export interface MenuItemRaw { id: string; parent?: string | null; label?: string; type?: string | null; connector?: string | null; target?: string | null }
export type MenusCatalog = Record<string, { items: MenuItemRaw[] }>
export interface ScreenRef { id: string; connector: string; read_query: string }
export type NodeState = 'allow' | 'deny'

export interface MenuNode {
  app: string
  id: string
  /** The node's own permission: `menu:<app>:<id>`. */
  key: string
  label: string
  type: string | null
  target: string | null
  /** What the leaf runs: `sql:<c>:<q>` / `api:<c>:<e>` / `dashboard:<id>`; `nomaflow:view` for a nomaflow page; null for folders and the other pages. */
  targetPerm: string | null
  children: MenuNode[]
}
export type MenuForest = Record<string, MenuNode[]>

export const nodeKey = (app: string, id: string): string => `menu:${app}:${id}`
export const isLeaf = (n: MenuNode): boolean => !!n.type && !!n.target

/** The permission a `page` leaf's route needs — the nomaflow pages ask `nomaflow:view` (which
 *  `nomaflow:edit` includes); other pages are open (liberty/web/menus.py `page_permission`). */
export const pagePermission = (target: string): string | null =>
  target === '/nomaflow' || target.startsWith('/nomaflow/') ? 'nomaflow:view' : null
const INCLUDED_IN: Record<string, string[]> = { 'nomaflow:view': ['nomaflow:edit'] }

/** The permission a menu leaf's target needs — the same resolution as the backend's `_keeper`:
 *  a `screen` leaf runs its screen's read query on the screen's connector. */
export function targetPermission(app: string, item: MenuItemRaw, screens: Record<string, ScreenRef[]>): string | null {
  if (!item.type || !item.target) return null
  if (item.type === 'dashboard') return `dashboard:${item.target}`
  if (item.type === 'job') return `job:${item.target}`
  if (item.type === 'page') return pagePermission(item.target)
  if (item.type === 'url') return null
  if (item.type === 'screen') {
    const scr = (screens[app] ?? []).find((s) => s.id === item.target)
    return scr ? `sql:${scr.connector}:${scr.read_query}` : null
  }
  const conn = item.connector || app
  return `${item.type === 'endpoint' ? 'api' : 'sql'}:${conn}:${item.target}`
}

export function buildMenuForest(menus: MenusCatalog, screens: Record<string, ScreenRef[]>): MenuForest {
  const out: MenuForest = {}
  for (const [app, m] of Object.entries(menus)) {
    const items = m.items ?? []
    const ids = new Set(items.map((i) => i.id))
    const nodes = new Map<string, MenuNode>()
    for (const it of items) {
      nodes.set(it.id, {
        app, id: it.id, key: nodeKey(app, it.id), label: it.label || it.id, type: it.type ?? null, target: it.target ?? null,
        targetPerm: targetPermission(app, it, screens), children: [],
      })
    }
    const roots: MenuNode[] = []
    for (const it of items) {
      const node = nodes.get(it.id)!
      if (it.parent && ids.has(it.parent) && it.parent !== it.id) nodes.get(it.parent)!.children.push(node)
      else roots.push(node)
    }
    out[app] = roots
  }
  return out
}

export function flattenForest(forest: MenuForest): MenuNode[] {
  const out: MenuNode[] = []
  const walk = (n: MenuNode) => { out.push(n); n.children.forEach(walk) }
  for (const roots of Object.values(forest)) roots.forEach(walk)
  return out
}


/** Whether the sidebar shows a leaf for a user holding *perms* — the `_keeper` rule: an explicit
 *  menu deny hides it; a dashboard needs no grant (existence is the gate) unless denied; a page
 *  is shown unless its feature area has a permission (the nomaflow pages); otherwise the target must be runnable or the menu explicitly allowed. */
export function leafVisible(perms: string[], node: MenuNode): boolean {
  if (isDenied(perms, node.key)) return false
  if (node.type === 'dashboard') return !isDenied(perms, node.targetPerm ?? '')
  if (node.type === 'url') return true   // an external address: the menu deny (above) gates it
  if (node.type === 'page') return node.targetPerm == null || hasPermission(perms, node.key)
    || [node.targetPerm, ...(INCLUDED_IN[node.targetPerm] ?? [])].some((p) => hasPermission(perms, p))
  if (node.targetPerm == null) return hasPermission(perms, node.key)
  return hasPermission(perms, node.targetPerm) || hasPermission(perms, node.key)
}

// ── the editor's model ─────────────────────────────────────────────────────────────────────
export interface RoleModel {
  /** Baseline: `*` (everything, minus the denies) or nothing (plus the allows). */
  full: boolean
  /** Explicit state per node key; a node without one inherits its nearest ancestor's, else the baseline. */
  states: Record<string, NodeState>
  /** Rules the tree doesn't express: connector / query / API / plugin / AI / dashboard-only patterns. */
  other: string[]
}

const prefixed = (state: NodeState, perm: string): string => (state === 'deny' ? '!' : '') + perm
const strip = (p: string): string => (p.startsWith('!') ? p.slice(1) : p)

function generate(model: Pick<RoleModel, 'full' | 'states'>, forest: MenuForest): string[] {
  const out: string[] = []
  const push = (s: string) => { if (!out.includes(s)) out.push(s) }
  if (model.full) push('*')
  const walk = (n: MenuNode, inherited: NodeState | null) => {
    const own = model.states[n.key] ?? null
    if (own) push(prefixed(own, n.key))
    const eff = own ?? inherited
    if (eff && isLeaf(n)) {
      if (!own) push(prefixed(eff, n.key))
      if (n.targetPerm) push(prefixed(eff, n.targetPerm))
    }
    n.children.forEach((c) => walk(c, eff))
  }
  for (const roots of Object.values(forest)) roots.forEach((r) => walk(r, null))
  return out
}

/** Drop explicit states that equal what the node would inherit anyway (the baseline counts as an
 *  inherited allow), so a round trip through the strings is canonical. */
function normalize(states: Record<string, NodeState>, full: boolean, forest: MenuForest): Record<string, NodeState> {
  const out: Record<string, NodeState> = { ...states }
  const walk = (n: MenuNode, inherited: NodeState | null) => {
    const own = out[n.key] ?? null
    if (own && own === inherited) delete out[n.key]
    const eff = (out[n.key] ?? null) ?? inherited
    n.children.forEach((c) => walk(c, eff))
  }
  for (const roots of Object.values(forest)) roots.forEach((r) => walk(r, full ? 'allow' : null))
  return out
}

export function parsePermissions(perms: string[], forest: MenuForest): RoleModel {
  const full = perms.includes('*')
  const nodes = flattenForest(forest)
  const byKey = new Map(nodes.map((n) => [n.key, n]))
  const byTarget = new Map<string, MenuNode[]>()
  for (const n of nodes) if (n.targetPerm) byTarget.set(n.targetPerm, [...(byTarget.get(n.targetPerm) ?? []), n])
  let states: Record<string, NodeState> = {}
  for (const p of perms) {
    const core = strip(p)
    if (!byKey.has(core)) continue
    if (p.startsWith('!') || !states[core]) states[core] = p.startsWith('!') ? 'deny' : 'allow'   // deny wins
  }
  // A leaf whose target is granted / denied by name without a menu entry (roles written by hand or
  // by the CLI) shows as that state too — the backend surfaces the leaf from its target.
  for (const p of perms) {
    for (const n of byTarget.get(strip(p)) ?? []) {
      if (!states[n.key]) states[n.key] = p.startsWith('!') ? 'deny' : 'allow'
    }
  }
  states = normalize(states, full, forest)
  const generated = new Set(generate({ full, states }, forest))
  const other = perms.filter((p) => p !== '*' && p !== '!*' && !generated.has(p) && !byKey.has(strip(p)))
  return { full, states, other: [...new Set(other)] }
}

export function buildPermissions(model: RoleModel, forest: MenuForest): string[] {
  const out = generate(model, forest)
  for (const p of model.other) if (p !== '*' && !out.includes(p)) out.push(p)
  return out
}

export interface NodeEffect {
  own: NodeState | null
  /** The state the node takes without an own one: the nearest ancestor's, else the baseline. */
  inherited: NodeState | null
  /** For leaves: shown in the sidebar for a holder of the full rule set (globs in `other` included). */
  visible: boolean | null
}

export function nodeEffects(model: RoleModel, forest: MenuForest): Record<string, NodeEffect> {
  const perms = buildPermissions(model, forest)
  const out: Record<string, NodeEffect> = {}
  const walk = (n: MenuNode, inherited: NodeState | null) => {
    const own = model.states[n.key] ?? null
    out[n.key] = { own, inherited, visible: isLeaf(n) ? leafVisible(perms, n) : null }
    n.children.forEach((c) => walk(c, own ?? inherited))
  }
  for (const roots of Object.values(forest)) roots.forEach((r) => walk(r, model.full ? 'allow' : null))
  return out
}
