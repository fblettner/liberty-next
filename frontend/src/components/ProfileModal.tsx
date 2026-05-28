// User profile + preferences — opened from the utility pill's username button. Two tabs:
//   • Profile  — avatar + name + role chip; username (read-only), full name + email (editable
//                for local accounts), role, settings access. Save → PATCH /auth/profile.
//   • Security — change password (current / new / confirm). Save → POST /auth/change-password.
// OIDC accounts are read-only here (their profile + password live at the identity provider).
// Reads the *live* record from GET /auth/profile so edits show immediately (the JWT claims only
// refresh on next login).
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { User, Shield, X } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { api, ApiError } from '../api/client'
import { Overlay, Modal, ModalBody, ModalFooter, Button, Tag, FieldLabel, Input, PasswordInput, Banner } from '../common'
import { colors, fontSize, fonts, radius } from '../theme'

interface Profile {
  username: string
  full_name: string | null
  email: string | null
  roles: string[]
  is_superuser: boolean
  settings_access: boolean
  provider: string
}

const Box = styled(Modal)`
  width: 520px;
  max-width: 95vw;
`
const Header = styled.div`
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  border-bottom: 1px solid ${colors.border};
  flex-shrink: 0;
`
const Avatar = styled.div`
  width: 56px; height: 56px; flex-shrink: 0;
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: ${colors.blue.bg};
  border: 2px solid ${colors.blue.border};
  color: ${colors.blue.main};
  font-size: ${fontSize['2xl']}; font-weight: 700; font-family: ${fonts.sans};
`
const HeadText = styled.div`
  display: flex; align-items: center; gap: 10px; min-width: 0; flex: 1;
  & .name { font-size: ${fontSize.xl}; font-weight: 700; color: ${colors.text.primary}; font-family: ${fonts.sans};
            overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`
const CloseBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 34px; height: 34px;
  border-radius: ${radius.md}; border: 1px solid ${colors.border}; background: transparent;
  color: ${colors.text.muted}; cursor: pointer; flex-shrink: 0;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const Tabs = styled.div`
  display: flex; gap: 4px; padding: 0 20px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0;
`
const TabBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 7px; padding: 12px 6px; margin-bottom: -1px;
  border: none; background: transparent; cursor: pointer;
  font-size: ${fontSize.md}; font-weight: 600; font-family: ${fonts.sans};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  border-bottom: 2px solid ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  & + & { margin-left: 16px; }
  &:hover { color: ${colors.text.primary}; }
`
const Field = styled.div`display: flex; flex-direction: column; gap: 5px;`
const ReadRow = styled.div`
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  & .k { font-size: ${fontSize.md}; color: ${colors.text.muted}; font-family: ${fonts.sans}; }
  & .v { font-size: ${fontSize.md}; color: ${colors.text.primary}; font-family: ${fonts.mono}; }
`
const Note = styled.div`
  font-size: ${fontSize.base}; color: ${colors.text.muted}; font-family: ${fonts.sans};
  background: ${colors.bg.input}; border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 10px 12px;
