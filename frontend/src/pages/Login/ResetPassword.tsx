// The page a password-reset mail links to (/reset-password?token=…), reached signed out: choose a
// new password. The link works once and for an hour (the server checks it); then back to sign-in.
import { useState } from 'react'
import type { FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Field, Input, SpinnerRing, Stack } from '../../common'
import { Brand, Center, Hint, Panel, Tagline } from './shell'

export default function ResetPassword() {
  const { t } = useTranslation()
  const [params] = useSearchParams()
  const token = params.get('token') ?? ''
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (next.length < 8) { setError(t('password.tooShort')); return }
    if (next !== confirm) { setError(t('password.mismatch')); return }
    setBusy(true); setError(null)
    try {
      await api.post('/auth/reset-password', { token, new_password: next })
      setDone(true)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Center>
      <Panel onSubmit={submit}>
        <Brand>{t('password.resetTitle')}</Brand>
        {done ? (
          <Stack gap={14}>
            <Banner $tone="ok">{t('password.resetDone')}</Banner>
            <Link to="/login">{t('password.backToSignIn')}</Link>
          </Stack>
        ) : !token ? (
          <Stack gap={14}>
            <Banner $tone="error">{t('password.resetNoToken')}</Banner>
            <Link to="/login">{t('password.backToSignIn')}</Link>
          </Stack>
        ) : (
          <>
            <Tagline>{t('password.resetIntro')}</Tagline>
            <Stack gap={14}>
              <Field label={t('password.new')} htmlFor="rp-new">
                <Input id="rp-new" type="password" autoFocus autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
              </Field>
              <Field label={t('password.confirm')} htmlFor="rp-conf">
                <Input id="rp-conf" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
              </Field>
              <Hint>{t('password.rule')}</Hint>
              {error && <Banner $tone="error">{error}</Banner>}
              <Button $variant="primary" $size="lg" type="submit" disabled={busy || !next || !confirm}>
                {busy && <SpinnerRing size={14} thickness={2} />}
                {t('password.resetButton')}
              </Button>
              <Link to="/login">{t('password.backToSignIn')}</Link>
            </Stack>
          </>
        )}
      </Panel>
    </Center>
  )
}
