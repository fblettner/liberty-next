import { describe, expect, it } from 'vitest'
import { cleanVars, docFromDraft, draftFromDoc, draftVars, effectiveFont, effectivePrimary, fullHex, isHex } from './themeDraft'

const PRESETS = [{ id: 'default', label: 'Liberty', primary: '#007AFF' }, { id: 'violet', label: 'Violet', primary: '#7C3AED' }]
const FONTS = [{ id: 'dm-sans', label: 'DM Sans', stack: "'DM Sans', sans-serif" }, { id: 'inter', label: 'Inter', stack: "'Inter', sans-serif" }]
const accent = (hex: string) => ({ 'blue-main': hex })

describe('the theme draft', () => {
  it('carries everything the file holds, the raw variables too', () => {
    const doc = { preset: 'violet', app_name: 'Atlas', primary_color: '#112233', font_family: 'inter', font_scale: 1.1, vars: { 'bg-base': '#000', 'radius-md': '4px' } }
    expect(docFromDraft(draftFromDoc(doc))).toEqual(doc)
  })
  it('writes only what is set', () => {
    expect(docFromDraft(draftFromDoc({ preset: '' }))).toEqual({ preset: 'default' })
    expect(docFromDraft({ preset: 'default', appName: '  ', primary: ' ', fontFamily: '', fontScale: 1, vars: [['', 'x'], ['a', ' ']] })).toEqual({ preset: 'default' })
  })
  it('cleans the raw variables', () => {
    expect(cleanVars([['--bg-base', ' #000 '], ['radius', '4px'], ['radius', '6px'], ['  ', 'x']])).toEqual({ 'bg-base': '#000', radius: '6px' })
  })
  it('knows the colour and the font in force', () => {
    const draft = draftFromDoc({ preset: 'violet' })
    expect(effectivePrimary(draft, PRESETS)).toBe('#7C3AED')
    expect(effectivePrimary({ ...draft, primary: '#abc' }, PRESETS)).toBe('#abc')
    expect(effectivePrimary({ ...draft, preset: 'gone' }, PRESETS)).toBe('#007AFF')
    expect(effectiveFont(draft, FONTS)?.id).toBe('dm-sans')
    expect(effectiveFont({ ...draft, fontFamily: 'inter' }, FONTS)?.id).toBe('inter')
  })
  it('resolves the variables like the server: accent, font, size, then the raw ones on top', () => {
    const draft = { ...draftFromDoc({ preset: 'violet', font_family: 'inter', font_scale: 1.2 }), vars: [['blue-main', '#ff0000'], ['x', '1']] as [string, string][] }
    expect(draftVars(draft, PRESETS, FONTS, accent)).toEqual({ 'blue-main': '#ff0000', 'font-sans': "'Inter', sans-serif", 'font-scale': '1.2', x: '1' })
    expect(draftVars(draftFromDoc({ preset: 'default' }), PRESETS, FONTS, accent)).toEqual({ 'blue-main': '#007AFF' })
  })
  it('reads hex colours', () => {
    expect(isHex('#abc')).toBe(true)
    expect(isHex('#aabbcc')).toBe(true)
    expect(isHex('blue')).toBe(false)
    expect(fullHex('#abc')).toBe('#aabbcc')
    expect(fullHex('nope')).toBe('#007AFF')
  })
})
