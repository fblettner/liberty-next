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

/** What *key* is linked to, directly — itself included. Nothing selected → null (everything shows). */
export function relatedTo(links: [NodeKey, NodeKey][], key: NodeKey | null): Set<NodeKey> | null {
  if (!key) return null
  const out = new Set<NodeKey>([key])
  for (const [a, b] of links) { if (a === key) out.add(b); if (b === key) out.add(a) }
  return out
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
