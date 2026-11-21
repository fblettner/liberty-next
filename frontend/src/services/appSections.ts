// The App editor's sections, in the order an installation is set up — not the alphabet's: what it is, who
// signs in, what is switched on, how it is run. No React here.
export type AppSectionKey = 'app' | 'display' | 'links' | 'license' | 'ai' | 'oidc' | 'notify' | 'branding' | 'log' | 'auth' | 'jobs' | 'changesets' | 'readonly'
export type AppSectionGroupId = 'installation' | 'signin' | 'features' | 'operate'

export const APP_SECTION_GROUPS: { id: AppSectionGroupId; keys: AppSectionKey[] }[] = [
  { id: 'installation', keys: ['app', 'display', 'links', 'branding', 'license'] },
  { id: 'signin', keys: ['auth', 'oidc'] },
  { id: 'features', keys: ['ai', 'notify', 'jobs', 'changesets'] },
  { id: 'operate', keys: ['log', 'readonly'] },
]
export const APP_SECTION_KEYS: AppSectionKey[] = APP_SECTION_GROUPS.flatMap((g) => g.keys)

/** The groups with the sections the page really has (the read-only list exists only when something is set
 *  outside the file); a group left empty is not shown. */
export function groupedSections<T extends { key: AppSectionKey }>(sections: T[]): { id: AppSectionGroupId; sections: T[] }[] {
  const by = new Map(sections.map((s) => [s.key, s]))
  return APP_SECTION_GROUPS
    .map((g) => ({ id: g.id, sections: g.keys.map((k) => by.get(k)).filter((s): s is T => !!s) }))
    .filter((g) => g.sections.length > 0)
}

/** The section shown: the one remembered when the page still has it, else the first. */
export function pickedSection<T extends { key: AppSectionKey }>(sections: T[], remembered: string | null): AppSectionKey | null {
  if (remembered && sections.some((s) => s.key === remembered)) return remembered as AppSectionKey
  return groupedSections(sections)[0]?.sections[0]?.key ?? null
}

/** A field's label and the explanation it carried in parentheses, apart: the label stays one short line above
 *  the field, the explanation goes under it in plain case. A short parenthesis — a unit, "(s)", "(dashboards)" —
 *  is part of the label and stays there. */
export function splitLabel(text: string): { label: string; help: string } {
  const m = /^(.*\S) \(([^()]+)\)$/.exec(text.trim())
  if (!m || m[2].length <= 14) return { label: text.trim(), help: '' }
  return { label: m[1], help: m[2] }
}
