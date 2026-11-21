// "Refresh from the server" of a generated endpoint (the API connector editor): the endpoint is
// built again from the same request ("Create from…" keeps it on the endpoint as `source`) and
// merged into the one the operator has — what the SERVER decides is taken from the fresh build
// (the address, the body, which parameters exist, whether it changes data), what the OPERATOR
// decides is kept (the name, the label and description, the response settings, and what they
// wrote on a parameter that still exists).

export interface GeneratedParam {
  name: string
  label?: string | null
  default?: string | null
  help?: string | null
  example?: string | null
  choices?: string[] | null
}

export interface GeneratedEndpoint {
  name: string
  method?: string
  path?: string
  body?: string | null
  writable?: boolean
  error_fields?: string[] | null
  warning_fields?: string[] | null
  params?: GeneratedParam[]
  [key: string]: unknown
}

export interface RefreshResult<T extends GeneratedEndpoint> {
  /** The endpoint after the refresh. */
  next: T
  /** What changes, in words — empty when the endpoint is already what the server describes. */
  changes: string[]
}

const same = (a: unknown, b: unknown): boolean => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

/** The JSON of a body without its layout, so re-indenting is not a change. */
const bodyKey = (body: string | null | undefined): string => {
  const text = (body ?? '').trim()
  if (!text) return ''
  try {
    // placeholders outside quotes ({{ROWS}}) are not JSON: compare without the white space instead
    return JSON.stringify(JSON.parse(text))
  } catch {
    return text.replace(/\s+/g, '')
  }
}

export function mergeGenerated<T extends GeneratedEndpoint>(current: T, fresh: GeneratedEndpoint): RefreshResult<T> {
  const changes: string[] = []
  const had = new Map((current.params ?? []).map((p) => [p.name, p]))
  const wanted = fresh.params ?? []
  const params: GeneratedParam[] = wanted.map((p) => {
    const mine = had.get(p.name)
    // a parameter that stays keeps what the operator wrote on it; only its allowed values follow the server
    return mine ? { ...p, ...mine, choices: p.choices ?? mine.choices } : p
  })
  const added = wanted.filter((p) => !had.has(p.name)).map((p) => p.name)
  const removed = [...had.keys()].filter((n) => !wanted.some((p) => p.name === n))
  if (added.length) changes.push(`+ ${added.join(', ')}`)
  if (removed.length) changes.push(`− ${removed.join(', ')}`)
  if ((current.method ?? 'GET') !== (fresh.method ?? 'GET')) changes.push(`method: ${current.method ?? 'GET'} → ${fresh.method ?? 'GET'}`)
  if ((current.path ?? '') !== (fresh.path ?? '')) changes.push(`path: ${current.path ?? ''} → ${fresh.path ?? ''}`)
  if (bodyKey(current.body) !== bodyKey(fresh.body)) changes.push('body')
  if (!!current.writable !== !!fresh.writable) changes.push(fresh.writable ? 'changes data: yes' : 'changes data: no')
  if (!same(current.error_fields ?? [], fresh.error_fields ?? [])) changes.push('error fields')
  if (!same(current.warning_fields ?? [], fresh.warning_fields ?? [])) changes.push('warning fields')
  const choicesChanged = wanted.filter((p) => had.has(p.name) && !same(had.get(p.name)?.choices, p.choices ?? had.get(p.name)?.choices)).map((p) => p.name)
  if (choicesChanged.length) changes.push(`allowed values: ${choicesChanged.join(', ')}`)

  const next: Record<string, unknown> = { ...current, method: fresh.method, path: fresh.path }
  for (const [key, value] of Object.entries({ body: fresh.body, writable: fresh.writable || undefined, error_fields: fresh.error_fields?.length ? fresh.error_fields : undefined, warning_fields: fresh.warning_fields?.length ? fresh.warning_fields : undefined, params: params.length ? params : undefined })) {
    if (value === undefined || value === null || value === '') delete next[key]
    else next[key] = value
  }
  return { next: next as T, changes }
}
