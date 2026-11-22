// The functions the applications ship (`module:function`, the `j_*` of plugins/), as ONE picker's
// choices: grouped by application and module, each line = the function's name and what it does.
// Used by every place a function is picked (a job's python step, a service's plugin step, an
// endpoint served by a function, the assistant's tools, the steps run after a change package).
import { api } from '../api/client'

export interface CallableDef {
  /** what is stored: `module:function` */
  callable: string
  module?: string | null
  name?: string | null
  /** the first line of the function's documentation */
  docstring?: string | null
}

export interface CallableOption {
  value: string
  /** what the function does, without the documentation's markup */
  label: string
  /** the function's name alone — its module is the group */
  mono: string
  /** `application · module` */
  group: string
}

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** `nomasx1.security:j_users` → application `nomasx1`, module `security`, name `j_users`. */
export function callableParts(callable: string): { app: string; module: string; name: string } {
  const at = callable.indexOf(':')
  const path = at < 0 ? '' : callable.slice(0, at)
  const name = at < 0 ? callable : callable.slice(at + 1)
  const dot = path.indexOf('.')
  return { app: dot < 0 ? path : path.slice(0, dot), module: dot < 0 ? '' : path.slice(dot + 1), name }
}

/** A documentation line as plain words: ``code``, *name*, :mod:`x` lose their marks. */
export function plainDoc(doc: string | null | undefined): string {
  return (doc ?? '')
    .replace(/:[a-z]+:`~?([^`]*)`/g, '$1')
    .replace(/``([^`]*)``/g, '$1')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/\*\*([^*]+)\*\*/g, '$1')
    .replace(/\*([^*\s][^*]*)\*/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
}

export function groupOf(callable: string): string {
  const { app, module } = callableParts(callable)
  return module ? `${app} · ${module}` : app
}

/** The picker's choices, in the order shown: the application of *current* first (the one being
 *  worked on), then the others by name; inside an application its modules, inside a module its
 *  functions by name. Never sorted on the description. */
export function callableOptions(catalog: CallableDef[], current = ''): CallableOption[] {
  const mine = callableParts(current).app
  const seen = new Set<string>()
  return catalog
    .filter((c) => c.callable && !seen.has(c.callable) && seen.add(c.callable))
    .map((c) => ({ c, p: callableParts(c.callable) }))
    .sort((a, b) =>
      (a.p.app === mine ? 0 : 1) - (b.p.app === mine ? 0 : 1)
      || collator.compare(a.p.app, b.p.app)
      || collator.compare(a.p.module, b.p.module)
      || collator.compare(a.p.name, b.p.name))
    .map(({ c, p }) => ({
      value: c.callable,
      label: plainDoc(c.docstring) || p.name,
      mono: p.name,
      group: groupOf(c.callable),
    }))
}

// One read per session: the catalogue only changes with an installation of the applications.
// A failure is not kept, so the next picker asks again.
let pending: Promise<CallableDef[]> | null = null

export function loadCallables(): Promise<CallableDef[]> {
  pending ??= api.get<{ callables?: CallableDef[] }>('/api/plugins/callables')
    .then((r) => r.callables ?? [])
    .catch((e) => { pending = null; throw e })
  return pending
}
