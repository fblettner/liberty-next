// The studio's map of an application — its shapes (GET /admin/studio/map) and what is linked to what.
// No React here: the page draws, this says which items a selection touches.

export interface MapMenuItem { id: string; label: string; depth: number; type: string | null; target: string | null; connector: string | null }
export interface MapScreen {
  id: string; label: string; connector: string; read: string | null; writes: string[]; calls: string[]; services: string[]; nested: string[]
  form: boolean; read_only: boolean; in_menu: boolean; reached: boolean
}
export interface MapData { key: string; connector: string; name: string; kind: 'query' | 'endpoint'; used: boolean; label: string | null; writable: boolean; service: string | null; missing: boolean }
export interface MapService { id: string; label: string | null; steps: number; screens: string[]; endpoints: { connector: string; endpoint: string }[]; missing: boolean }
export interface AppMap {
  apps: string[]; app: string | null; menu: MapMenuItem[]; screens: MapScreen[]; data: MapData[]; services: MapService[]
  dashboards: { id: string; label: string; in_menu: boolean }[]
}

/** A node of the map: `m:` a menu entry, `s:` a screen, `d:` a query or an endpoint, `v:` a service. */
export type NodeKey = string
export const menuKey = (id: string): NodeKey => `m:${id}`
export const screenKey = (id: string): NodeKey => `s:${id}`
export const dataKey = (key: string): NodeKey => `d:${key}`
export const serviceKey = (id: string): NodeKey => `v:${id}`

/** Every link of the map, as [from, to] in reading order (menu → screen → data → service; a screen → the screens inside its form). */
export function mapLinks(map: AppMap): [NodeKey, NodeKey][] {
  const out: [NodeKey, NodeKey][] = []
  const screens = new Set(map.screens.map((s) => s.id))
  const services = new Set(map.services.map((v) => v.id))
  for (const m of map.menu) if (m.type === 'screen' && m.target && screens.has(m.target)) out.push([menuKey(m.id), screenKey(m.target)])
  for (const s of map.screens) {
    for (const key of new Set([...(s.read ? [s.read] : []), ...s.writes, ...s.calls])) out.push([screenKey(s.id), dataKey(key)])
    for (const ref of s.services) out.push([screenKey(s.id), serviceKey(ref)])
    for (const n of s.nested) if (screens.has(n)) out.push([screenKey(s.id), screenKey(n)])
  }
  for (const d of map.data) if (d.service && services.has(d.service)) out.push([dataKey(d.key), serviceKey(d.service)])
  return out
}

/** What a selection reaches: the items and the links to light up. */
export interface Reach { nodes: Set<NodeKey>; links: [NodeKey, NodeKey][] }

/** Everything *key* leads to and everything that leads to it, following the links all the way: downstream
 *  (a menu entry → its screen → the data and services it uses → the screens inside its form…) and upstream
 *  (a query → the screens that use it → their menu entries). Never up then down again — that would light up
 *  a screen's siblings. Nothing selected → null (everything shows). */
export function reachOf(links: [NodeKey, NodeKey][], key: NodeKey | null): Reach | null {
  if (!key) return null
  const nodes = new Set<NodeKey>([key])
  const used = new Set<number>()
  const follow = (from: 0 | 1) => {
    const to = from === 0 ? 1 : 0
    const seen = new Set<NodeKey>([key])
    const queue = [key]
    while (queue.length) {
      const at = queue.shift()!
      links.forEach((link, i) => {
        if (link[from] !== at) return
        used.add(i)
        nodes.add(link[to])
        if (!seen.has(link[to])) { seen.add(link[to]); queue.push(link[to]) }
      })
    }
  }
  follow(0); follow(1)
  return { nodes, links: [...used].sort((a, b) => a - b).map((i) => links[i]) }
}

/** How a screen uses a piece of data: it reads it, saves through it, or an action of the screen calls it. */
export function usageOf(screen: MapScreen, key: string): 'reads' | 'saves' | 'calls' | null {
  if (screen.read === key) return 'reads'
  if (screen.writes.includes(key)) return 'saves'
  return screen.calls.includes(key) ? 'calls' : null
}

/** Does *text* (a filter typed by the user) match one of the *fields*? Blank matches all. */
export function matches(text: string, ...fields: (string | null | undefined)[]): boolean {
  const q = text.trim().toLowerCase()
  return !q || fields.some((f) => (f ?? '').toLowerCase().includes(q))
}
