// A text given in several languages, the way the dashboards keep it: beside a record's own
// texts (`label`, `description`, …) an `l` table holds, per language, the same fields said in
// that language — `l.fr = { label = "Ventes", description = "…" }`. The server shows the
// request's language when it is there, the record's own text otherwise.
//
// No React here: the editors' "in the other languages" block (common/LocalizedTexts) reads and
// writes through these.

export type Localized = Record<string, Record<string, unknown>>

/** The languages a text may be given in besides the one the configuration is written in. */
export function otherLanguages(all: readonly string[], written: string): string[] {
  const base = written.toLowerCase().split('-')[0]
  return all.filter((l, i) => l.toLowerCase() !== base && all.indexOf(l) === i)
}

/** What *field* says in *language* — '' when nothing is given. */
export function localizedValue(l: Localized | null | undefined, language: string, field: string): string {
  const v = l?.[language]?.[field]
  return typeof v === 'string' ? v : ''
}

/** *l* with *field* said *value* in *language*. A blank value takes the field out, a language
 *  left with nothing is taken out too; what the caller does not name is kept as it is. */
export function withLocalized(l: Localized | null | undefined, language: string, field: string, value: string): Localized {
  const next: Localized = {}
  for (const [lang, texts] of Object.entries(l ?? {})) next[lang] = { ...texts }
  const texts = { ...(next[language] ?? {}) }
  if (value.trim()) texts[field] = value
  else delete texts[field]
  if (Object.keys(texts).length) next[language] = texts
  else delete next[language]
  return next
}

/** *l* ready to be saved: undefined when it holds nothing (the key is then left out). */
export function prunedLocalized(l: Localized | null | undefined): Localized | undefined {
  const next: Localized = {}
  for (const [lang, texts] of Object.entries(l ?? {})) {
    const kept = Object.fromEntries(Object.entries(texts ?? {}).filter(([, v]) => !(typeof v === 'string' && !v.trim()) && v != null))
    if (Object.keys(kept).length) next[lang] = kept
  }
  return Object.keys(next).length ? next : undefined
}

/** How many of *fields* are said in another language — a block's one-line summary. */
export function localizedCount(l: Localized | null | undefined, fields: readonly string[]): number {
  let n = 0
  for (const texts of Object.values(l ?? {})) for (const f of fields) if (typeof texts?.[f] === 'string' && (texts[f] as string).trim()) n += 1
  return n
}
