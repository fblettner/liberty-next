// A diagram laid out by the server (liberty/diagram.py — lay_out): groups (a tier, a host in a tier), cards and
// links, each with the place the server gave it. The page's painter (common/Diagram.tsx) and the document's
// (render_svg, in Python) draw the SAME places: nothing is placed here. This module holds the shape, a tolerant
// reading of what the server sent, and what the painter works out from it (what a card's links are, what the
// legend lists). No React here.

export type DiagramState = 'ok' | 'warn' | 'down' | 'unknown'
export const DIAGRAM_STATES: DiagramState[] = ['ok', 'warn', 'down', 'unknown']

export interface DiagramBadge { label: string; value: string }
export interface DiagramBox { x: number; y: number; w: number; h: number }
export interface PlacedGroup extends DiagramBox { id: string; label: string; kind: string; parent: string | null; declared: boolean }
export interface PlacedNode extends DiagramBox {
  id: string; group: string; kind: string; label: string; detail: string; state: DiagramState; declared: boolean; badges: DiagramBadge[]
}
/** a link once placed: its line and its arrowhead as SVG paths, and where its label sits */
export interface PlacedLink { from: string; to: string; label: string; evidence: string; path: string; arrow: string; x: number; y: number }
export interface PlacedDiagram {
  width: number; height: number
  groups: PlacedGroup[]; nodes: PlacedNode[]; links: PlacedLink[]
  /** the distinct evidences of the links, in the order met: a line's colour is its evidence's rank */
  evidences: string[]
  note: string
}

const text = (v: unknown): string => (typeof v === 'string' ? v : typeof v === 'number' ? String(v) : '')
const isRecord = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const list = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isRecord) : [])

function box(v: Record<string, unknown>): DiagramBox | null {
  const b = { x: Number(v.x), y: Number(v.y), w: Number(v.w), h: Number(v.h) }
  return [b.x, b.y, b.w, b.h].every(Number.isFinite) && b.w >= 0 && b.h >= 0 ? b : null
}

export function stateOf(v: unknown): DiagramState {
  return DIAGRAM_STATES.includes(v as DiagramState) ? (v as DiagramState) : 'unknown'
}

// a path as the server writes it (M L C Q Z and numbers): anything else is not drawn
const PATH = /^[MLCQZ0-9 .,-]*$/

/** What the server sent, as the painter needs it — or null when it is not a diagram. An entry without an id or
 *  without a place is left out (and a link to it with it): the rest is still drawn. */
export function readDiagram(raw: unknown): PlacedDiagram | null {
  if (!isRecord(raw) || !Array.isArray(raw.groups) || !Array.isArray(raw.nodes)) return null
  const groups: PlacedGroup[] = []
  for (const g of list(raw.groups)) {
    const b = box(g)
    if (!b || !text(g.id)) continue
    groups.push({ ...b, id: text(g.id), label: text(g.label), kind: text(g.kind) || 'tier', parent: text(g.parent) || null, declared: g.declared !== false })
  }
  const nodes: PlacedNode[] = []
  for (const n of list(raw.nodes)) {
    const b = box(n)
    if (!b || !text(n.id)) continue
    nodes.push({
      ...b, id: text(n.id), group: text(n.group), kind: text(n.kind), label: text(n.label), detail: text(n.detail), state: stateOf(n.state),
      declared: n.declared !== false, badges: list(n.badges).map((x) => ({ label: text(x.label), value: text(x.value) })).filter((x) => x.label || x.value),
    })
  }
  const known = new Set([...groups.map((g) => g.id), ...nodes.map((n) => n.id)])
  const links: PlacedLink[] = []
  for (const k of list(raw.links)) {
    const from = text(k.from), to = text(k.to), path = text(k.path), arrow = text(k.arrow)
    if (!known.has(from) || !known.has(to) || !path || !PATH.test(path) || !PATH.test(arrow)) continue
    links.push({ from, to, label: text(k.label), evidence: text(k.evidence), path, arrow, x: Number(k.x) || 0, y: Number(k.y) || 0 })
  }
  const said = Array.isArray(raw.evidences) ? raw.evidences.map(text).filter(Boolean) : []
  const evidences = [...new Set([...said, ...links.map((k) => k.evidence).filter(Boolean)])]
  const width = Number(raw.width), height = Number(raw.height)
  return {
    width: Number.isFinite(width) && width > 0 ? width : 0, height: Number.isFinite(height) && height > 0 ? height : 0,
    groups, nodes, links, evidences, note: text(raw.note),
  }
}

/** The boxes a card sits in, the nearest first: its host, then the host's tier. */
export function boxesOf(d: PlacedDiagram, node: PlacedNode): PlacedGroup[] {
  const out: PlacedGroup[] = []
  let at = d.groups.find((g) => g.id === node.group)
  while (at && !out.includes(at)) {
    out.push(at)
    const parent = at.parent
    at = parent ? d.groups.find((g) => g.id === parent) : undefined
  }
  return out
}

/** The links of a card: its own, and those drawn from or to a box it sits in (a host's sessions are its cards'). */
export function linksOf(d: PlacedDiagram, id: string): PlacedLink[] {
  const node = d.nodes.find((n) => n.id === id)
  const mine = new Set([id, ...(node ? boxesOf(d, node).map((g) => g.id) : [])])
  return d.links.filter((k) => mine.has(k.from) || mine.has(k.to))
}

/** What a link's end is called: the card's or the box's label (its id when it has none). */
export function endLabel(d: PlacedDiagram, id: string): string {
  return d.nodes.find((n) => n.id === id)?.label || d.groups.find((g) => g.id === id)?.label || id
}

/** The rank of an evidence among the diagram's (its line's colour); -1 for a link that gives none. */
export function evidenceRank(d: PlacedDiagram, evidence: string): number {
  return evidence ? d.evidences.indexOf(evidence) : -1
}

/** What a card says of its badges on one line: the first `limit`, "Sessions 184 · Port 1521" (as the document does). */
export function badgeLine(node: Pick<PlacedNode, 'badges'>, limit = 2): string {
  return node.badges.filter((b) => b.value).slice(0, limit).map((b) => `${b.label} ${b.value}`.trim()).join(' · ')
}

/** What the legend lists: the states that occur (in the order ok, warn, down, unknown), whether something is not
 *  declared, the evidences. */
export function legendOf(d: PlacedDiagram): { states: DiagramState[]; undeclared: boolean; evidences: string[] } {
  return {
    states: DIAGRAM_STATES.filter((s) => d.nodes.some((n) => n.state === s)),
    undeclared: d.nodes.some((n) => !n.declared) || d.groups.some((g) => !g.declared),
    evidences: d.evidences,
  }
}
