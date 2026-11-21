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
  required?: boolean
}

export interface GeneratedEndpoint {
  name: string
  method?: string
  path?: string
  body?: string | null
  writable?: boolean
  rows_field?: string | null
  row_map?: Record<string, string> | null
  labels_field?: string | null
  more_field?: string | null
  limit_param?: string | null
  callable?: string | null
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
    // … and whether it is required: the server's object decides, not a label the operator wrote
    if (!mine) return p
    const { required: _was, ...kept } = mine
    void _was
    return { ...p, ...kept, choices: p.choices ?? mine.choices }
  })
  const added = wanted.filter((p) => !had.has(p.name)).map((p) => p.name)
  const removed = [...had.keys()].filter((n) => !wanted.some((p) => p.name === n))
  if (added.length) changes.push(`+ ${added.join(', ')}`)
  if (removed.length) changes.push(`− ${removed.join(', ')}`)
  if ((current.method ?? 'GET') !== (fresh.method ?? 'GET')) changes.push(`method: ${current.method ?? 'GET'} → ${fresh.method ?? 'GET'}`)
  if ((current.path ?? '') !== (fresh.path ?? '')) changes.push(`path: ${current.path ?? ''} → ${fresh.path ?? ''}`)
  if ((current.callable ?? '') !== (fresh.callable ?? '')) changes.push(`function: ${current.callable ?? '—'} → ${fresh.callable ?? '—'}`)
  if (bodyKey(current.body) !== bodyKey(fresh.body)) changes.push('body')
  if (!!current.writable !== !!fresh.writable) changes.push(fresh.writable ? 'changes data: yes' : 'changes data: no')
  if (!same([current.rows_field, current.row_map, current.labels_field, current.more_field, current.limit_param], [fresh.rows_field, fresh.row_map, fresh.labels_field, fresh.more_field, fresh.limit_param])) changes.push('rows')
  if (!same(current.error_fields ?? [], fresh.error_fields ?? [])) changes.push('error fields')
  if (!same(current.warning_fields ?? [], fresh.warning_fields ?? [])) changes.push('warning fields')
  const choicesChanged = wanted.filter((p) => had.has(p.name) && !same(had.get(p.name)?.choices, p.choices ?? had.get(p.name)?.choices)).map((p) => p.name)
  if (choicesChanged.length) changes.push(`allowed values: ${choicesChanged.join(', ')}`)

  const next: Record<string, unknown> = { ...current, method: fresh.method, path: fresh.path }
  for (const [key, value] of Object.entries({ callable: fresh.callable, body: fresh.body, writable: fresh.writable || undefined, rows_field: fresh.rows_field, row_map: fresh.row_map && Object.keys(fresh.row_map).length ? fresh.row_map : undefined, labels_field: fresh.labels_field, more_field: fresh.more_field, limit_param: fresh.limit_param,
    error_fields: fresh.error_fields?.length ? fresh.error_fields : undefined, warning_fields: fresh.warning_fields?.length ? fresh.warning_fields : undefined, params: params.length ? params : undefined })) {
    if (value === undefined || value === null || value === '') delete next[key]
    else next[key] = value
  }
  return { next: next as T, changes }
}

// ── "Modify…": the same generator asked again with other choices ──────────────────────────────

export interface ModifyLine { name: string; changes: string[]; added?: boolean }

const sourceOf = (e: GeneratedEndpoint): { generator?: string; candidate?: string } | undefined =>
  (e.source && typeof e.source === 'object' ? (e.source as { generator?: string; candidate?: string }) : undefined)

/** The endpoints after a generated one (at `idx`) was built again with OTHER choices: what the build
 *  returned is merged into the endpoints of the same origin (the same generator and candidate — an
 *  application's read / add / change / delete go together), each as a refresh does (`mergeGenerated`:
 *  the operator's name, label and parameter texts are kept); an endpoint the build returns that the list
 *  does not hold is added; one it no longer returns is left as it is. Every endpoint touched takes the
 *  build's `source`, so the next "Modify…" starts from the new choices. */
export function mergeModified<T extends GeneratedEndpoint>(endpoints: T[], idx: number, built: GeneratedEndpoint[]): { endpoints: T[]; lines: ModifyLine[] } {
  const target = endpoints[idx]
  const origin = target ? sourceOf(target) : undefined
  if (!target || !origin) return { endpoints, lines: [] }
  const next = endpoints.slice()
  const lines: ModifyLine[] = []
  const family = (e: GeneratedEndpoint) => { const s = sourceOf(e); return !!s && s.generator === origin.generator && s.candidate === origin.candidate }
  for (const fresh of built) {
    // the one being modified is found whatever it was renamed to when the build returns a single endpoint
    let at = built.length === 1 ? idx : next.findIndex((e) => family(e) && e.name === fresh.name)
    if (at < 0 && fresh.name === target.name) at = idx
    if (at < 0) {
      let name = fresh.name
      for (let n = 2; next.some((e) => e.name === name); n++) name = `${fresh.name}_${n}`
      next.push({ ...fresh, name } as T)
      lines.push({ name, changes: [], added: true })
      continue
    }
    const merged = mergeGenerated(next[at], fresh)
    next[at] = { ...merged.next, ...(fresh.source ? { source: fresh.source } : {}) } as T
    if (merged.changes.length) lines.push({ name: next[at].name, changes: merged.changes })
  }
  return { endpoints: next, lines }
}

/** The keys of the first row an answer holds at `rowsField` (a dotted path; '' = the answer itself is
 *  the list) — what a column can be read from. Empty when there is no row there. */
export function rowKeys(answer: unknown, rowsField: string): string[] {
  let at: unknown = answer
  for (const part of rowsField.split('.').map((p) => p.trim()).filter(Boolean)) {
    if (Array.isArray(at)) at = at[0]
    if (!at || typeof at !== 'object') return []
    at = (at as Record<string, unknown>)[part]
  }
  const row = Array.isArray(at) ? at[0] : at
  return row && typeof row === 'object' && !Array.isArray(row) ? Object.keys(row as object) : []
}
