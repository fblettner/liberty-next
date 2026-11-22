// What is checked in the menus before they are sent: the cheap things the server would refuse
// anyway — an entry that opens nothing, two entries with the same id, a folder that is gone —
// found at once and placed on the entry. Mirrors liberty/menus/config.py (`MenuItem._check`,
// `AppMenu._check`). No React here.

export interface CheckedMenuItem {
  id: string
  label?: string
  parent?: string | null
  type?: string | null
  target?: string | null
}

export type MenuIssueCode =
  | 'target'       // an entry that is not a folder opens nothing
  | 'duplicate'    // two entries of an application have the same id
  | 'parent'       // an entry sits under a folder that does not exist

export interface MenuIssue {
  code: MenuIssueCode
  app: string
  /** The entry's place in its application's list. */
  index: number
  /** The id in question (the duplicated id, the missing folder). */
  name: string
}

/** The menus as they are sent: a fixed value left without a name is not one (the row was added
 *  and not filled in), and an entry left with none carries no ``params`` at all. */
export function cleanMenus<T extends { items?: (CheckedMenuItem & { params?: Record<string, unknown> })[] }>(apps: Record<string, T>): Record<string, T> {
  const out: Record<string, T> = {}
  for (const [app, menu] of Object.entries(apps)) {
    out[app] = {
      ...menu,
      items: (menu.items ?? []).map((it) => {
        if (!it.params) return it
        const kept = Object.fromEntries(Object.entries(it.params).filter(([name]) => name.trim() !== ''))
        const { params: _dropped, ...rest } = it
        return Object.keys(kept).length ? { ...rest, params: kept } : rest
      }),
    }
  }
  return out
}

/** Everything to correct in the menus before they are saved. */
export function checkMenus(apps: Record<string, { items?: CheckedMenuItem[] }>): MenuIssue[] {
  const issues: MenuIssue[] = []
  for (const [app, menu] of Object.entries(apps)) {
    const items = menu.items ?? []
    const ids = new Set(items.map((it) => it.id))
    const seen = new Set<string>()
    items.forEach((it, index) => {
      if (seen.has(it.id)) issues.push({ code: 'duplicate', app, index, name: it.id })
      seen.add(it.id)
      if (it.type && !(it.target ?? '').trim()) issues.push({ code: 'target', app, index, name: it.id })
      if (it.parent && !ids.has(it.parent)) issues.push({ code: 'parent', app, index, name: it.parent })
    })
  }
  return issues
}
