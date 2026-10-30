import { useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../../auth/AuthContext'
import { useBranding } from '../../branding/BrandingContext'
import { api, ApiError } from '../../api/client'
import { Button, Field, Input, Banner, SpinnerRing, Stack } from '../../common'
import { Accent, Brand, Center, Hint, LinkButton, Panel, Tagline } from './shell'

export default function Login() {
  const { t } = useTranslation()
  const { appName } = useBranding()
  const { user, ready, login, oidcLogin } = useAuth()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // "Forgot password?" — offered only when the server can mail a reset link ([notify] SMTP).
  const [resetAvailable, setResetAvailable] = useState(false)
  const [mode, setMode] = useState<'login' | 'forgot'>('login')
  const [email, setEmail] = useState('')
  const [sent, setSent] = useState(false)
  useEffect(() => {
    api.get<{ auth?: { password_reset?: boolean } }>('/info')
      .then((i) => setResetAvailable(!!i.auth?.password_reset))
      .catch(() => setResetAvailable(false))
  }, [])

  if (ready && user) return <Navigate to={from} replace />

  async function askReset(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await api.post('/auth/forgot-password', { username: username.trim(), email: email.trim() })
      setSent(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  if (mode === 'forgot') {
    return (
      <Center>
        <Panel onSubmit={askReset}>
          <Brand>{t('password.forgotTitle')}</Brand>
          <Tagline>{t('password.forgotIntro')}</Tagline>
          <Stack gap={14}>
            {sent ? (
              <Banner $tone="ok">{t('password.forgotSent')}</Banner>
            ) : (
              <>
                <Field label={t('login.username')} htmlFor="fp-u">
                  <Input id="fp-u" type="text" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
                </Field>
                <Field label={t('password.email')} htmlFor="fp-e">
                  <Input id="fp-e" type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
                </Field>
                {error && <Banner $tone="error">{error}</Banner>}
                <Button $variant="primary" $size="lg" type="submit" disabled={busy || !username.trim() || !email.trim()}>
                  {busy && <SpinnerRing size={14} thickness={2} />}
                  {t('password.forgotSend')}
                </Button>
              </>
            )}
            <LinkButton type="button" onClick={() => { setMode('login'); setSent(false); setError(null) }}>
              {t('password.backToSignIn')}
            </LinkButton>
          </Stack>
        </Panel>
      </Center>
    )
  }

  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Center>
      <Panel onSubmit={submit}>
        <Brand>
          <Accent>{appName || t('app.title')}</Accent>
        </Brand>
        <Tagline>{t('app.subtitle')}</Tagline>
        <Stack gap={14}>
          <Field label={t('login.username')} htmlFor="login-u">
            <Input id="login-u" type="text" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
          </Field>
          <Field label={t('login.password')} htmlFor="login-p">
            <Input id="login-p" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
          </Field>
          {error && <Banner $tone="error">{error}</Banner>}
          <Button $variant="primary" $size="lg" type="submit" disabled={busy || !username || !password}>
            {busy && <SpinnerRing size={14} thickness={2} />}
            {busy ? t('common.signingIn') : t('common.signIn')}
          </Button>
          {resetAvailable && (
            <LinkButton type="button" onClick={() => { setMode('forgot'); setError(null) }}>
              {t('password.forgotLink')}
            </LinkButton>
          )}
          <Button $size="lg" type="button" onClick={oidcLogin}>
            {t('login.oidcButton')}
          </Button>
          <Hint>{t('login.oidcHint')}</Hint>
        </Stack>
      </Panel>
    </Center>
  )
}
