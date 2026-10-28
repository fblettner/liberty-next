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
