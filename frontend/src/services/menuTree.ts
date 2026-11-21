// A menu as a tree kept in a flat list: each item names its parent, the list's order is the order shown.
// Dragging an item onto another puts it before it, after it, or inside it (a folder) — with everything under
// it. No React here.

export interface TreeItem { id: string; parent?: string | null; type?: string | null }
export type DropWhere = 'before' | 'after' | 'into'

/** The item and everything under it, in the list's order. */
export function subtree<T extends TreeItem>(items: T[], rootId: string): T[] {
  const inside = new Set<string>([rootId])
  let grew = true
  while (grew) {
    grew = false
    for (const it of items) if (it.parent && inside.has(it.parent) && !inside.has(it.id)) { inside.add(it.id); grew = true }
  }
  return items.filter((it) => inside.has(it.id))
}

/** Where the pointer says the dragged item goes, from its height over the row: the top quarter = before, the
 *  bottom quarter = after, the middle = into (a folder, or any item when `intoAny`); a row that takes nothing
 *  inside splits in two halves. */
export function dropZone(y: number, top: number, height: number, takesChildren: boolean): DropWhere {
  const at = (y - top) / Math.max(1, height)
  if (!takesChildren) return at < 0.5 ? 'before' : 'after'
  return at < 0.25 ? 'before' : at > 0.75 ? 'after' : 'into'
}

/** The list with `id` (and what is under it) dropped before / after / into `targetId`. The same list when the
 *  drop means nothing: onto itself, into its own subtree, or where it already is. */
export function dropInTree<T extends TreeItem>(items: T[], id: string, targetId: string, where: DropWhere): T[] {
  if (id === targetId) return items
  const block = subtree(items, id)
  const moving = new Set(block.map((b) => b.id))
  const target = items.find((it) => it.id === targetId)
  if (!block.length || !target || moving.has(targetId)) return items
  const parent = where === 'into' ? targetId : (target.parent ?? null)
  const head = { ...block[0], parent: parent ?? undefined } as T
  if (parent == null) delete (head as TreeItem).parent
  const moved = [head, ...block.slice(1)]
  const rest = items.filter((it) => !moving.has(it.id))
  let at: number
  if (where === 'before') at = rest.findIndex((it) => it.id === targetId)
  else {
    // after the target — or, into it, after the last thing it already holds
    const under = subtree(rest, targetId)
    at = rest.findIndex((it) => it.id === under[under.length - 1].id) + 1
  }
  const next = [...rest.slice(0, at), ...moved, ...rest.slice(at)]
  const same = next.length === items.length && next.every((it, i) => it.id === items[i].id && (it.parent ?? null) === (items[i].parent ?? null))
  return same ? items : next
}

/** What a menu item opens, for its line: a folder when it has no type. */
export function itemKind(it: TreeItem): string {
  return it.type || 'folder'
}
