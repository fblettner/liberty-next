// Ordering helpers for the option lists the dropdowns show. `sortOptions` puts a catalogue
// (connectors, queries, screens, models, presets, roles…) in alphabetical order of its label —
// locale-aware, digits compared as numbers (`F0101` before `F0111`, `item 2` before `item 10`),
// case ignored — keeping an "empty" option (value '') first since it is the "none" row. Stable:
// options with the same label keep their relative order. Enum-like lists whose order carries a
// meaning (statuses, steps, join types) are not sorted by their callers.
export interface Orderable { value: string; label: string }

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

export function sortOptions<T extends Orderable>(options: T[]): T[] {
  return options
    .map((o, i) => ({ o, i }))
    .sort((a, b) => {
      if (a.o.value === '' || b.o.value === '') return a.o.value === '' ? (b.o.value === '' ? a.i - b.i : -1) : 1
      return collator.compare(a.o.label, b.o.label) || a.i - b.i
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
