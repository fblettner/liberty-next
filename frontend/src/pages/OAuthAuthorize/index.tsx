// The consent page of the OAuth sign-in (/oauth/authorize): an external client — Claude, through
// the MCP endpoint — asks to act as the signed-in user. Signed out, the user signs in first (the
// usual page, password or SSO) and comes back here. Allowing asks the server for the redirect
// carrying the authorization code; refusing answers the client `access_denied`. The server checks
// everything again (liberty/auth/oauth.py): nothing here is trusted.
import { useEffect, useMemo, useState } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../../auth/AuthContext'
import { useBranding } from '../../branding/BrandingContext'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Centered, SpinnerRing, Stack } from '../../common'
import { deniedUrl, keepPendingAuthorize, parseAuthorizeRequest, takePendingAuthorize } from '../../services/oauthConsent'
import { Brand, Center, Hint, LinkButton, Panel, Tagline } from '../Login/shell'

interface ClientInfo {
  client_name: string
  redirect_host: string
  local: boolean
  allowed: boolean
}

export default function OAuthAuthorize() {
  const { t } = useTranslation()
  const { appName } = useBranding()
  const { user, ready, logout } = useAuth()
  const location = useLocation()
  const here = location.pathname + location.search
  const parsed = useMemo(() => parseAuthorizeRequest(location.search), [location.search])
  const request = 'request' in parsed ? parsed.request : null
  const [client, setClient] = useState<ClientInfo | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const signedIn = ready && !!user && !(user.must_change_password && user.provider === 'local')

  useEffect(() => {
    if (!signedIn || !request) return
    takePendingAuthorize()                       // back from the sign-in: nothing left to return to
    let stale = false
    const q = new URLSearchParams({ client_id: request.client_id, redirect_uri: request.redirect_uri })
    api.get<ClientInfo>(`/oauth/client?${q}`)
      .then((info) => { if (!stale) setClient(info) })
      .catch((err) => { if (!stale) setError(err instanceof ApiError ? err.message : String(err)) })
    return () => { stale = true }
  }, [signedIn, request])

  if (!ready) return <Centered />
  if (!user) {
    keepPendingAuthorize(here)
    return <Navigate to="/login" replace state={{ from: here }} />
  }
  // a temporary password: the new one first, as everywhere (the sign-in page asks for it)
  if (!signedIn) return <Navigate to="/" replace />

  const app = appName || t('app.title')

  async function allow() {
    if (!request) return
    setBusy(true)
    setError(null)
    try {
      const out = await api.post<{ redirect_to: string }>('/oauth/approve', request)
      window.location.assign(out.redirect_to)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
      setBusy(false)
    }
  }

  return (
    <Center>
      <Panel onSubmit={(e) => { e.preventDefault(); void allow() }}>
        <Brand>{t('oauth.title')}</Brand>
        {!request ? (
          <Banner $tone="error">{t(`oauth.problem.${'problem' in parsed ? parsed.problem : 'missing'}`)}</Banner>
        ) : !client ? (
          error ? <Banner $tone="error">{error}</Banner> : <SpinnerRing size={18} thickness={2} />
        ) : (
          <>
            <Tagline>{t('oauth.intro', { client: client.client_name, app, user: user.username })}</Tagline>
            <Stack gap={14}>
              <Hint>{t('oauth.what')}</Hint>
              <Hint>{client.local ? t('oauth.returnsLocal') : t('oauth.returnsTo', { host: client.redirect_host })}</Hint>
              {!client.allowed && <Banner $tone="error">{t('oauth.notAllowed')}</Banner>}
              {error && <Banner $tone="error">{error}</Banner>}
              <Button $variant="primary" $size="lg" type="submit" disabled={busy || !client.allowed}>
                {busy && <SpinnerRing size={14} thickness={2} />}
                {t('oauth.allow')}
              </Button>
              <Button $size="lg" type="button" disabled={busy} onClick={() => window.location.assign(deniedUrl(request))}>
                {t('oauth.deny')}
              </Button>
              <LinkButton type="button" onClick={logout}>{t('oauth.switchUser', { user: user.username })}</LinkButton>
            </Stack>
          </>
        )}
      </Panel>
    </Center>
  )
}
