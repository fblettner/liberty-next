// Settings → Access — RBAC management (users + roles), superuser-only. The framework enforces
// permissions (Principal.has_permission, allow/deny, deny-wins); this is the management UI.
//
// Roles are edited in a full-height panel next to the roles list (pages/Settings/Access/
// RoleEditor): a baseline (`*` or nothing), the menu tree with inherit / allow / deny per node,
// the rules beyond menus, and the resulting permission strings. The strings written are the
// same `menu:<app>:<id>` / `sql:…` / `!…` the chip editor wrote, so nothing changes server-side.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Users, Shield, Plus, X } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, Checkbox, Field, Input, Overlay, Modal, ModalBody, ModalFooter, PasswordInput, SearchSelect, SpinnerRing, Tag, type SearchSelectOption } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import RoleEditor, { type AppRole } from './Access/RoleEditor'
import { PermChip } from './Access/OtherAccess'
import { buildMenuForest, type MenusCatalog } from './Access/permissions'
import { attributeFromText, attributeKeys, attributeToText, screenColumnNames } from './Access/scopeRules'
import type { DictionaryDoc, ScreensDoc } from '../../types/config'

interface AppUser { username: string; email: string | null; full_name: string | null; is_active: boolean; is_superuser: boolean; roles: string[]; attributes?: Record<string, string | string[]> | null }

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; height: 100%;`
const Tabs = styled.div`display: flex; gap: 16px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0;`
const TabBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 7px; padding: 10px 4px; margin-bottom: -1px;
  border: none; background: transparent; cursor: pointer; font-size: ${fontSize.md}; font-weight: 600; font-family: ${fonts.sans};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  border-bottom: 2px solid ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  &:hover { color: ${colors.text.primary}; }
`
const Bar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0;`
const List = styled(Card)`flex: 1; min-height: 0; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 6px;`
const Split = styled.div`display: flex; gap: 12px; flex: 1; min-height: 0;`
const Side = styled.div`width: 300px; flex-shrink: 0; display: flex; flex-direction: column; gap: 10px; min-height: 0;`
const Item = styled.div<{ $selected?: boolean }>`
  display: flex; align-items: center; gap: 10px; padding: 9px 11px; border-radius: ${radius.md}; cursor: pointer;
  border: 1px solid ${({ $selected }) => ($selected ? colors.blue.border : colors.border)};
  background: ${({ $selected }) => ($selected ? colors.blue.bg : colors.bg.input)}; min-width: 0;
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; }
  & .sub { font-size: ${fontSize.micro}; color: ${colors.text.muted}; display: flex; gap: 5px; flex-wrap: wrap; align-items: center; }
`
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 5px; align-items: center;`
const Box = styled(Modal)`width: min(620px, 96vw);`
const Header = styled.div`display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0; & .title { font-size: ${fontSize.lg}; font-weight: 700; color: ${colors.text.primary}; }`
const Placeholder = styled(Card)`flex: 1; display: flex; align-items: center; justify-content: center; color: ${colors.text.muted}; font-size: ${fontSize.sm};`

