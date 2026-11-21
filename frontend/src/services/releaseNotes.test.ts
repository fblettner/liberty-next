import { describe, expect, it } from 'vitest'
import { matchingItems, parseItems, parseSections } from './releaseNotes'

const MD = `# Product — Release notes

## 1.2.0 — unreleased

- **A page that shows it.** The palettes are swatches,
  and the preview is a piece of the application.
- **Security — a parameter can no longer redirect.** Now:
  - in a path, a segment is refused;
  - in a body, a value is escaped.
- A plain line without a title, with a **bold** word inside.

## 1.1.0 — 2026-11-19

A paragraph before the list.

- **Fixed.** One thing.
`

describe('the release notes', () => {
  it('splits the versions', () => {
    const { intro, sections } = parseSections(MD)
    expect(intro.trim()).toBe('# Product — Release notes')
    expect(sections.map((s) => [s.version, s.date])).toEqual([['1.2.0', 'unreleased'], ['1.1.0', '2026-11-19']])
  })
  it('splits a version into its items, each titled by the sentence it opens on', () => {
    const items = parseItems(parseSections(MD).sections[0].md)
    expect(items.map((i) => i.title)).toEqual(['A page that shows it', 'Security — a parameter can no longer redirect', ''])
    expect(items[0].body).toBe('The palettes are swatches,\nand the preview is a piece of the application.')
    expect(items[1].body).toBe('Now:\n- in a path, a segment is refused;\n- in a body, a value is escaped.')     // nested bullets, back at the margin
    expect(items[2].body).toBe('A plain line without a title, with a **bold** word inside.')
  })
  it('keeps what precedes the first bullet', () => {
    const items = parseItems(parseSections(MD).sections[1].md)
    expect(items).toEqual([{ title: '', body: 'A paragraph before the list.' }, { title: 'Fixed', body: 'One thing.' }])
  })
  it('finds the items that hold every word typed', () => {
    const items = parseItems(parseSections(MD).sections[0].md)
    expect(matchingItems(items, 'segment refused').map((i) => i.title)).toEqual(['Security — a parameter can no longer redirect'])
    expect(matchingItems(items, 'PALETTES')).toHaveLength(1)
    expect(matchingItems(items, '  ')).toHaveLength(3)
    expect(matchingItems(items, 'nothing like it')).toEqual([])
  })
})
