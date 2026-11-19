// The OAuth sign-in of the MCP endpoint, seen from the consent page (/oauth/authorize): reading the
// client's authorization request, the "refused" answer, and the request kept across a sign-in that
// leaves the page (SSO). The checks that matter are the server's (liberty/auth/oauth.py).

export interface AuthorizeRequest {
  client_id: string
  redirect_uri: string
  code_challenge: string
  code_challenge_method: string
  state: string | null
}

/** Why the request cannot be answered — a key under `oauth.problem`. */
export type AuthorizeProblem = 'missing' | 'responseType' | 'pkce'

export function parseAuthorizeRequest(search: string): { request: AuthorizeRequest } | { problem: AuthorizeProblem } {
  const q = new URLSearchParams(search)
  const client_id = q.get('client_id') ?? ''
  const redirect_uri = q.get('redirect_uri') ?? ''
  if (!client_id || !redirect_uri) return { problem: 'missing' }
  if (q.get('response_type') !== 'code') return { problem: 'responseType' }
  const code_challenge = q.get('code_challenge') ?? ''
  const code_challenge_method = q.get('code_challenge_method') ?? ''
  if (!code_challenge || code_challenge_method !== 'S256') return { problem: 'pkce' }
  return { request: { client_id, redirect_uri, code_challenge, code_challenge_method, state: q.get('state') } }
}

/** Where a refusal sends the browser: the client's address with `error=access_denied`. Only for an
 *  address the server has confirmed as the client's. */
export function deniedUrl(req: Pick<AuthorizeRequest, 'redirect_uri' | 'state'>): string {
  const url = new URL(req.redirect_uri)
  url.searchParams.set('error', 'access_denied')
  if (req.state !== null) url.searchParams.set('state', req.state)
  return url.toString()
}

const PENDING_KEY = 'liberty.oauth.pending'

/** Keep the consent page's address while the user signs in (an SSO sign-in leaves the application). */
export function keepPendingAuthorize(path: string): void {
  try { sessionStorage.setItem(PENDING_KEY, path) } catch { /* no storage: the password sign-in still returns */ }
}

/** The consent page to return to after a sign-in, once. */
export function takePendingAuthorize(): string | null {
  try {
    const path = sessionStorage.getItem(PENDING_KEY)
    sessionStorage.removeItem(PENDING_KEY)
    return path && path.startsWith('/oauth/authorize') ? path : null
  } catch {
    return null
  }
}
