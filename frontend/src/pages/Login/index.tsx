import { useState } from 'react'
import type { FormEvent } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../../auth/AuthContext'
import { ApiError } from '../../api/client'
import { Button, Field, Input, Banner, SpinnerRing, Stack } from '../../common'
import { colors, fontSize, fonts, radius, glass } from '../../theme'

const Center = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
`

const Panel = styled.form`
  width: 340px;
  background: var(--ghost-bg, ${colors.bg.card});
  border: 1px solid ${colors.border};
  border-radius: ${radius.lg};
  padding: 24px;
  ${glass.surface}
  ${glass.specularSm}
`

const Brand = styled.div`
  font-size: ${fontSize['3xl']};
  font-weight: 700;
  letter-spacing: -0.3px;
  margin-bottom: 4px;
`
const Accent = styled.span`color: ${colors.blue.main};`
const Tagline = styled.div`
  font-size: ${fontSize.base};
  color: ${colors.text.muted};
  margin-bottom: 18px;
`
const Hint = styled.div`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
  line-height: 1.5;
  code { font-family: ${fonts.mono}; }
`

export default function Login() {
  const { t } = useTranslation()
  const { user, ready, login, oidcLogin } = useAuth()
  const location = useLocation()
  const from = (location.state as { from?: string } | null)?.from ?? '/'
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  if (ready && user) return <Navigate to={from} replace />

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
          <Accent>{t('app.title')}</Accent>
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
          <Button $size="lg" type="button" onClick={oidcLogin}>
            {t('login.oidcButton')}
          </Button>
          <Hint>{t('login.oidcHint')}</Hint>
        </Stack>
      </Panel>
    </Center>
  )
}
