// The ``open_url`` action's URL template (liberty/screens/config.py ``OpenUrlAction``) and the
// ``url`` menu item: ``{NAME}`` takes a value of the firing context (a row column / form field, a
// prompt field, a chain path ``{step.first_row.X}``), URL-encoded; ``#LOGIN_USER#`` and the other
// built-ins too. A placeholder at the very start is the address itself and goes in as it is. Only
// http(s) addresses and paths of this server are opened (``safeUrl``). No React here.

const PLACEHOLDER = /\{([A-Za-z0-9_.$@#]+)\}|#([A-Z_]+)#/g

/** Fill *template* with *resolve(name)* — ``name`` is the placeholder's inside (``AUUSER``,
 *  ``step.first_row.X``) or the built-in with its hashes (``#LOGIN_USER#``). Unknown → empty. */
export function fillUrlTemplate(template: string, resolve: (name: string) => unknown): string {
  return template.replace(PLACEHOLDER, (_m, field: string | undefined, builtin: string | undefined, offset: number) => {
    const v = resolve(field ?? `#${builtin}#`)
    const s = v === null || v === undefined ? '' : String(v).trim()
    return offset === 0 ? s : encodeURIComponent(s)
  })
}

/** The URL when it may be opened — an absolute http(s) address or a path of this server — else
 *  null (``javascript:``, ``data:``, a protocol-relative ``//host`` …). */
export function safeUrl(url: string): string | null {
  const u = url.trim()
  if (/^https?:\/\//i.test(u)) return u
  if (u.startsWith('/') && !u.startsWith('//')) return u
  return null
}
