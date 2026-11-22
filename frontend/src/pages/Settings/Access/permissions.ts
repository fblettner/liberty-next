// Pure helpers behind the role editor (no React): the menu forest the tree renders, the
// backend's colon-segment glob matcher, and the two-way mapping between a role's permission
// strings and the tree's model: per menu node, nothing of its own (it follows its folder), READ,
// READ AND CHANGE, or hidden — plus the "other" rules menus don't cover.
//
// A role stores the INTENT: `menu:<app>:<id>` for read, `edit:<app>:<id>` on top for change, on
// the node the person ticked. The server works out what the entries under it need — their read
// query, the lists behind their dropdowns, their record form's tabs, and for "change" what
// writes (liberty/auth/implied.py). So a role is a few lines, and follows its folder when an
// upgrade adds a screen to it. A hidden node still names each entry under it and its target
// (`!menu:…`, `!sql:…`): that is what takes it away from a role that has everything.
// Roles written before (one `menu:` + one target per entry) are read as the same states.

import { hasPermission, isDenied } from '../../../services/permissions'
import { appPageOf, explorerModelOf } from '../../../tabs/pageTabs'
// the backend's matcher lives in services/ (shared with the screens that gate an action)
export { hasPermission, isDenied, segmentMatch } from '../../../services/permissions'

export interface MenuItemRaw { id: string; parent?: string | null; label?: string; type?: string | null; connector?: string | null; target?: string | null }
export type MenusCatalog = Record<string, { items: MenuItemRaw[] }>
export interface ScreenRef { id: string; connector: string; read_query: string }
/** `allow` = read; `change` = read and change; `deny` = hidden. */
export type NodeState = 'allow' | 'change' | 'deny'

export interface MenuNode {
  app: string
  id: string
  /** The node's own permission: `menu:<app>:<id>`. */
  key: string
  /** The node's "change" permission: `edit:<app>:<id>`. */
  editKey: string
  /** What the entry gives at each level, as the server works it out (GET /admin/access/menu-rules). */
  needs?: { read: string[]; change: string[] }
  label: string
  type: string | null
  target: string | null
  /** What the leaf runs: `sql:<c>:<q>` / `api:<c>:<e>` / `dashboard:<id>`; `nomaflow:view` for a nomaflow page; null for folders and the other pages. */
  targetPerm: string | null
  /** Permissions any one of which also shows the leaf: a dashboard's data-widget queries, the
   *  application's explorer models for its Data Explorer page (GET /admin/access/menu-rules). */
  anyOf?: string[]
  /** A leaf that needs no permission (a dashboard of texts and links, an open page). */
  open?: boolean
  children: MenuNode[]
}
/** `GET /admin/access/menu-rules` — what shows a menu entry beyond its own target. */
export interface MenuRules {
  dashboards: Record<string, { permissions: string[]; open: boolean }>
  explorer: Record<string, string[]>
  /** application → entry id → what a grant on it gives (absent from an older server). */
  needs?: Record<string, Record<string, { read: string[]; change: string[] }>>
}
export const EXPLORER_PAGE = '/explorer'
export type MenuForest = Record<string, MenuNode[]>

export const nodeKey = (app: string, id: string): string => `menu:${app}:${id}`
export const editKey = (app: string, id: string): string => `edit:${app}:${id}`
export const isLeaf = (n: MenuNode): boolean => !!n.type && !!n.target

/** The permission a `page` leaf's route needs — the nomaflow pages ask `nomaflow:view` (which
 *  `nomaflow:edit` includes), the tests' pages `tests:view` (which `tests:run` / `tests:edit` include); other pages are open (liberty/web/menus.py `page_permission`). */
export const pagePermission = (target: string): string | null => {
  const shipped = appPageOf(target)                                  // a page an application ships: its own permission
  if (shipped) return `page:${shipped.app}:${shipped.page}`
  const onModel = explorerModelOf(target)                             // the Explorer on one model, or on a published analysis / dashboard
  if (onModel) return `explorer:${onModel.app}:${onModel.model}`
  return target === '/nomaflow' || target.startsWith('/nomaflow/') ? 'nomaflow:view'
    : target === '/liberty/tests' || target === '/liberty/test-results' ? 'tests:view' : null
}
const INCLUDED_IN: Record<string, string[]> = { 'nomaflow:view': ['nomaflow:edit'], 'tests:view': ['tests:run', 'tests:edit'] }

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

/** What else shows a leaf, from the server's rules: `anyOf` / `open` (absent without rules). */
function leafRule(app: string, it: MenuItemRaw, rules: MenuRules | undefined): Pick<MenuNode, 'anyOf' | 'open'> {
  if (!rules || !it.target) return {}
  if (it.type === 'dashboard') {
    const d = rules.dashboards[it.target]
    return d ? { anyOf: d.permissions, open: d.open } : {}
  }
  if (it.type === 'page' && it.target === EXPLORER_PAGE) {
    const models = rules.explorer[app] ?? []
    return models.length ? { anyOf: models.map((m) => `explorer:${app}:${m}`) } : {}
  }
  return {}
}

