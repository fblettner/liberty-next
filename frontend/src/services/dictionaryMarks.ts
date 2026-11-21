// What a dictionary record's line says in the list, without opening it: the name it goes by, and the marks worth
// seeing — an entry's format and rule, how many values an enum holds, the other connector a lookup or a
// sequence reads from. A chip is one short word: what would be cut goes to its tooltip.
// No React here.

export type DictKind = 'entries' | 'enums' | 'lookups' | 'sequences' | 'framework_enums'
/** `text` is what the chip shows; `title` what it says in full when the chip shows less (a rule without its target). */
export interface RecordMark { tone: 'neutral' | 'purple' | 'green' | 'blue'; text: string; title?: string; mono?: boolean }
/** `more` is what the filter also reads though the line does not show it (the query a lookup reads). */
export interface RecordLine { sub: string; marks: RecordMark[]; more?: string[] }

const text = (v: unknown): string => (typeof v === 'string' ? v.trim() : '')

export function recordLine(kind: DictKind, rec: Record<string, unknown> | undefined, name = ''): RecordLine {
  const r = rec ?? {}
  const marks: RecordMark[] = []
  if (kind === 'entries') {
    if (text(r.format)) marks.push({ tone: 'neutral', text: text(r.format) })
    // the rule's name is enough on the line (its target would always be cut): the target is in the chip's tooltip
    if (text(r.rules)) {
      const rule = text(r.rules).toUpperCase()
      marks.push({ tone: 'purple', text: rule, ...(text(r.rules_values) ? { title: `${rule} ${text(r.rules_values)}` } : {}) })
    }
    return { sub: text(r.label), marks }
  }
  if (kind === 'enums' || kind === 'framework_enums') {
    if (Array.isArray(r.values)) marks.push({ tone: 'green', text: String(r.values.length) })
    return { sub: text(r.label), marks }
  }
  // lookups, sequences: the chip says the one thing worth a glance — the rows come from ANOTHER connector — and
  // names it; the query (often the record's own name, always too long for a chip) is in the tooltip and is
  // still what the filter reads
  const query = text(r.query)
  const connector = text(r.connector)
  if (connector) marks.push({ tone: 'blue', text: connector, ...(query ? { title: `${connector}.${query}` } : {}), mono: true })
  if (kind === 'lookups' && Array.isArray(r.union) && r.union.length) marks.push({ tone: 'neutral', text: `+${r.union.length}` })
  return { sub: text(r.description), marks, ...(query && query !== name ? { more: [query] } : {}) }
}

/** A record matches what is typed when its name, its label or one of its marks holds it. */
export function recordMatches(needle: string, name: string, line: RecordLine): boolean {
  const n = needle.trim().toLowerCase()
  if (!n) return true
  return [name, line.sub, ...line.marks.map((m) => m.title ?? m.text), ...(line.more ?? [])].some((s) => s.toLowerCase().includes(n))
}
