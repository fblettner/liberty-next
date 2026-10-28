// The URL prefix the app is served under (a reverse proxy's sub-path, e.g. ``/liberty/``). The
// server writes it into index.html's ``<base href>`` (``[app] base_path``); every absolute app
// path — the API, downloads, the socket, the router — is resolved against it.

/** The prefix from a ``<base href>`` (or none): always starts and ends with ``/``. */
export function basePathOf(baseHref: string | null | undefined): string {
  if (!baseHref) return '/'
  let p: string
  try { p = new URL(baseHref, 'http://x').pathname } catch { return '/' }
  return p.endsWith('/') ? p : `${p}/`
}

/** ``/api/foo`` under the prefix (``/liberty/api/foo``); a relative or absolute URL stays as is. */
export function joinBase(base: string, path: string): string {
  if (!path.startsWith('/') || path.startsWith('//')) return path
  return base + path.slice(1)
}

/** The router's ``basename``: the prefix without its trailing slash (``''`` at the site root). */
export function routerBasename(base: string): string {
  return base === '/' ? '' : base.replace(/\/$/, '')
}
