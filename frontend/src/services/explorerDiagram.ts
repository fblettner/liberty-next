// An Explorer model as a diagram: the hub in the middle, the entities it reaches placed by how far they are
// from it — the "many" side (the details: several rows per hub row) to its left, the "one" side (what a hub
// row has one of: its dimensions) to its right — and what does not reach the hub set apart, since an analysis
// cannot use it. The layout is computed from the model: nothing is placed by hand. No React here.

export interface DiagramEntity { cardinality?: 'one' | 'many'; label?: string }
export interface DiagramRelation { from: string; to: string; on?: string[][] }
export interface DiagramModel { hub: string; entities: Record<string, DiagramEntity>; relations?: DiagramRelation[] }

export type NodeRole = 'hub' | 'one' | 'many' | 'loose'
export interface DiagramNode { id: string; role: NodeRole; distance: number; column: number; row: number }
export interface DiagramEdge { index: number; from: string; to: string; pairs: string[][]; complete: boolean; known: boolean }
export interface Diagram {
  nodes: DiagramNode[]
  edges: DiagramEdge[]
  /** how many rows each column holds (the loose ones are not in a column) */
  columns: number[]
  loose: string[]
}

const filled = (on: string[][] | undefined): string[][] => (on ?? []).filter((p) => p.length === 2 && p[0] && p[1])

export function modelDiagram(model: DiagramModel): Diagram {
  const ids = Object.keys(model.entities)
  const edges: DiagramEdge[] = (model.relations ?? []).map((r, index) => {
    const pairs = filled(r.on)
    return { index, from: r.from, to: r.to, pairs, complete: pairs.length > 0, known: r.from in model.entities && r.to in model.entities }
  })
  // how far each entity is from the hub, following the relations either way
  const next = new Map<string, string[]>()
  for (const e of edges) {
    if (!e.known) continue
    next.set(e.from, [...(next.get(e.from) ?? []), e.to])
    next.set(e.to, [...(next.get(e.to) ?? []), e.from])
  }
  const distance = new Map<string, number>()
  // the side an entity is on is the side of the branch it hangs from: a dimension of a detail stays with the detail
  const side = new Map<string, 1 | -1>()
  if (model.hub in model.entities) {
    distance.set(model.hub, 0)
    const queue = [model.hub]
    while (queue.length) {
      const at = queue.shift() as string
      for (const to of next.get(at) ?? []) {
        if (distance.has(to)) continue
        distance.set(to, (distance.get(at) as number) + 1)
        side.set(to, at === model.hub ? (model.entities[to].cardinality === 'many' ? -1 : 1) : (side.get(at) as 1 | -1))
        queue.push(to)
      }
    }
  }
  const placed = ids.filter((id) => distance.has(id))
  const loose = ids.filter((id) => !distance.has(id))
  const signed = (id: string): number => (distance.get(id) as number) * (side.get(id) ?? 1)
  const lowest = Math.min(0, ...placed.map(signed))
  const rows = new Map<number, number>()
  const nodes: DiagramNode[] = placed.map((id) => {
    const column = signed(id) - lowest
    const row = rows.get(column) ?? 0
    rows.set(column, row + 1)
    const role: NodeRole = id === model.hub ? 'hub' : model.entities[id].cardinality === 'many' ? 'many' : 'one'
    return { id, role, distance: distance.get(id) as number, column, row }
  })
  const width = placed.length ? Math.max(...nodes.map((n) => n.column)) + 1 : 0
  const columns = Array.from({ length: width }, (_, c) => rows.get(c) ?? 0)
  loose.forEach((id, i) => nodes.push({ id, role: 'loose', distance: -1, column: i, row: 0 }))
  return { nodes, edges, columns, loose }
}

export interface Box { x: number; y: number; w: number; h: number }
export interface Placed { boxes: Record<string, Box>; width: number; height: number; looseTop: number | null }

/** Where each card goes: a column per distance, each column centred on the tallest, the loose ones in a row below. */
export function place(d: Diagram, card: { w: number; h: number; gapX: number; gapY: number }): Placed {
  const tallest = Math.max(1, ...d.columns)
  const colHeight = (n: number): number => n * card.h + Math.max(0, n - 1) * card.gapY
  const full = colHeight(tallest)
  const boxes: Record<string, Box> = {}
  for (const n of d.nodes) {
    if (n.role === 'loose') continue
    const top = (full - colHeight(d.columns[n.column])) / 2
    boxes[n.id] = { x: n.column * (card.w + card.gapX), y: top + n.row * (card.h + card.gapY), w: card.w, h: card.h }
  }
  const placedWidth = d.columns.length ? d.columns.length * card.w + (d.columns.length - 1) * card.gapX : 0
  let looseTop: number | null = null
  let height = d.columns.length ? full : 0
  if (d.loose.length) {
    looseTop = height ? height + card.gapY * 2 + 22 : 22          // room for the row's heading
    d.loose.forEach((id, i) => { boxes[id] = { x: i * (card.w + card.gapY), y: looseTop as number, w: card.w, h: card.h } })
    height = looseTop + card.h
  }
  const looseWidth = d.loose.length ? d.loose.length * card.w + (d.loose.length - 1) * card.gapY : 0
  return { boxes, width: Math.max(placedWidth, looseWidth), height, looseTop }
}

/** The line of a relation between two cards: from the side of one facing the other, as an SVG path, and its middle. */
export function link(a: Box, b: Box): { path: string; mid: { x: number; y: number } } {
  const leftToRight = a.x + a.w <= b.x, rightToLeft = b.x + b.w <= a.x
  if (leftToRight || rightToLeft) {
    const x1 = leftToRight ? a.x + a.w : a.x, x2 = leftToRight ? b.x : b.x + b.w
    const y1 = a.y + a.h / 2, y2 = b.y + b.h / 2, bend = (x2 - x1) / 2
    return { path: `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`, mid: { x: (x1 + x2) / 2, y: (y1 + y2) / 2 } }
  }
  // the same column (or overlapping ones): a bow on the right side
  const x = Math.max(a.x + a.w, b.x + b.w), y1 = a.y + a.h / 2, y2 = b.y + b.h / 2, out = 46
  return { path: `M ${x} ${y1} C ${x + out} ${y1}, ${x + out} ${y2}, ${x} ${y2}`, mid: { x: x + out * 0.75, y: (y1 + y2) / 2 } }
}

/** What a relation says on its line: its columns when there is one pair, how many otherwise. */
export function edgeLabel(e: DiagramEdge): string {
  if (!e.complete) return '?'
  if (e.pairs.length === 1) return e.pairs[0][0] === e.pairs[0][1] ? e.pairs[0][0] : `${e.pairs[0][0]} = ${e.pairs[0][1]}`
  return `${e.pairs.length} ×`
}
