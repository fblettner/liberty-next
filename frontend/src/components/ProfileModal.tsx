// Read-only "who am I" panel — opened from the utility pill's username button.
// Shows the JWT-derived Principal (username/email/provider/roles/permissions).
// There is no self-service password change yet (the backend has no endpoint for
// it — `liberty-admin set-password` is the CLI path); add one here when it does.
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { LogOut } from 'lucide-react'
import { useAuth } from '../auth/AuthContext'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Button, Tag, FieldLabel } from '../common'
import { colors, fontSize, fonts } from '../theme'

const Box = styled(Modal)`
  width: 460px;
`

const Field = styled.div`
  display: flex;
  flex-direction: column;
  gap: 4px;
`

const Value = styled.div`
  font-size: ${fontSize.md};
  color: ${colors.text.primary};
  font-family: ${fonts.sans};
  word-break: break-word;
`

const Tags = styled.div`
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
`

const Empty = styled.span`
  font-size: ${fontSize.base};
  color: ${colors.text.muted};
`

export function ProfileModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const { user, logout } = useAuth()
  const { license } = useWorkspace()
  if (!user) return null

  const noKey = !!license.error && /no license key/i.test(license.error)
  const expires = license.expires_at ? new Date(license.expires_at * 1000).toLocaleDateString() : null

  return (
    <Overlay onClick={onClose}>
      <Box onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('profile.title')}</ModalHeader>
        <ModalBody>
          <Field>
            <FieldLabel>{t('login.username')}</FieldLabel>
            <Value>
              {user.username}
              {user.is_superuser && (
                <>
                  {' '}
                  <Tag $tone="orange">{t('profile.superuser')}</Tag>
                </>
              )}
            </Value>
          </Field>
          <Field>
            <FieldLabel>{t('profile.email')}</FieldLabel>
            <Value>{user.email || <Empty>—</Empty>}</Value>
          </Field>
          <Field>
            <FieldLabel>{t('profile.provider')}</FieldLabel>
            <Value>{user.provider}</Value>
          </Field>
          <Field>
            <FieldLabel>{t('profile.roles')}</FieldLabel>
            {user.roles.length ? (
              <Tags>
                {user.roles.map((r) => (
                  <Tag key={r} $tone="blue">
                    {r}
                  </Tag>
                ))}
              </Tags>
            ) : (
              <Empty>{t('profile.noRoles')}</Empty>
            )}
          </Field>
          <Field>
            <FieldLabel>{t('profile.permissions')}</FieldLabel>
            {user.permissions.length ? (
              <Tags>
                {user.permissions.map((p) => (
                  <Tag key={p} $tone="purple">
                    {p}
                  </Tag>
                ))}
              </Tags>
            ) : (
              <Empty>{t('profile.noPermissions')}</Empty>
            )}
          </Field>
          <Field>
            <FieldLabel>{t('profile.license')}</FieldLabel>
            <Value>
              {license.mode === 'full' ? (
                <>
                  <Tag $tone="green">{t('license.licensed')}</Tag>
                  {license.customer ? ` ${license.customer}` : ''}
                  {license.plan ? ` · ${license.plan}` : ''}
                  {expires ? ` · ${t('license.expires', { date: expires })}` : ''}
                </>
              ) : (
                <>
                  <Tag $tone="orange">{noKey ? t('license.open') : t('license.restricted')}</Tag>
                  {license.error && !noKey ? ` ${license.error}` : ''}
                </>
              )}
            </Value>
          </Field>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="danger" onClick={logout}>
            <LogOut size={13} />
            {t('common.signOut')}
          </Button>
          <Button $size="sm" $variant="ghost" onClick={onClose}>
            {t('common.close')}
          </Button>
        </ModalFooter>
      </Box>
    </Overlay>
  )
}

export default ProfileModal
