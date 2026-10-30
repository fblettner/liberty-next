// Signed in with a temporary password (an account created or reset with one): a new password is
// required before anything else — the server refuses every other call until then. Local accounts
// only; the reply's tokens replace the session's, so the app opens without signing in again.
import { useState } from 'react'
import type { FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useAuth } from '../../auth/AuthContext'
import { ApiError } from '../../api/client'
import { Banner, Button, Field, Input, SpinnerRing, Stack } from '../../common'
import { Brand, Center, Hint, Panel, Tagline } from './shell'

export default function ChangePasswordRequired() {
  const { t } = useTranslation()
  const { user, changePassword, logout } = useAuth()
  const [current, setCurrent] = useState('')
  const [next, setNext] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  async function submit(e: FormEvent) {
    e.preventDefault()
    if (next.length < 8) { setError(t('password.tooShort')); return }
    if (next !== confirm) { setError(t('password.mismatch')); return }
    setBusy(true); setError(null)
    try {
      await changePassword(current, next)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Center>
      <Panel onSubmit={submit}>
        <Brand>{t('password.requiredTitle')}</Brand>
        <Tagline>{t('password.requiredIntro', { user: user?.username ?? '' })}</Tagline>
        <Stack gap={14}>
          <Field label={t('password.current')} htmlFor="pwc-cur">
            <Input id="pwc-cur" type="password" autoFocus autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
          </Field>
          <Field label={t('password.new')} htmlFor="pwc-new">
            <Input id="pwc-new" type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
          </Field>
          <Field label={t('password.confirm')} htmlFor="pwc-conf">
            <Input id="pwc-conf" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          </Field>
          <Hint>{t('password.rule')}</Hint>
          {error && <Banner $tone="error">{error}</Banner>}
          <Button $variant="primary" $size="lg" type="submit" disabled={busy || !current || !next || !confirm}>
            {busy && <SpinnerRing size={14} thickness={2} />}
            {t('password.change')}
          </Button>
          <Button $size="lg" type="button" onClick={logout}>{t('password.signOut')}</Button>
        </Stack>
      </Panel>
    </Center>
  )
}
