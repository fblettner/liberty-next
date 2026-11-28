// What a user may set when starting a job from its menu page (`user_params` in jobs.toml): each
// parameter's choices come from a fixed list, or are read when the page opens — the rows of a
// query, the Explorer models of an application, the installation's test suites, its databases. That source is
// one string in the file (`choices_from`); the job editor shows it as the fields an operator
// knows. No React here.
//
//   query:<connector>/<query>:<value column>:<label column>
//   explorer_models:<application>
//   test_suites            test_suites:<connector>
//   pools                  pools:<connector>          (every database, or those a connector runs on)
//   connectors             connectors:sql | connectors:api

export type ChoiceSource =
  | { kind: 'none' }
  | { kind: 'query'; connector: string; query: string; value: string; label: string }
  | { kind: 'explorer_models'; app: string }
  | { kind: 'test_suites'; connector: string }
  | { kind: 'pools'; connector: string }
  | { kind: 'connectors'; type: '' | 'sql' | 'api' }
  /** a source the editor does not know: kept exactly as written */
  | { kind: 'other'; text: string }

export function parseChoicesFrom(text: string | null | undefined): ChoiceSource {
  const s = (text ?? '').trim()
  if (!s) return { kind: 'none' }
  const at = s.indexOf(':')
  const kind = at < 0 ? s : s.slice(0, at)
  const arg = at < 0 ? '' : s.slice(at + 1)
  if (kind === 'explorer_models') return { kind, app: arg }
  if (kind === 'test_suites') return { kind, connector: arg }
  if (kind === 'pools') return { kind, connector: arg }
  if (kind === 'connectors' && (arg === '' || arg === 'sql' || arg === 'api')) return { kind, type: arg }
  if (kind === 'query') {
    const [target = '', value = '', label = '', ...more] = arg.split(':')
    const slash = target.indexOf('/')
    if (more.length === 0) return { kind, connector: slash < 0 ? target : target.slice(0, slash), query: slash < 0 ? '' : target.slice(slash + 1), value, label }
  }
  return { kind: 'other', text: s }
}

/** The string the file holds — '' when there is no source. */
export function composeChoicesFrom(src: ChoiceSource): string {
  switch (src.kind) {
    case 'none': return ''
    case 'explorer_models': return `explorer_models:${src.app}`
    case 'test_suites': return src.connector ? `test_suites:${src.connector}` : 'test_suites'
    case 'pools': return src.connector ? `pools:${src.connector}` : 'pools'
    case 'connectors': return src.type ? `connectors:${src.type}` : 'connectors'
    case 'query': return `query:${src.connector}/${src.query}:${src.value}${src.label ? `:${src.label}` : ''}`
    case 'other': return src.text
  }
}

/** One parameter as jobs.toml holds it. */
export interface UserParamConfig {
  name: string
  label?: string | null
  /** the label per language */
  l?: Record<string, string>
  /** the step receiving the value; blank = every step */
  step?: string | null
  default?: string | null
  required?: boolean
  choices?: string[]
  choices_from?: string | null
  multiple?: boolean
}

/** A parameter ready to be saved: what is blank or off is left out, so the file stays as short as written by hand. */
export function cleanUserParam(p: UserParamConfig): UserParamConfig {
  const out: UserParamConfig = { name: p.name.trim() }
  if (p.label?.trim()) out.label = p.label.trim()
  const l = Object.fromEntries(Object.entries(p.l ?? {}).filter(([, v]) => typeof v === 'string' && v.trim()))
  if (Object.keys(l).length) out.l = l
  if (p.step?.trim()) out.step = p.step.trim()
  if (p.default != null && String(p.default).trim() !== '') out.default = String(p.default)
  if (p.required) out.required = true
  const choices = (p.choices ?? []).map((c) => String(c).trim()).filter(Boolean)
  if (choices.length) out.choices = choices
  if (p.choices_from?.trim()) out.choices_from = p.choices_from.trim()
  if (p.multiple) out.multiple = true
  return out
}

/** The list ready to be saved: unnamed parameters dropped; undefined when none is left. */
export function cleanUserParams(list: readonly UserParamConfig[] | null | undefined): UserParamConfig[] | undefined {
  const kept = (list ?? []).filter((p) => p.name?.trim()).map(cleanUserParam)
  return kept.length ? kept : undefined
}

/** The names "one run per value" may point at: the job's own parameters and those the user sets. */
export function forEachCandidates(params: Record<string, unknown> | null | undefined, user: readonly UserParamConfig[] | null | undefined): string[] {
  return [...new Set([...(user ?? []).map((p) => p.name?.trim()).filter(Boolean) as string[], ...Object.keys(params ?? {})])]
}