export function buildMenuForest(menus: MenusCatalog, screens: Record<string, ScreenRef[]>, rules?: MenuRules): MenuForest {
  const out: MenuForest = {}
  for (const [app, m] of Object.entries(menus)) {
    const items = m.items ?? []
    const ids = new Set(items.map((i) => i.id))
    const nodes = new Map<string, MenuNode>()
    for (const it of items) {
      nodes.set(it.id, {
        app, id: it.id, key: nodeKey(app, it.id), editKey: editKey(app, it.id), label: it.label || it.id, type: it.type ?? null, target: it.target ?? null,
        targetPerm: targetPermission(app, it, screens), ...leafRule(app, it, rules),
        ...(rules?.needs?.[app]?.[it.id] ? { needs: rules.needs[app][it.id] } : {}), children: [],
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
 *  menu deny hides it; a dashboard is shown to who can read one of its data widgets or holds
 *  `dashboard:<id>` / the menu allow (texts and links only: to all); the Data Explorer page to who
 *  may use a model of the application; another page unless its feature area has a permission
 *  (the nomaflow pages); otherwise the target must be runnable or the menu explicitly allowed. */
export function leafVisible(perms: string[], node: MenuNode): boolean {
  if (isDenied(perms, node.key)) return false
  if (node.type === 'dashboard') {
    if (isDenied(perms, node.targetPerm ?? '')) return false
    if (node.anyOf === undefined) return true   // no rules loaded: the entry's existence, as before
    return !!node.open || hasPermission(perms, node.targetPerm ?? '') || hasPermission(perms, node.key) || node.anyOf.some((p) => hasPermission(perms, p))
  }
  if (node.type === 'url') return true   // an external address: the menu deny (above) gates it
  if (node.type === 'page' && node.anyOf !== undefined) return hasPermission(perms, node.key) || node.anyOf.some((p) => hasPermission(perms, p))
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

const strip = (p: string): string => (p.startsWith('!') ? p.slice(1) : p)
const allowed = (s: NodeState | null): boolean => s === 'allow' || s === 'change'

function generate(model: Pick<RoleModel, 'full' | 'states'>, forest: MenuForest): string[] {
  const out: string[] = []
  const push = (s: string) => { if (!out.includes(s)) out.push(s) }
  if (model.full) push('*')
  const walk = (n: MenuNode, inherited: NodeState | null) => {
    const own = model.states[n.key] ?? null
    if (own === 'deny') push(`!${n.key}`)
    else if (own === 'change') { push(n.key); push(n.editKey) }
    else if (own === 'allow') {
      push(n.key)
      if (inherited === 'change') push(`!${n.editKey}`)     // read only, inside a folder given in change
    }
    const eff = own ?? inherited
    // hidden: each entry under it and its target by name — what takes it away from a role that has everything
    if (eff === 'deny' && isLeaf(n)) {
      push(`!${n.key}`)
      if (n.targetPerm) push(`!${n.targetPerm}`)
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
  const byEdit = new Map(nodes.map((n) => [n.editKey, n]))
  const parentOf = new Map<string, MenuNode>()
  for (const n of nodes) for (const c of n.children) parentOf.set(c.key, n)
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
  // "change" on a node that is allowed
  for (const p of perms) {
    const n = p.startsWith('!') ? undefined : byEdit.get(p)
    if (n && states[n.key] === 'allow') states[n.key] = 'change'
  }
  // "read only" inside a folder given in change: the node carries its own allow (written with it)
  const above = (n: MenuNode): NodeState | null => {
    for (let a = parentOf.get(n.key); a; a = parentOf.get(a.key)) if (states[a.key]) return states[a.key]
    return null
  }
  for (const p of perms) {
    const n = p.startsWith('!') ? byEdit.get(p.slice(1)) : undefined
    if (n && !states[n.key] && above(n) === 'change') states[n.key] = 'allow'
  }
  states = normalize(states, full, forest)
  const generated = new Set(generate({ full, states }, forest))
  // What the server now works out by itself: the target of an entry the menus allow. Older versions
  // wrote one per entry; kept as "other" rules they would only repeat what the entry already gives.
  const given = new Set<string>()
  const walk = (n: MenuNode, inherited: NodeState | null) => {
    const eff = states[n.key] ?? inherited
    if (allowed(eff) && isLeaf(n) && n.targetPerm) given.add(n.targetPerm)
    n.children.forEach((c) => walk(c, eff))
  }
  for (const roots of Object.values(forest)) roots.forEach((r) => walk(r, null))
  const other = perms.filter((p) => p !== '*' && p !== '!*' && !generated.has(p) && !byKey.has(strip(p)) && !given.has(p))
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
    const eff = own ?? inherited
    // an entry the menus allow is shown: the server gives it what it runs (unless a rule denies the target by name)
    const shown = eff === 'deny' ? false
      : allowed(eff) && !model.full ? !isDenied(perms, n.key) && !(n.targetPerm && isDenied(perms, n.targetPerm))
        : leafVisible(perms, n)
    out[n.key] = { own, inherited, visible: isLeaf(n) ? shown : null }
    n.children.forEach((c) => walk(c, own ?? inherited))
  }
  for (const roots of Object.values(forest)) roots.forEach((r) => walk(r, model.full ? 'allow' : null))
  return out
}
