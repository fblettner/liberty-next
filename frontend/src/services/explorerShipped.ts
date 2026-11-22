// What an Explorer model delivers with itself: the analyses and dashboards the application ships
// (explorer.toml ``analyses`` / ``dashboards``), installed for everyone at the next start and
// refreshed when their version grows. The builder fills them from a SAVED analysis or dashboard —
// nobody writes a definition by hand. No React here.

/** One delivered analysis or dashboard, as explorer.toml holds it. */
export interface ShippedObject {
  label?: string | null
  description?: string | null
  audience?: string | null
  version?: number
  definition?: Record<string, unknown>
}

/** A saved analysis or dashboard, as far as delivering it needs. */
export interface SavedForShipping {
  id: string
  name: string
  description?: string | null
  audience?: string | null
  definition: unknown
}

const ID = /^[A-Za-z0-9_-]+$/

/** The id a saved object is delivered under: its own when the file can carry it, else one made
 *  from its name; never one already taken by another delivered object. */
export function shippedId(item: SavedForShipping, taken: string[]): string {
  if (ID.test(item.id)) return item.id
  const base = item.name.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'item'
  let id = base
  for (let n = 2; taken.includes(id); n++) id = `${base}_${n}`
  return id
}

/** The delivered objects after *item* is delivered under *id*: added at version 1, or — when it
 *  is delivered already — replaced with its version one higher, so that the installed copies
 *  nobody changed are refreshed. */
export function deliver(shipped: Record<string, ShippedObject> | undefined, id: string, item: SavedForShipping): Record<string, ShippedObject> {
  const before = shipped?.[id]
  const next: ShippedObject = { label: item.name, version: before ? (before.version ?? 1) + 1 : 1, definition: (item.definition ?? {}) as Record<string, unknown> }
  if (item.description) next.description = item.description
  if (item.audience) next.audience = item.audience
  return { ...(shipped ?? {}), [id]: next }
}

/** The delivered objects without *id*; undefined when none is left (the key leaves the file). */
export function withdraw(shipped: Record<string, ShippedObject> | undefined, id: string): Record<string, ShippedObject> | undefined {
  const next = { ...(shipped ?? {}) }
  delete next[id]
  return Object.keys(next).length ? next : undefined
}

/** The analyses a delivered dashboard shows that are not delivered with it: it would open empty. */
export function missingAnalyses(dashboard: ShippedObject, analyses: Record<string, ShippedObject> | undefined): string[] {
  const tiles = (dashboard.definition as { tiles?: { analysis?: unknown }[] } | undefined)?.tiles ?? []
  const wanted = [...new Set(tiles.map((x) => x.analysis).filter((x): x is string => typeof x === 'string' && x !== ''))]
  return wanted.filter((id) => !(analyses && id in analyses))
}