`

export function ProfileModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const { user } = useAuth()
  const [tab, setTab] = useState<'profile' | 'security'>('profile')
  const [profile, setProfile] = useState<Profile | null>(null)
  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  // Security tab
  const [curPw, setCurPw] = useState('')
  const [newPw, setNewPw] = useState('')
  const [confirmPw, setConfirmPw] = useState('')

  const load = useCallback(async () => {
    try {
      const p = await api.get<Profile>('/auth/profile')
      setProfile(p)
      setFullName(p.full_name ?? '')
      setEmail(p.email ?? '')
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }, [])
  useEffect(() => { void load() }, [load])

  const isLocal = (profile?.provider ?? user?.provider) === 'local'
  const displayName = (profile?.full_name || profile?.username || user?.username || '').trim()
  const initial = (displayName || '?').charAt(0).toUpperCase()
  const roleChip = profile?.roles?.[0] ?? (profile?.is_superuser ? t('profile.superuser') : null)

  const switchTab = (next: 'profile' | 'security') => { setTab(next); setError(null); setStatus(null) }

  const saveProfile = async () => {
    setBusy(true); setError(null); setStatus(null)
    try {
      const p = await api.patch<Profile>('/auth/profile', { full_name: fullName, email })
      setProfile(p); setFullName(p.full_name ?? ''); setEmail(p.email ?? '')
      setStatus(t('profile.profileSaved'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const changePassword = async () => {
    setError(null); setStatus(null)
    if (newPw.length < 8) { setError(t('profile.passwordTooShort')); return }
    if (newPw !== confirmPw) { setError(t('profile.passwordMismatch')); return }
    setBusy(true)
    try {
      await api.post('/auth/change-password', { current_password: curPw, new_password: newPw })
      setCurPw(''); setNewPw(''); setConfirmPw('')
      setStatus(t('profile.passwordChanged'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const canSave = tab === 'profile'
    ? isLocal && !busy
    : isLocal && !busy && curPw.length > 0 && newPw.length > 0 && confirmPw.length > 0
  const onSave = () => { void (tab === 'profile' ? saveProfile() : changePassword()) }

  return (
    <Overlay onClick={onClose}>
      <Box onClick={(e) => e.stopPropagation()}>
        <Header>
          <Avatar>{initial}</Avatar>
          <HeadText>
            <span className="name">{displayName || user?.username}</span>
            {roleChip && <Tag $tone="blue">{roleChip}</Tag>}
          </HeadText>
          <CloseBtn onClick={onClose} title={t('common.close')} aria-label={t('common.close')}><X size={16} /></CloseBtn>
        </Header>

        <Tabs>
          <TabBtn $active={tab === 'profile'} onClick={() => switchTab('profile')}>
            <User size={15} /> {t('profile.tabProfile')}
          </TabBtn>
          <TabBtn $active={tab === 'security'} onClick={() => switchTab('security')}>
            <Shield size={15} /> {t('profile.tabSecurity')}
          </TabBtn>
        </Tabs>

        <ModalBody>
          {error && <Banner $tone="error">{error}</Banner>}
          {status && <Banner $tone="ok">{status}</Banner>}

          {tab === 'profile' ? (
            <>
              <ReadRow><span className="k">{t('login.username')}</span><span className="v">{profile?.username ?? user?.username}</span></ReadRow>
              <Field>
                <FieldLabel>{t('profile.fullName')}</FieldLabel>
                <Input value={fullName} disabled={!isLocal} placeholder={t('profile.fullName')}
                  onChange={(e) => { setFullName(e.target.value); setStatus(null) }} />
              </Field>
              <Field>
                <FieldLabel>{t('profile.email')}</FieldLabel>
                <Input value={email} disabled={!isLocal} placeholder="user@example.com" type="email"
                  onChange={(e) => { setEmail(e.target.value); setStatus(null) }} />
              </Field>
              <ReadRow><span className="k">{t('profile.role')}</span><span className="v">{(profile?.roles ?? []).join(', ') || '—'}</span></ReadRow>
              <ReadRow><span className="k">{t('profile.settingsAccess')}</span><span className="v">{profile?.settings_access ? t('profile.yes') : t('profile.no')}</span></ReadRow>
              {!isLocal && <Note>{t('profile.passwordOidc')}</Note>}
            </>
          ) : (
            <>
              {isLocal ? (
                <>
                  <Field>
                    <FieldLabel>{t('profile.currentPassword')}</FieldLabel>
                    <PasswordInput value={curPw} autoComplete="current-password" onChange={(e) => { setCurPw(e.target.value); setStatus(null) }} />
                  </Field>
                  <Field>
                    <FieldLabel>{t('profile.newPassword')}</FieldLabel>
                    <PasswordInput value={newPw} autoComplete="new-password" onChange={(e) => { setNewPw(e.target.value); setStatus(null) }} />
                  </Field>
                  <Field>
                    <FieldLabel>{t('profile.confirmPassword')}</FieldLabel>
                    <PasswordInput value={confirmPw} autoComplete="new-password"
                      onChange={(e) => { setConfirmPw(e.target.value); setStatus(null) }}
                      onKeyDown={(e) => { if (e.key === 'Enter' && canSave) onSave() }} />
                  </Field>
                </>
              ) : (
                <Note>{t('profile.passwordOidc')}</Note>
              )}
            </>
          )}
        </ModalBody>

        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.close')}</Button>
          {isLocal && <Button $size="sm" $variant="primary" disabled={!canSave} onClick={onSave}>{busy ? t('profile.saving') : t('common.save')}</Button>}
        </ModalFooter>
      </Box>
    </Overlay>
  )
}

export default ProfileModal
