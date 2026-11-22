// A free "name = value" setting (a menu entry's fixed values, a step's parameters): edited as
// text rows, saved without changing the kind of a value that was not touched — a number stays a
// number, yes / no stays yes / no. No React here.

/** The map as the rows show it: every value as text. */
export function freeMapText(map: Record<string, unknown>): Record<string, string> {
  return Object.fromEntries(Object.entries(map).map(([k, v]) => [k, v == null ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)]))
}

/** What is saved after an edit of the rows: a value whose text did not change keeps its original
 *  kind (whatever its row's name became); a typed one is text. Rows without a name are kept while
 *  they are being typed (the builder's save drops what is blank). */
export function mergeFreeMap(original: Record<string, unknown>, edited: Record<string, string>): Record<string, unknown> {
  const shown = freeMapText(original)
  const out: Record<string, unknown> = {}
  const before = Object.keys(original)
  Object.entries(edited).forEach(([name, text], index) => {
    // same name, same text → untouched; a renamed row is found by its place
    const from = name in original && shown[name] === text ? name
      : before[index] !== undefined && !(before[index] in edited) && shown[before[index]] === text ? before[index]
      : null
    out[name] = from !== null ? original[from] : text
  })
  return out
}

/** The names a menu entry may give a fixed value to: the filters of the screen it opens, or the
 *  declared parameters of the query it opens. */
export function targetParamNames(
  item: { type?: string | null; target?: string | null } | null | undefined,
  screen: { columns?: { name?: unknown; filter?: unknown }[] } | null | undefined,
  connector: { tables?: unknown; queries?: unknown } | null | undefined,
): string[] {
  if (!item?.target) return []
  const names: string[] = []
  const add = (n: unknown) => { if (typeof n === 'string' && n && !names.includes(n)) names.push(n) }
  if (item.type === 'screen') {
    for (const c of screen?.columns ?? []) if (c.filter) add(c.name)
  } else if (item.type === 'query') {
    const paramsOf = (holder: unknown) => {
      const params = (holder as { params?: unknown } | null | undefined)?.params
      if (Array.isArray(params)) for (const p of params) add((p as { name?: unknown })?.name)
    }
    for (const tb of Array.isArray(connector?.tables) ? (connector.tables as { name?: unknown; get?: unknown }[]) : []) {
      if (`${String(tb.name)}_get` === item.target) paramsOf(tb.get)
    }
    for (const q of Array.isArray(connector?.queries) ? (connector.queries as { name?: unknown }[]) : []) {
      if (q.name === item.target) paramsOf(q)
    }
  }
  return names
}
