// Ordering helpers for the option lists the dropdowns show. `sortOptions` puts a catalogue
// (connectors, queries, screens, models, presets, roles…) in alphabetical order of what each line
// STARTS with: its code (`mono`) when it shows one, else its label. A line "code  description" is
// looked up by its code; ordered on the description the codes came out shuffled (a description is
// found by typing in the search box, which matches both) —
// locale-aware, digits compared as numbers (`F0101` before `F0111`, `item 2` before `item 10`),
// case ignored — keeping an "empty" option (value '') first since it is the "none" row. Stable:
// options with the same label keep their relative order. Enum-like lists whose order carries a
// meaning (statuses, steps, join types) are not sorted by their callers.
export interface Orderable { value: string; label: string; mono?: string }

/** What a line is ordered on: its code when it shows one, else its label. */
const orderKey = (o: Orderable): string => (o.mono && o.mono.trim() ? o.mono : o.label)

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

export function sortOptions<T extends Orderable>(options: T[]): T[] {
  return options
    .map((o, i) => ({ o, i }))
    .sort((a, b) => {
      if (a.o.value === '' || b.o.value === '') return a.o.value === '' ? (b.o.value === '' ? a.i - b.i : -1) : 1
      return collator.compare(orderKey(a.o), orderKey(b.o)) || a.i - b.i
    })
    .map((x) => x.o)
}

/** Options in their display order: ungrouped first, then each ``group`` in the order met
 *  (alphabetical with *sort*), each group's options sorted with *sort*. */
export function orderOptions<T extends Orderable & { group?: string }>(options: T[], sort = false): T[] {
  const base = sort ? sortOptions(options) : options
  if (!base.some((o) => o.group)) return base
  const order: string[] = []
  for (const o of base) if (o.group && !order.includes(o.group)) order.push(o.group)
  if (sort) order.sort((a, b) => collator.compare(a, b))
  return [...base.filter((o) => !o.group), ...order.flatMap((g) => base.filter((o) => o.group === g))]
}

// ── narrowing a list: the search box and the filter chips, shared by the dropdown and its table dialog ──

/** What a list is narrowed on: its text, its code and its extra columns. */
export interface Searchable { value: string; label: string; mono?: string; cells?: string[] }

/** The columns of a list that are filtered by chips (a LOOKUP's filter fields), each with its distinct values. */
export function facetColumns<T extends Searchable>(
  cellColumns: { label: string; filterable?: boolean }[] | undefined, options: T[],
): { index: number; label: string; values: string[] }[] {
  if (!cellColumns) return []
  return cellColumns
    .map((c, index) => ({ index, label: c.label, filterable: !!c.filterable }))
    .filter((c) => c.filterable)
    .map((c) => ({ index: c.index, label: c.label, values: Array.from(new Set(options.map((o) => (o.cells?.[c.index] ?? '').trim()).filter(Boolean))).sort() }))
    .filter((c) => c.values.length > 0)
}

/** The options the text typed and the chips picked leave: for every column with a chip the cell equals it, and
 *  the text is found in the label, the value, the code or a cell (case ignored). */
export function filterOptions<T extends Searchable>(options: T[], text: string, facets: Record<number, string> = {}): T[] {
  const needle = text.trim().toLowerCase()
  const active = Object.entries(facets).filter(([, v]) => v)
  if (!needle && !active.length) return options
  return options.filter((o) => {
    for (const [index, wanted] of active) if ((o.cells?.[Number(index)] ?? '').trim() !== wanted) return false
    if (!needle) return true
    return o.label.toLowerCase().includes(needle) || o.value.toLowerCase().includes(needle)
      || (o.mono ?? '').toLowerCase().includes(needle) || (o.cells ?? []).some((c) => c.toLowerCase().includes(needle))
  })
}

/** From how many lines a list is also offered as a table in a dialog (a list with extra columns always is). */
export const BROWSE_FROM = 40
export function browsable(options: Searchable[]): boolean {
  return options.length >= BROWSE_FROM || options.some((o) => (o.cells?.length ?? 0) > 0)
}

/** A list as the "Open as a table" dialog draws it with the shared grid: one row per option (``value`` kept to pick
 *  it), and its columns — the code when the lines show one, the label, each extra cell under its heading. A cell
 *  column the dropdown offers chips for (``filterable``) is filtered by picking among its values (``choices``). */
export interface LookupTableColumn { key: string; label: string; mono?: boolean; choices?: string[] }
export function lookupTable(options: Searchable[], cellColumns: { label: string; filterable?: boolean }[] | undefined, words: { code: string; label: string }):
  { columns: LookupTableColumn[]; rows: Record<string, string>[] } {
  const coded = options.some((o) => o.mono && o.mono !== o.label)
  const extra = options.reduce((n, o) => Math.max(n, o.cells?.length ?? 0), 0)
  const columns: LookupTableColumn[] = [
    ...(coded ? [{ key: 'code', label: words.code, mono: true }] : []),
    { key: 'label', label: words.label },
    ...Array.from({ length: extra }, (_x, i): LookupTableColumn => {
      const filterable = !!cellColumns?.[i]?.filterable
      const values = filterable ? [...new Set(options.map((o) => (o.cells?.[i] ?? '').trim()).filter(Boolean))].sort((a, b) => collator.compare(a, b)) : undefined
      return { key: `c${i}`, label: cellColumns?.[i]?.label ?? '', ...(values ? { choices: values } : {}) }
    }),
  ]
  const rows = options.map((o) => ({
    value: o.value, ...(coded ? { code: o.mono ?? '' } : {}), label: o.label,
    ...Object.fromEntries(Array.from({ length: extra }, (_x, i) => [`c${i}`, o.cells?.[i] ?? ''])),
  }))
  return { columns, rows }
}
