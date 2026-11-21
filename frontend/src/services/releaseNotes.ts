// The release notes as things to read: versions (the "## <version> — <date>" headings of the bundled
// changelog), and inside a version its ITEMS — one per top-level bullet, with the bold sentence it opens on
// as its title. So a page can show one version at a time, an item as a card, and find an item by a word
// across every version. No React here.

export interface NoteSection { version: string; date: string; anchor: string; md: string }
export interface NoteItem { title: string; body: string }

export const anchorOf = (v: string): string => `rn-${v.replace(/[^a-z0-9.]/gi, '-')}`

/** The versions of a changelog (what precedes the first one is its intro). */
export function parseSections(md: string): { intro: string; sections: NoteSection[] } {
  const parts = md.split(/^## /m)
  const intro = parts.shift() ?? ''
  const sections: NoteSection[] = []
  for (const p of parts) {
    const nl = p.indexOf('\n')
    const heading = (nl === -1 ? p : p.slice(0, nl)).trim()
    const m = heading.match(/^(\S+)\s*(?:[—–-]\s*(.+))?$/)
    const version = m?.[1] ?? heading
    sections.push({ version, date: (m?.[2] ?? '').trim(), anchor: anchorOf(version), md: `## ${p}` })
  }
  return { intro, sections }
}

/** A version's items: its top-level bullets, each with its continuation lines (nested bullets included, moved
 *  back to the margin). The bold sentence a bullet opens on is its title; a bullet without one is all body.
 *  Text before the first bullet (a paragraph, a sub-heading) is kept as an untitled item. */
export function parseItems(sectionMd: string): NoteItem[] {
  const lines = sectionMd.split('\n')
  if (lines[0]?.startsWith('## ')) lines.shift()
  const chunks: string[][] = []
  let lead: string[] = []
  for (const line of lines) {
    if (/^[-*] /.test(line)) chunks.push([line.slice(2)])
    else if (chunks.length) chunks[chunks.length - 1].push(line.replace(/^ {2}/, ''))
    else lead.push(line)
  }
  const items: NoteItem[] = []
  lead = lead.join('\n').trim() ? lead : []
  if (lead.length) items.push({ title: '', body: lead.join('\n').trim() })
  for (const chunk of chunks) {
    const text = chunk.join('\n').trim()
    const m = /^\*\*([\s\S]+?)\*\*\s*/.exec(text)
    // a title is a sentence, not a bold word inside one: the bullet must OPEN on it
    if (m && m[1].length <= 160) items.push({ title: m[1].replace(/\s+/g, ' ').replace(/[.:]$/, ''), body: text.slice(m[0].length).trim() })
    else items.push({ title: '', body: text })
  }
  return items
}

/** The items that hold every word typed, in a version's notes. */
export function matchingItems(items: NoteItem[], query: string): NoteItem[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return items
  return items.filter((it) => { const hay = `${it.title}\n${it.body}`.toLowerCase(); return words.every((w) => hay.includes(w)) })
}
