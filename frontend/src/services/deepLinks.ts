// Links that open a framework page ON something — from a page an application ships, a notification, a
// document: the address says what, the page reads it once. No React.
//
//   /job/<id>?<parameter>=<value>&…                       the job's page with those values filled in
//   /liberty/connectors?connector=<name>                    the Connectors page on that connector
//   /liberty/connectors?connector=<name>&create=<generator>&q=<text>
//                                                           … its "Create from…" open on that generator, the text searched

/** The values a link to a job's page carries: `?name=value` pairs, when *pathname* is that job's page. */
export function jobValues(pathname: string, search: string, target: string): Record<string, string> {
  if (decodeURIComponent(pathname) !== `/job/${target}`) return {}
  return Object.fromEntries(new URLSearchParams(search))
}

export interface ConnectorLink { connector: string; create?: string; q?: string }
/** What a link to the Connectors page asks to open, or null (another page, or no connector named). */
export function connectorLink(pathname: string, search: string): ConnectorLink | null {
  if (pathname !== '/liberty/connectors') return null
  const p = new URLSearchParams(search)
  const connector = (p.get('connector') ?? '').trim()
  if (!connector) return null
  const create = (p.get('create') ?? '').trim(), q = (p.get('q') ?? '').trim()
  return { connector, ...(create ? { create } : {}), ...(create && q ? { q } : {}) }
}
