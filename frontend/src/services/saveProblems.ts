// A refused configuration save, as the builders use it: the problems the server lists
// (liberty/web/save_errors.py — where, in the labels of what holds it, and what is wrong) and what
// each of them points at, so that a builder opens the object. No React here.
import type { SaveProblem } from '../api/client'

export type { SaveProblem }

/** What a failed save left: the text to show, and the problems one by one when the server listed them. */
export interface SaveFailure {
  message: string
  problems: SaveProblem[]
}

/** Read a caught error. Anything that is not a refused save gives its message and no problem. */
export function failureOf(e: unknown): SaveFailure {
  if (e && typeof e === 'object') {
    const err = e as { message?: unknown; problems?: unknown }
    const problems = Array.isArray(err.problems) ? (err.problems as SaveProblem[]).filter(isProblem) : []
    if (typeof err.message === 'string') return { message: err.message, problems }
  }
  return { message: String(e), problems: [] }
}

function isProblem(p: unknown): p is SaveProblem {
  return !!p && typeof p === 'object' && Array.isArray((p as SaveProblem).path) && typeof (p as SaveProblem).message === 'string'
}

/** The sections a builder saves, as the server names them in a refused save. */
export type SaveSection =
  | 'screens' | 'charts' | 'dashboards' | 'explorer' | 'menus' | 'actions' | 'notifications'
  | 'connectors' | 'dictionary' | 'jobs' | 'pools' | 'app' | 'ai' | 'theme'

/** What a problem points at. ``scope`` is the application (or the connector a dictionary block
 *  belongs to), ``id`` the object, ``kind`` its family where a section holds several (a connector's
 *  queries / tables / endpoints, a dictionary's entries / enums / lookups / sequences), ``index``
 *  its place where the section is a list (a job, a menu entry), ``inner`` what is left of the path
 *  inside the object. */
export interface ProblemTarget {
  scope?: string
  kind?: string
  id?: string
  index?: number
  inner: (string | number)[]
}

const str = (v: string | number | undefined): string | undefined => (typeof v === 'string' ? v : undefined)
const num = (v: string | number | undefined): number | undefined => (typeof v === 'number' ? v : undefined)

/** Where *path* leads in *section*; null when it does not reach an object. */
export function targetOf(section: SaveSection, path: (string | number)[]): ProblemTarget | null {
  const [a, b, c, d, ...rest] = path
  switch (section) {
    case 'screens': case 'charts': case 'dashboards': case 'explorer':
      // <section>.<application>.<id>.…
      if (a !== section || !str(b) || !str(c)) return null
      return { scope: str(b), id: str(c), inner: d === undefined ? [] : [d, ...rest] }
    case 'actions': case 'notifications': case 'pools':
      // <section>.<id>.…
      if (a !== section || !str(b)) return null
      return { id: str(b), inner: path.slice(2) }
    case 'menus':
      // menus.<application>.items.<n>.…
      if (a !== 'menus' || !str(b)) return null
      return { scope: str(b), index: c === 'items' ? num(d) : undefined, inner: c === 'items' ? rest : path.slice(2) }
    case 'connectors':
      // connectors.<name>.<queries|tables|sequences|lookups>.<n>.…  (a list), or .endpoints.<key>.…
      if (a !== 'connectors' || !str(b)) return null
      return str(c) && d !== undefined
        ? { scope: str(b), kind: str(c), id: str(d), index: num(d), inner: rest }
        : { scope: str(b), inner: path.slice(2) }
    case 'dictionary':
      // <entries|enums|lookups|sequences>.<id>.…, or connectors.<scope>.<kind>.<id>.…
      if (a === 'connectors') {
        if (!str(b) || !str(c) || !str(d)) return str(b) ? { scope: str(b), inner: path.slice(2) } : null
        return { scope: str(b), kind: str(c), id: str(d), inner: rest }
      }
      if (!str(a) || !str(b)) return null
      return { scope: '', kind: str(a), id: str(b), inner: path.slice(2) }
    case 'jobs':
      // jobs.<n>.…  (steps.<n>.… inside)
      if (a !== 'jobs' || num(b) === undefined) return null
      return { index: num(b), inner: path.slice(2) }
    case 'app':
      // <section of the settings>.<field>
      if (!str(a)) return null
      return { id: str(a), inner: path.slice(1) }
    case 'ai':
      if (a !== 'ai' || !str(b)) return null
      return { scope: str(b), inner: path.slice(2) }
    default:
      return null
  }
}

// The refusals said the same way whatever the field: these have a translation; any other keeps
// the server's own sentence (it carries values — a list of choices, a limit).
const CODE_KEYS: Record<string, string> = {
  missing: 'missing',
  extra_forbidden: 'unknown',
  string_type: 'text',
  int_type: 'integer', int_parsing: 'integer', int_from_float: 'integer',
  float_type: 'number', float_parsing: 'number',
  bool_type: 'yesNo', bool_parsing: 'yesNo',
  list_type: 'list',
  dict_type: 'shape', model_type: 'shape', model_attributes_type: 'shape',
  string_too_short: 'empty',
  string_pattern_mismatch: 'characters',
  union_tag_not_found: 'noType',
}

/** The translation key of a problem's message (under ``saveProblems.code``) and the name it
 *  takes, or null when the server's sentence is shown as it is. */
export function messageKey(problem: SaveProblem): { key: string; name: string } | null {
  const key = CODE_KEYS[problem.code]
  if (!key) return null
  const last = problem.path[problem.path.length - 1]
  return { key: `saveProblems.code.${key}`, name: last === undefined ? '' : String(last) }
}
