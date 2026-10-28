// The AI assistant's page context — what the user is looking at right now, published by the
// page (a TableView tab today) and read by the Chat drawer, which sends its rendering as a
// second, uncached system block with each turn. A plain module store (no React): the page calls
// ``setPageContext`` from an effect, the chat subscribes. ``path`` is the route that published
// it, so the chat shows the chip only while the user is still on that page.

export interface PageContext {
  /** The route that published this context (``location.pathname``). */
  path: string
  app?: string | null
  kind: 'screen' | 'sql' | 'explorer'
  /** The page's friendly title (the screen's label / the query's description). */
  title: string
  connector: string
  query: string
  /** The screen id when the page is a designed screen. */
  screen?: string | null
  /** The run params + server filters, as the TableView sends them (``NAME`` → value, ``NAME_op`` → operator). */
  filters?: Record<string, string>
  rowCount?: number
  truncated?: boolean
  columns?: string[]
  /** The row the user last clicked (or opened the row menu on). */
  row?: Record<string, unknown> | null
  /** An Explorer page: the model and the analysis on the canvas (its entity, rows, measures, period). */
  explorer?: { app: string; model: string; entity?: string; rows?: string[]; measures?: string[]; period?: string | null; analysis?: string | null }
}

/** The hard cap on the rendered text — the backend caps at the same size (an uncached block is paid on every turn). */
export const CONTEXT_MAX_CHARS = 4000
const MAX_COLUMNS = 80
const MAX_CELL = 120

let current: PageContext | null = null
const listeners = new Set<(c: PageContext | null) => void>()

export function getPageContext(): PageContext | null {
  return current
}

export function setPageContext(ctx: PageContext | null): void {
  current = ctx
  for (const fn of listeners) fn(ctx)
}

export function subscribePageContext(fn: (c: PageContext | null) => void): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** The active filters as ``NAME op value`` strings (the ``_op`` companions folded in). */
export function filterLines(filters: Record<string, string> | undefined): string[] {
  if (!filters) return []
  const out: string[] = []
  for (const [k, v] of Object.entries(filters)) {
    if (k.endsWith('_op') || v === '') continue
    const op = filters[`${k}_op`]
    out.push(`${k} ${op && op !== 'eq' ? op : '='} ${v}`)
  }
  return out
}

function clipCell(v: unknown): unknown {
  if (typeof v === 'string' && v.length > MAX_CELL) return v.slice(0, MAX_CELL - 1) + '…'
  return v
}

/** The text sent to the assistant: one line per fact, capped at CONTEXT_MAX_CHARS. */
export function renderPageContext(c: PageContext): string {
  const lines: string[] = []
  lines.push(`app: ${c.app || c.connector}`)
  if (c.kind === 'explorer' && c.explorer) {
    const x = c.explorer
    lines.push(`page: Data Explorer — model ${x.app}/${x.model} (${c.title}); use explorer_fields / explorer_query on it, explorer_save_analysis to save an analysis the user asks for`)
    if (x.analysis) lines.push(`saved analysis open: ${x.analysis}`)
    if (x.entity) lines.push(`analysis on the canvas: entity ${x.entity}; rows ${x.rows?.length ? x.rows.join(', ') : 'none'}; measures ${x.measures?.length ? x.measures.join(', ') : 'none'}; period ${x.period ?? 'none'}`)
    return lines.join('\n')
  }
  if (c.screen) lines.push(`screen: ${c.screen} — ${c.title} (connector ${c.connector}, read query ${c.query})`)
  else lines.push(`query: ${c.connector}.${c.query} — ${c.title}`)
  const f = filterLines(c.filters)
  lines.push(f.length ? `filters: ${f.join('; ')}` : 'filters: none')
  if (c.rowCount != null) lines.push(`rows loaded: ${c.rowCount}${c.truncated ? ' (cut by the row cap — more exist)' : ''}`)
  if (c.columns && c.columns.length) {
    lines.push(`columns: ${c.columns.slice(0, MAX_COLUMNS).join(', ')}${c.columns.length > MAX_COLUMNS ? `, … (${c.columns.length} in all)` : ''}`)
  }
  if (c.row) {
    const clipped: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(c.row)) clipped[k] = clipCell(v)
    lines.push(`current row (the one the user clicked): ${JSON.stringify(clipped)}`)
  }
  const text = lines.join('\n')
  return text.length <= CONTEXT_MAX_CHARS ? text : text.slice(0, CONTEXT_MAX_CHARS - 1) + '…'
}

/** What the chip shows: the page's short name, how many filters, whether a row is in. */
export function summarizePageContext(c: PageContext): { name: string; filters: number; hasRow: boolean } {
  return { name: c.screen ?? c.query, filters: filterLines(c.filters).length, hasRow: !!c.row }
}
