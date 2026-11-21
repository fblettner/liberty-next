// A "pairs" value: `name = value`, one per line — the text a column of format `pairs` holds (the parameters of a
// call, values to check…). The first `=` separates, so a value may hold some. No React here.

export interface Pair { name: string; value: string }

/** The pairs of *text* — or null when a line is not `name = value` (the text is then edited as it is). */
export function parsePairs(text: string | null | undefined): Pair[] | null {
  const out: Pair[] = []
  for (const raw of String(text ?? '').split(/\r?\n/)) {
    const line = raw.trim()
    if (!line) continue
    const at = line.indexOf('=')
    if (at <= 0) return null
    out.push({ name: line.slice(0, at).trim(), value: line.slice(at + 1).trim() })
  }
  return out
}

/** The text of *pairs*: one line each, a pair without a name left out. */
export function formatPairs(pairs: Pair[]): string {
  return pairs.filter((p) => p.name.trim()).map((p) => `${p.name.trim()} = ${p.value.trim()}`).join('\n')
}

/** *pairs* with *name* set to *value* — removed when the value is blank, added last when new. Names match
 *  without regard to case (a parameter typed `an8` fills `AN8`). */
export function setPair(pairs: Pair[], name: string, value: string): Pair[] {
  const low = name.toLowerCase()
  const at = pairs.findIndex((p) => p.name.toLowerCase() === low)
  if (value.trim() === '') return pairs.filter((_p, i) => i !== at)
  if (at < 0) return [...pairs, { name, value }]
  return pairs.map((p, i) => (i === at ? { name: p.name, value } : p))
}

/** The value of *name* in *pairs* ('' when absent), without regard to case. */
export function pairValue(pairs: Pair[], name: string): string {
  const low = name.toLowerCase()
  return pairs.find((p) => p.name.toLowerCase() === low)?.value ?? ''
}
