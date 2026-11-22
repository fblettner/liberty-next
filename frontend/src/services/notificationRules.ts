// The notification rules a service step may send (`send_notification`): the rules of
// notifications.toml whose trigger is "action", and the values each one's texts wait for.
// Pure — the pickers of the action editor are built from it.

export interface RuleRecord {
  label?: string | null
  enabled?: boolean
  trigger?: { kind?: string } | null
  subject?: string | null
  body?: string | null
  link?: string | null
  action?: { params?: Record<string, unknown> | null } | null
}

export interface ActionRule {
  id: string
  /** what the picker shows: the rule's label, else its subject */
  label: string
  enabled: boolean
  /** the `{names}` its subject, body, link and call wait for, in the order they first appear */
  placeholders: string[]
}

// What the server adds to every payload by itself: never asked from the step.
const GIVEN = new Set(['USER'])

/** The `{names}` of a text. `{{` / `}}` are literal braces, `{a.b}` / `{a[0]}` / `{a:>5}` name `a`. */
export function placeholdersIn(text: unknown): string[] {
  if (typeof text !== 'string' || !text) return []
  const out: string[] = []
  const bare = text.replace(/\{\{|\}\}/g, '')
  for (const m of bare.matchAll(/\{([A-Za-z_]\w*)[^{}]*\}/g)) {
    if (!out.includes(m[1])) out.push(m[1])
  }
  return out
}

export function rulePlaceholders(rule: RuleRecord): string[] {
  const out: string[] = []
  const add = (text: unknown) => {
    for (const name of placeholdersIn(text)) {
      if (!GIVEN.has(name.toUpperCase()) && !out.some((o) => o.toUpperCase() === name.toUpperCase())) out.push(name)
    }
  }
  add(rule.subject); add(rule.body); add(rule.link)
  for (const v of Object.values(rule.action?.params ?? {})) add(v)
  return out
}

/** The rules a step may send, by id. A rule of another trigger (a screen event, a job) is left out:
 *  the server answers "not found" for it. */
export function actionRules(notifications: Record<string, RuleRecord> | null | undefined): ActionRule[] {
  return Object.entries(notifications ?? {})
    .filter(([, r]) => r?.trigger?.kind === 'action')
    .map(([id, r]) => ({
      id,
      label: (r.label || r.subject || id).trim() || id,
      enabled: r.enabled !== false,
      placeholders: rulePlaceholders(r),
    }))
    .sort((a, b) => a.id.localeCompare(b.id))
}
