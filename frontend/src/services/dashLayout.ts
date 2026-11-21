// A dashboard's cards on its 12-column grid: the order they are stored in is the order they flow in, each
// with a width in columns and a height in rows. Moving a card by drag and drop and sizing it by its corner
// come down to the two functions here. No React.

export const GRID_COLUMNS = 12
export const MAX_ROWS = 8

/** The cards with the one at `from` dropped on the one at `over`: before it, or after it. */
export function dropped<T>(cards: T[], from: number, over: number, after: boolean): T[] {
  if (from < 0 || from >= cards.length || over < 0 || over >= cards.length) return cards
  let to = after ? over + 1 : over
  if (from < to) to -= 1          // taking the card out shifts what follows it
  if (to === from) return cards
  const next = cards.slice()
  const [card] = next.splice(from, 1)
  next.splice(to, 0, card)
  return next
}

/** Where a dragged card would land, as "before" / "after" the card under the pointer: the half of it the pointer
 *  is on. Null when it would not move (dropped on itself, or right where it already is). */
export function dropSide(from: number, over: number, pointerX: number, left: number, width: number): 'before' | 'after' | null {
  if (from === over) return null
  const after = pointerX > left + width / 2
  if ((after && over + 1 === from) || (!after && over - 1 === from)) return null
  return after ? 'after' : 'before'
}

const clamp = (n: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, n))

/** A card's size after its corner was dragged by (dx, dy) pixels: whole columns and rows, never less than one,
 *  never wider than the grid. `colPx` / `rowPx` are one column's and one row's pitch (the gap included);
 *  `maxCols` / `maxRows` for a grid that is not the dashboards' 12 × 8 (the Explorer's tiles: 4 columns). */
export function resized(colSpan: number, rowSpan: number, dx: number, dy: number, colPx: number, rowPx: number, maxCols = GRID_COLUMNS, maxRows = MAX_ROWS): { col_span: number; row_span: number } {
  return {
    col_span: clamp(colSpan + Math.round(dx / Math.max(1, colPx)), 1, maxCols),
    row_span: clamp(rowSpan + Math.round(dy / Math.max(1, rowPx)), 1, maxRows),
  }
}

/** The same by steps (the keyboard: Shift + an arrow). */
export function stepped(colSpan: number, rowSpan: number, dCol: number, dRow: number, maxCols = GRID_COLUMNS, maxRows = MAX_ROWS): { col_span: number; row_span: number } {
  return { col_span: clamp(colSpan + dCol, 1, maxCols), row_span: clamp(rowSpan + dRow, 1, maxRows) }
}

/** For a card whose height is one of a few named sizes (small / medium / large): the name whose height is the
 *  closest to `px`. `sizes` is name → pixels. */
export function nearestSize<K extends string>(sizes: Record<K, number>, px: number): K {
  const names = Object.keys(sizes) as K[]
  return names.reduce((best, k) => (Math.abs(sizes[k] - px) < Math.abs(sizes[best] - px) ? k : best), names[0])
}

/** The next named size up (`step` 1) or down (-1), in the order of their heights; the ends stay where they are. */
export function steppedSize<K extends string>(sizes: Record<K, number>, current: K, step: number): K {
  const names = (Object.keys(sizes) as K[]).sort((a, b) => sizes[a] - sizes[b])
  return names[clamp(names.indexOf(current) + step, 0, names.length - 1)]
}
