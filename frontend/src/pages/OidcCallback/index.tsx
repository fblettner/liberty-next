import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../../auth/AuthContext'
import { Centered } from '../../common'

/**
 * Landing route for the OIDC flow. The backend's /auth/oidc/callback redirects
 * here with the freshly-minted JWTs in the URL fragment
 * (`#access_token=…&refresh_token=…`) when `[oidc] frontend_redirect` points at
 * this path. We stash them via the auth context, then go to the app.
 */
export default function OidcCallback() {
  const { t } = useTranslation()
  const { setTokens } = useAuth()
  const navigate = useNavigate()
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ''))
    const access = hash.get('access_token')
    const refresh = hash.get('refresh_token')
    if (!access || !refresh) {
      setError(t('oidc.noTokens'))
      return
    }
    setTokens({ access_token: access, refresh_token: refresh })
      .then(() => navigate('/', { replace: true }))
      .catch((e) => setError(String(e)))
  }, [setTokens, navigate, t])

  return <Centered error={!!error}>{error ?? t('oidc.completing')}</Centered>
}
