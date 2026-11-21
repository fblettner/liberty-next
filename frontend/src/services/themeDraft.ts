// The Theme editor's draft: theme.toml's [theme] table as the page edits it, and back. No React here.
// Everything the file may hold is carried — the raw CSS variables too (`vars`, the power user's escape
// hatch): a save from the page must never drop what it does not show by default.

export interface ThemeDoc {
  preset: string
  app_name?: string | null
  primary_color?: string | null
  font_family?: string | null
  font_scale?: number | null
  vars?: Record<string, string>
}
export interface ThemePreset { id: string; label: string; primary?: string }
export interface ThemeFont { id: string; label: string; stack: string }

export interface ThemeDraft {
  preset: string
  appName: string
  /** '' = the palette's own colour */
  primary: string
  /** '' = the default font */
  fontFamily: string
  fontScale: number
  /** raw CSS variables, in the order typed: [name without `--`, value] */
  vars: [string, string][]
}

export const FALLBACK_PRIMARY = '#007AFF'
export const SIZE_STEPS = [
  { id: 'compact', value: 0.9 },
  { id: 'normal', value: 1.0 },
  { id: 'comfortable', value: 1.1 },
  { id: 'large', value: 1.2 },
] as const

const same = (a: number, b: number): boolean => Math.abs(a - b) < 1e-6

export function isHex(value: string): boolean {
  return /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/.test(value.trim())
}

/** `#rgb` → `#rrggbb` (what a colour input accepts); anything that is not a hex colour → the fallback. */
export function fullHex(value: string, fallback: string = FALLBACK_PRIMARY): string {
  const v = value.trim()
  if (!isHex(v)) return fallback
  return v.length === 4 ? `#${v.slice(1).split('').map((c) => c + c).join('')}` : v
}

export function draftFromDoc(doc: ThemeDoc): ThemeDraft {
  return {
    preset: doc.preset || 'default',
    appName: doc.app_name ?? '',
    primary: doc.primary_color ?? '',
    fontFamily: doc.font_family ?? '',
    fontScale: doc.font_scale ?? 1,
    vars: Object.entries(doc.vars ?? {}),
  }
}

/** A variable's name as the file keeps it: no leading `--`, no spaces. */
export const varName = (name: string): string => name.trim().replace(/^--/, '').trim()

/** The variables worth saving: a line needs a name and a value; a name typed twice keeps its last value. */
export function cleanVars(vars: [string, string][]): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [name, value] of vars) {
    const k = varName(name)
    if (k && value.trim()) out[k] = value.trim()
  }
  return out
}

export function docFromDraft(draft: ThemeDraft): ThemeDoc {
  const doc: ThemeDoc = { preset: draft.preset }
  if (draft.appName.trim()) doc.app_name = draft.appName.trim()
  if (draft.primary.trim()) doc.primary_color = draft.primary.trim()
  if (draft.fontFamily) doc.font_family = draft.fontFamily
  if (!same(draft.fontScale, 1)) doc.font_scale = draft.fontScale
  const vars = cleanVars(draft.vars)
  if (Object.keys(vars).length) doc.vars = vars
  return doc
}

/** The colour in force: the one typed, else the palette's. */
export function effectivePrimary(draft: ThemeDraft, presets: ThemePreset[]): string {
  return (draft.primary || presets.find((p) => p.id === draft.preset)?.primary || FALLBACK_PRIMARY).trim()
}

/** The font in force (the first of the list is the default). */
export function effectiveFont(draft: ThemeDraft, fonts: ThemeFont[]): ThemeFont | undefined {
  return fonts.find((f) => f.id === draft.fontFamily) ?? fonts[0]
}

/** The CSS variables the draft sets, the way the server resolves a saved theme: the accent family (given by
 *  `accent`, the same derivation as the server's), the font, the size, then the raw variables on top. */
export function draftVars(draft: ThemeDraft, presets: ThemePreset[], fonts: ThemeFont[], accent: (hex: string) => Record<string, string>): Record<string, string> {
  const out: Record<string, string> = { ...accent(effectivePrimary(draft, presets)) }
  const font = fonts.find((f) => f.id === draft.fontFamily)
  if (font?.stack) out['font-sans'] = font.stack
  if (!same(draft.fontScale, 1)) out['font-scale'] = String(draft.fontScale)
  return { ...out, ...cleanVars(draft.vars) }
}
