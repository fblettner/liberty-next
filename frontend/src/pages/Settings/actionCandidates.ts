// Helpers that derive ParamBind autocomplete candidates from the workspace catalog + the
// surrounding screen's read-query columns. Used by ``ActionTreeView`` (Visual Designer's
// Inspector) and ``ActionListEditor`` (ScreenEditor's dialog hooks) to populate the
// ``param`` / ``source`` SearchSelects on each :class:`ParamBind` row.
//
// v1 surfaced these suggestions everywhere a param/source needed typing — the developer
// picked the target query's column name from a dropdown instead of remembering it. v2's
// equivalent reads the same data from the live ``/api/connectors`` + ``/api/screens``
// payloads cached in the workspace context.
import type { SearchSelectOption } from '../../common'
import type { ConnectorMeta, SqlQueryMeta, ApiEndpointMeta, Column } from '../../types/connectors'
import { BUILTIN_SOURCE_PATHS } from '../TableView/actionRunner'

type Row = Record<string, unknown>

/** ``param`` candidates — the target query / endpoint's parameters. For a SQL query both the
 *  *declared* ``params`` (carrying labels) and the *scanned* ``:bind_params`` from the SQL
 *  text are unioned; declared wins for the label. For an API endpoint only the declared
 *  ``params``. The result is sorted alphabetically so a long target reads predictably.
 *
 *  Returns ``[]`` when the action's target can't be located in the workspace catalog (a
 *  freshly-renamed query, a connector the caller can't see, …) — the consumer falls back to
 *  a plain text input. */
export function targetParamOptions(
  action: Row,
  wsConnectors: ConnectorMeta[] | null | undefined,
  defaultConnector: string,
): SearchSelectOption[] {
  const aType = String(action?.type ?? '')
  if (aType !== 'run_query' && aType !== 'navigate' && aType !== 'call_api') return []
  const targetConn = (typeof action.connector === 'string' && action.connector.trim()
    ? action.connector
    : defaultConnector) as string
  const conn = (wsConnectors ?? []).find((c) => c.name === targetConn)
  if (!conn) return []

  if (aType === 'call_api') {
    if (conn.type !== 'api') return []
    const ep = (conn.endpoints as ApiEndpointMeta[]).find((e) => e.name === action.endpoint)
    if (!ep) return []
    return ep.params.map((p) => ({
      value: p.name,
      label: p.label ?? '',
      mono: p.name,
    })).sort(byValue)
  }

  // run_query / navigate — both target a SQL query.
  if (conn.type !== 'sql') return []
  const targetName = String((aType === 'navigate' ? action.to : action.query) ?? '')
  const q = (conn.queries as SqlQueryMeta[]).find((qq) => qq.name === targetName)
  if (!q) return []
  // Union declared params + scanned bind_params. Declared wins (it carries the label).
  const declared = new Map<string, string | null>()
  for (const p of q.params) declared.set(p.name, p.label)
  for (const name of q.bind_params) if (!declared.has(name)) declared.set(name, null)
  return [...declared.entries()].map(([name, label]) => ({
    value: name,
    label: label ?? '',
    mono: name,
  })).sort(byValue)
}

/** The param names a SQL query accepts — its declared ``params`` ∪ the scanned ``:bind_params``.
 *  This is the authoritative candidate set for binding a lookup's parameters: a dd entry's STATIC
 *  ``lookup_params`` or a screen column's DYNAMIC ``lookup_param_binds``. Resolve a lookup → its
 *  ``query`` + ``connector`` → call this. (NOT the lookup's ``key_columns`` — those are label
 *  disambiguation, not query parameters; the historical ``params``-named field was that, renamed.) */
export function lookupQueryParamNames(
  wsConnectors: ConnectorMeta[] | null | undefined,
  connector: string,
  queryName: string,
): string[] {
  const conn = (wsConnectors ?? []).find((c) => c.name === connector)
  if (!conn || conn.type !== 'sql' || !queryName) return []
  const q = (conn.queries as SqlQueryMeta[]).find((qq) => qq.name === queryName)
  if (!q) return []
  const names = new Set<string>()
  for (const p of q.params) names.add(p.name)
  for (const n of q.bind_params) names.add(n)
  return [...names].sort()
}

/** ``source`` candidates from the *firing screen's* read-query columns. For a row-menu or
 *  toolbar action, the firing context IS the row that fired it; column names with no dots
 *  resolve against the row at runtime. The display label is the column's friendly label
 *  (when set) so the operator picks by human name. */
export function screenReadColumnOptions(
  columns: Column[] | Row[] | undefined | null,
): SearchSelectOption[] {
  if (!Array.isArray(columns) || columns.length === 0) return []
  const out: SearchSelectOption[] = []
  const seen = new Set<string>()
  for (const c of columns) {
    const name = String((c as Row).name ?? '').trim()
    if (!name || seen.has(name)) continue
    seen.add(name)
    const label = (c as Row).label
    out.push({
      value: name,
      label: typeof label === 'string' && label ? label : '',
      mono: name,
    })
  }
  out.sort(byValue)
  return out
}

/** ``#LOGIN_USER#`` / ``#SYSDATE#`` / ``#NOW#`` / ``#LANGUAGE#`` — auth + runtime built-ins,
 *  v2's port of v1's `dd_rules` LOGIN / SYSDATE / CURRENT_DATE resolvers. The runtime side lives
 *  in :file:`pages/TableView/actionRunner.ts` (``resolveBuiltin``); this just surfaces the
 *  same catalog so the operator sees them in the SearchSelect dropdown instead of having to
 *  remember the exact ``#NAME#`` form. ``allowCustom`` stays on so any future built-in still
 *  resolves when typed by hand. */
export function builtinSourceOptions(): SearchSelectOption[] {
  return BUILTIN_SOURCE_PATHS.map((b) => ({
    value: b.value,
    label: b.label,
    mono: b.value,
  }))
}

/** Merge multiple candidate lists, de-duplicating by ``value`` (first occurrence wins). The
 *  ordering convention used by the editors: chain-context candidates first (most specific to
 *  the workflow), then screen-read-row columns (the firing context), then the standard
 *  built-ins (always applicable), then anything else. */
export function mergeCandidates(...lists: SearchSelectOption[][]): SearchSelectOption[] {
  const seen = new Set<string>()
  const out: SearchSelectOption[] = []
  for (const list of lists) {
    for (const opt of list) {
      if (seen.has(opt.value)) continue
      seen.add(opt.value)
      out.push(opt)
    }
  }
  return out
}

const byValue = (a: SearchSelectOption, b: SearchSelectOption) => a.value.localeCompare(b.value)