// ── user editor ─────────────────────────────────────────────────────────────────────────
function UserModal({ initial, roleNames, attributeNames, onSaved, onClose }: { initial: AppUser | null; roleNames: string[]; attributeNames: string[]; onSaved: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const isNew = initial == null
  const [username, setUsername] = useState(initial?.username ?? '')
  const [fullName, setFullName] = useState(initial?.full_name ?? '')
  const [email, setEmail] = useState(initial?.email ?? '')
  const [roles, setRoles] = useState<string[]>(initial?.roles ?? [])
  const [isActive, setIsActive] = useState(initial?.is_active ?? true)
  const [isSuperuser, setIsSuperuser] = useState(initial?.is_superuser ?? false)
  const [password, setPassword] = useState('')
  // Data-scope attributes as editable rows (key + "a, b" text); an empty value drops the key on save.
  const [attrs, setAttrs] = useState<{ key: string; text: string }[]>(() => Object.entries(initial?.attributes ?? {}).map(([key, v]) => ({ key, text: attributeToText(v) })))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const roleOpts: SearchSelectOption[] = roleNames.map((r) => ({ value: r, label: r, mono: r }))
  const addRole = (r: string) => { if (r && !roles.includes(r)) setRoles([...roles, r]) }
  const attrOpts: SearchSelectOption[] = attributeNames.filter((a) => !attrs.some((x) => x.key === a)).map((a) => ({ value: a, label: a, mono: `#USER.${a}#` }))
  const attributes = Object.fromEntries(attrs.filter((a) => a.key.trim() && a.text.trim()).map((a) => [a.key.trim(), attributeFromText(a.text)]))

  const save = async () => {
    const u = username.trim()
    if (!u) { setError(t('settings.access.errUsername', 'A username is required.')); return }
    if (isNew && password.length < 8) { setError(t('profile.passwordTooShort')); return }
    setBusy(true); setError(null)
    try {
      if (isNew) {
        await api.post('/admin/access/users', { username: u, password, email: email.trim() || null, full_name: fullName.trim() || null, roles, is_superuser: isSuperuser, is_active: isActive, attributes })
      } else {
        await api.patch(`/admin/access/users/${encodeURIComponent(u)}`, { email: email.trim() || null, full_name: fullName.trim() || null, roles, is_active: isActive, is_superuser: isSuperuser, attributes })
        if (password) await api.post(`/admin/access/users/${encodeURIComponent(u)}/password`, { password })
      }
      onSaved()
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <Overlay onClick={onClose}>
      <Box onClick={(e) => e.stopPropagation()}>
        <Header><Users size={16} color={colors.blue.main} /><span className="title">{isNew ? t('settings.access.addUser', 'Add user') : initial?.username}</span></Header>
        <ModalBody>
          {error && <Banner $tone="error">{error}</Banner>}
          <Field label={t('login.username')}>
            <Input value={username} disabled={!isNew} onChange={(e) => setUsername(e.target.value)} placeholder="jdoe" />
          </Field>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
            <Field label={t('profile.fullName')}><Input value={fullName} onChange={(e) => setFullName(e.target.value)} /></Field>
            <Field label={t('profile.email')}><Input value={email} type="email" onChange={(e) => setEmail(e.target.value)} /></Field>
          </div>
          <Field label={t('profile.roles')}>
            <Chips>
              {roles.map((r) => <PermChip key={r}>{r}<button type="button" onClick={() => setRoles(roles.filter((x) => x !== r))}><X size={11} /></button></PermChip>)}
              <div style={{ minWidth: 200 }}>
                <SearchSelect value="" onChange={addRole} options={roleOpts.filter((o) => !roles.includes(o.value))} placeholder={t('settings.access.addRole', 'Add role…')} />
              </div>
            </Chips>
          </Field>
          <Field label={t('settings.access.attributes', 'Data-scope attributes')}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
              {attrs.map((a, i) => (
                <div key={i} style={{ display: 'grid', gridTemplateColumns: '1fr 2fr auto', gap: 8, alignItems: 'center' }}>
                  <Input value={a.key} placeholder="company" onChange={(e) => setAttrs(attrs.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
                  <Input value={a.text} placeholder="00001, 00050" onChange={(e) => setAttrs(attrs.map((x, j) => (j === i ? { ...x, text: e.target.value } : x)))} />
                  <Button $size="sm" $variant="ghost" onClick={() => setAttrs(attrs.filter((_, j) => j !== i))} title={t('common.delete')}><X size={13} /></Button>
                </div>
              ))}
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <div style={{ minWidth: 220 }}>
                  <SearchSelect value="" onChange={(k) => { if (k.trim()) setAttrs([...attrs, { key: k.trim(), text: '' }]) }} options={attrOpts} allowCustom placeholder={t('settings.access.addAttribute', 'Add an attribute…')} />
                </div>
                <span style={{ fontSize: fontSize.micro, color: colors.text.muted }}>{t('settings.access.attributesHint', 'Read by the roles\' rules (#USER.company#); a comma makes a list.')}</span>
              </div>
            </div>
          </Field>
          <div style={{ display: 'flex', gap: 20 }}>
            <Checkbox checked={isActive} onChange={setIsActive} label={t('settings.access.active', 'Active')} />
            <Checkbox checked={isSuperuser} onChange={setIsSuperuser} label={t('profile.superuser')} />
          </div>
          <Field label={isNew ? t('login.password') : t('profile.newPassword') + ' (' + t('settings.access.optional', 'optional') + ')'}>
            <PasswordInput value={password} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)}
              placeholder={isNew ? '' : t('settings.access.leaveBlankKeep', 'Leave blank to keep current')} />
          </Field>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={() => void save()} disabled={busy}>{busy ? <SpinnerRing size={13} thickness={2} /> : t('common.save')}</Button>
        </ModalFooter>
      </Box>
    </Overlay>
  )
}

export default function AccessBuilder() {
  const { t } = useTranslation()
  const { screens } = useWorkspace()
  const [tab, setTab] = useState<'users' | 'roles'>('users')
  const [users, setUsers] = useState<AppUser[] | null>(null)
  const [roles, setRoles] = useState<AppRole[] | null>(null)
  const [menus, setMenus] = useState<MenusCatalog>({})
  const [dashboardIds, setDashboardIds] = useState<string[]>([])
  const [dictionary, setDictionary] = useState<DictionaryDoc | null>(null)
  const [columnNames, setColumnNames] = useState<Record<string, string[]>>({})
  const [error, setError] = useState<string | null>(null)
  const [userEdit, setUserEdit] = useState<AppUser | null | undefined>(undefined)
  // undefined = nothing open; null = a new role; a name = that role (keyed so the editor remounts on switch)
  const [roleEdit, setRoleEdit] = useState<string | null | undefined>(undefined)

  const load = useCallback(() => {
    setError(null)
    Promise.all([
      api.get<{ users: AppUser[] }>('/admin/access/users'),
      api.get<{ roles: AppRole[] }>('/admin/access/roles'),
      api.get<{ menus: MenusCatalog }>('/admin/config/menus/parsed').catch(() => ({ menus: {} as MenusCatalog })),
      api.get<{ dashboards: Record<string, unknown> }>('/admin/config/dashboards/parsed').catch(() => ({ dashboards: {} })),
      api.get<DictionaryDoc>('/admin/config/dictionary/parsed').catch((): DictionaryDoc | null => null),
      api.get<ScreensDoc>('/admin/config/screens/parsed').catch((): ScreensDoc | null => null),
    ])
      .then(([u, r, m, d, dict, scr]) => {
        setUsers(u.users); setRoles(r.roles); setMenus(m.menus ?? {}); setDashboardIds(Object.keys(d.dashboards ?? {})); setDictionary(dict); setColumnNames(screenColumnNames(scr))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }, [t])
  useEffect(load, [load])

  const roleNames = useMemo(() => (roles ?? []).map((r) => r.name), [roles])
  const attributeNames = useMemo(() => attributeKeys(roles ?? [], users ?? []), [roles, users])
  // The superuser's screen catalog resolves `screen` leaves to their read query (a superuser sees every screen).
  const forest = useMemo(() => buildMenuForest(menus, screens ?? {}), [menus, screens])

  if (error && !users) return <Banner $tone="error">{error}</Banner>
  if (!users || !roles) return <Centered />

  const editing = roleEdit === null ? null : roleEdit !== undefined ? roles.find((r) => r.name === roleEdit) ?? null : undefined

  return (
    <Shell>
      <Tabs>
        <TabBtn $active={tab === 'users'} onClick={() => setTab('users')}><Users size={15} /> {t('settings.access.users', 'Users')}</TabBtn>
        <TabBtn $active={tab === 'roles'} onClick={() => setTab('roles')}><Shield size={15} /> {t('settings.access.roles', 'Roles')}</TabBtn>
      </Tabs>
      {error && <Banner $tone="error">{error}</Banner>}

      {tab === 'users' ? (
        <>
          <Bar><div style={{ flex: 1 }} /><Button $size="sm" $variant="primary" onClick={() => setUserEdit(null)}><Plus size={13} /> {t('settings.access.addUser', 'Add user')}</Button></Bar>
          <List>
            {users.map((u) => (
              <Item key={u.username} onClick={() => setUserEdit(u)}>
                <Users size={15} color={colors.text.muted} />
                <span className="text">
                  <span className="name">{u.username}{u.full_name ? ` — ${u.full_name}` : ''}</span>
                  <span className="sub">
                    {!u.is_active && <Tag $tone="red">{t('settings.access.inactive', 'inactive')}</Tag>}
                    {u.is_superuser && <Tag $tone="orange">{t('profile.superuser')}</Tag>}
                    {u.roles.map((r) => <Tag key={r} $tone="blue">{r}</Tag>)}
                    {u.roles.length === 0 && !u.is_superuser && <span>{t('profile.noRoles')}</span>}
                    {Object.entries(u.attributes ?? {}).map(([k, v]) => <span key={k} style={{ fontFamily: fonts.mono }}>{k}={attributeToText(v)}</span>)}
                  </span>
                </span>
              </Item>
            ))}
          </List>
        </>
      ) : (
        <Split>
          <Side>
            <Bar><div style={{ flex: 1 }} /><Button $size="sm" $variant="primary" onClick={() => setRoleEdit(null)}><Plus size={13} /> {t('settings.access.addRole', 'Add role')}</Button></Bar>
            <List>
              {roles.map((r) => (
                <Item key={r.name} $selected={roleEdit === r.name} onClick={() => setRoleEdit(r.name)}>
                  <Shield size={15} color={colors.text.muted} />
                  <span className="text">
                    <span className="name">{r.name}</span>
                    <span className="sub">
                      {r.description || ''}{r.description ? ' · ' : ''}
                      {r.permissions.includes('*') ? <Tag $tone="green">{t('settings.access.fullAccess', 'Full access')}</Tag> : null}
                      {t('settings.access.permCount', '{{n}} rule(s)', { n: r.permissions.filter((p) => p !== '*').length })}
                    </span>
                  </span>
                </Item>
              ))}
            </List>
          </Side>
          {editing !== undefined ? (
            <RoleEditor key={roleEdit ?? '__new__'} initial={editing} takenNames={roleNames} forest={forest} dashboardIds={dashboardIds} dictionary={dictionary} attributeNames={attributeNames} columnNames={columnNames}
              onCancel={() => setRoleEdit(undefined)} onSaved={(name) => { setRoleEdit(name); load() }} />
          ) : (
            <Placeholder>{t('settings.access.pickRole', 'Pick a role to edit, or add one.')}</Placeholder>
          )}
        </Split>
      )}

      {userEdit !== undefined && (
        <UserModal initial={userEdit} roleNames={roleNames} attributeNames={attributeNames} onClose={() => setUserEdit(undefined)} onSaved={() => { setUserEdit(undefined); load() }} />
      )}
    </Shell>
  )
}
