// What the open tabs become when a page is opened — plain logic, tested.
//
// Two settings close tabs by themselves: single-tab mode (`[ui] tabs_multi = false`: the page opened replaces
// the one shown) and the cap (`tabs_max`: the oldest closes). Neither may take a page that holds unsaved
// edits with it — nobody asked the user. Such a tab stays (the tab strip marks it), for its owner to save
// or close; the cap closes the oldest tab that holds nothing instead.

export interface OpenRules {
  /** several tabs at once; false = the page opened replaces the others */
  multi: boolean
  /** how many tabs stay open (at least 1) */
  max: number
}

export function withOpened<T extends { id: string }>(tabs: T[], opened: T, rules: OpenRules, holdsEdits: (id: string) => boolean): T[] {
  if (tabs.some((x) => x.id === opened.id)) return tabs
  if (!rules.multi) return [...tabs.filter((x) => holdsEdits(x.id)), opened]
  const over = tabs.length - Math.max(1, rules.max) + 1
  if (over <= 0) return [...tabs, opened]
  // the oldest go first — but only those that hold nothing unsaved (the cap gives way to them)
  const going = new Set(tabs.filter((x) => !holdsEdits(x.id)).slice(0, over).map((x) => x.id))
  return [...tabs.filter((x) => !going.has(x.id)), opened]
}
