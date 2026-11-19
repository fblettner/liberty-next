import { describe, expect, it } from 'vitest'
import { deniedUrl, parseAuthorizeRequest } from './oauthConsent'

const ok = '?response_type=code&client_id=abc&redirect_uri=https%3A%2F%2Fclaude.ai%2Fapi%2Fmcp%2Fauth_callback&code_challenge=xyz&code_challenge_method=S256&state=s%201&resource=https%3A%2F%2Fliberty%2Fmcp'

describe('parseAuthorizeRequest', () => {
  it('reads a complete request', () => {
    expect(parseAuthorizeRequest(ok)).toEqual({
      request: {
        client_id: 'abc', redirect_uri: 'https://claude.ai/api/mcp/auth_callback',
        code_challenge: 'xyz', code_challenge_method: 'S256', state: 's 1',
      },
    })
  })
  it('keeps an absent state absent', () => {
    const out = parseAuthorizeRequest(ok.replace('&state=s%201', ''))
    expect('request' in out && out.request.state).toBe(null)
  })
  it('names what is wrong', () => {
    expect(parseAuthorizeRequest('')).toEqual({ problem: 'missing' })
    expect(parseAuthorizeRequest(ok.replace('client_id=abc', 'client_id='))).toEqual({ problem: 'missing' })
    expect(parseAuthorizeRequest(ok.replace('response_type=code', 'response_type=token'))).toEqual({ problem: 'responseType' })
    expect(parseAuthorizeRequest(ok.replace('code_challenge_method=S256', 'code_challenge_method=plain'))).toEqual({ problem: 'pkce' })
    expect(parseAuthorizeRequest(ok.replace('code_challenge=xyz&', ''))).toEqual({ problem: 'pkce' })
  })
})

describe('deniedUrl', () => {
  it('answers access_denied with the state, keeping the address own query', () => {
    expect(deniedUrl({ redirect_uri: 'https://claude.ai/cb', state: 's 1' })).toBe('https://claude.ai/cb?error=access_denied&state=s+1')
    expect(deniedUrl({ redirect_uri: 'http://localhost:6274/cb?x=1', state: null })).toBe('http://localhost:6274/cb?x=1&error=access_denied')
  })
})
