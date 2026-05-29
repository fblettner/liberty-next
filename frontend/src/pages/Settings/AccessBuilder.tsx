// Settings → Access — RBAC management (users + roles), superuser-only. The framework enforces
// permissions (Principal.has_permission, allow/deny, deny-wins); this is the management UI.
//
// Roles use a baseline + allow/deny rules model: pick "Full access" (the `*` permission) or "No
// access", then add allow/deny rules. Each rule targets a resource via a type → item cascade
// (Connector / Query / Menu / Dashboard / API / AI). Menu & dashboard rules use the first-class
// menu:<app>:<id> / dashboard:<id> permissions; allowing a menu also grants its target query so
// the menu is usable, denying a menu also denies its target. So you can do "all access but
// disable the Security menu" (Full access + deny menu) or "Security menu only" (No access + allow
// menu).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Users, Shield, Plus, Pencil, X, Check, Ban } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, Checkbox, Field, Input, Overlay, Modal, ModalBody, ModalFooter, PasswordInput, SearchSelect, SpinnerRing, Tag, type SearchSelectOption } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

interface AppUser { username: string; email: string | null; full_name: string | null; is_active: boolean; is_superuser: boolean; roles: string[] }
interface AppRole { name: string; permissions: string[]; description: string | null }
interface MenuItemRaw { id: string; parent?: string | null; label?: string; type?: string | null; connector?: string | null; target?: string | null }
type MenusCatalog = Record<string, { items: MenuItemRaw[] }>

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
const Item = styled.div`
  display: flex; align-items: center; gap: 10px; padding: 9px 11px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; min-width: 0;
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; }
  & .sub { font-size: ${fontSize.micro}; color: ${colors.text.muted}; display: flex; gap: 5px; flex-wrap: wrap; align-items: center; }
`
const IconBtn = styled.button`
  flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 30px;
  border-radius: ${radius.sm}; border: 1px solid transparent; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 5px; align-items: center;`
const PermChip = styled.span<{ $deny?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; padding: 2px 4px 2px 8px; border-radius: ${radius.sm};
  background: ${({ $deny }) => ($deny ? colors.red.bg : colors.green.bg)};
  border: 1px solid ${({ $deny }) => ($deny ? colors.red.border : colors.green.border)};
  color: ${colors.text.primary}; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  & button { border: 0; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; padding: 1px; }
  & button:hover { color: ${colors.red.main}; }
`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean; $tone?: 'allow' | 'deny' }>`
  display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; border: 0; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600;
  background: ${({ $active, $tone }) => (!$active ? 'transparent' : $tone === 'deny' ? colors.red.bg : colors.green.bg)};
  color: ${({ $active, $tone }) => (!$active ? colors.text.muted : $tone === 'deny' ? colors.red.main : colors.green.main)};
  & + & { border-left: 1px solid ${colors.border}; }
`
const AddRow = styled.div`display: flex; gap: 8px; align-items: flex-end; flex-wrap: wrap; margin-top: 8px;`
const Box = styled(Modal)`width: min(620px, 96vw);`
const Header = styled.div`display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0; & .title { font-size: ${fontSize.lg}; font-weight: 700; color: ${colors.text.primary}; }`
const BaselineRow = styled.div`display: flex; gap: 8px; align-items: center;`

type RuleType = 'connector' | 'query' | 'menu' | 'dashboard' | 'api' | 'ai'

// ── permission picker ───────────────────────────────────────────────────────────────────
function PermissionPicker({ value, onChange, menus, dashboardIds }: { value: string[]; onChange: (next: string[]) => void; menus: MenusCatalog; dashboardIds: string[] }) {
  const { t } = useTranslation()
  const { connectors } = useWorkspace()
  const [effect, setEffect] = useState<'allow' | 'deny'>('allow')
  const [type, setType] = useState<RuleType>('menu')
  const [item, setItem] = useState('')

  const hasStar = value.includes('*')
  const setBaseline = (full: boolean) => {
    const rest = value.filter((v) => v !== '*' && v !== '!*')
    onChange(full ? ['*', ...rest] : rest)
  }

  const sqlConns = useMemo(() => (connectors ?? []).filter((c) => c.type === 'sql'), [connectors])
  const apiConns = useMemo(() => (connectors ?? []).filter((c) => c.type === 'api'), [connectors])

  // Resolve a menu leaf's target permission (so allowing/denying a menu also grants/denies data).
  const leafTargetPerm = (app: string, it: MenuItemRaw): string | null => {
    if (!it.type || !it.target) return null
    if (it.type === 'dashboard') return `dashboard:${it.target}`
    if (it.type === 'page') return null
    const conn = it.connector || app
    return `${it.type === 'query' ? 'sql' : 'api'}:${conn}:${it.target}`
  }
  // Descendant leaves of a menu node (itself if it's a leaf) — for folder selection.
  const descendantLeaves = (app: string, id: string): MenuItemRaw[] => {
    const items = menus[app]?.items ?? []
    const out: MenuItemRaw[] = []
    const walk = (nid: string) => {
      const node = items.find((x) => x.id === nid)
      if (node && node.type && node.target) out.push(node)               // a leaf
      for (const ch of items.filter((x) => x.parent === nid)) walk(ch.id)
    }
    walk(id)
    return out
  }

  // The second-dropdown options + the resolver that turns the picked item into permission patterns.
  const { itemOpts, resolve } = useMemo<{ itemOpts: SearchSelectOption[]; resolve: (v: string) => string[] }>(() => {
    if (type === 'connector') {
      return {
        itemOpts: sqlConns.map((c) => ({ value: c.name, label: `${c.name}  (all queries)`, mono: `sql:${c.name}:*` })),
        resolve: (v) => [`sql:${v}:*`],
      }
    }
    if (type === 'query') {
      const opts: SearchSelectOption[] = []
      for (const c of sqlConns) for (const q of c.queries) opts.push({ value: `sql:${c.name}:${q.name}`, label: `${c.name} · ${q.label || q.name}`, mono: `sql:${c.name}:${q.name}` })
      return { itemOpts: opts, resolve: (v) => [v] }
    }
    if (type === 'api') {
      const opts: SearchSelectOption[] = []
      for (const c of apiConns) for (const e of c.endpoints) opts.push({ value: `api:${c.name}:${e.name}`, label: `${c.name} · ${e.label || e.name}`, mono: `api:${c.name}:${e.name}` })
      return { itemOpts: opts, resolve: (v) => [v] }
    }
    if (type === 'dashboard') {
      return { itemOpts: dashboardIds.map((d) => ({ value: d, label: d, mono: `dashboard:${d}` })), resolve: (v) => [`dashboard:${v}`] }
    }
    if (type === 'ai') {
      return { itemOpts: [{ value: 'ai:chat', label: 'AI assistant (ai:chat)', mono: 'ai:chat' }], resolve: (v) => [v] }
    }
    // menu — list every node (folders + leaves), labelled by app; resolve expands to the node's
    // menu: perm + its leaves' menu: + target perms (so the menu shows AND its screens run).
    const opts: SearchSelectOption[] = []
    for (const [app, m] of Object.entries(menus)) {
      for (const it of m.items) opts.push({ value: `${app} ${it.id}`, label: `${app} · ${it.label || it.id}${it.type ? '' : '  (folder)'}`, mono: `menu:${app}:${it.id}` })
    }
    const resolveMenu = (v: string): string[] => {
      const [app, id] = v.split(' ')
      const patterns = new Set<string>([`menu:${app}:${id}`])
      for (const leaf of descendantLeaves(app, id)) {
        patterns.add(`menu:${app}:${leaf.id}`)
        const tp = leafTargetPerm(app, leaf)
        if (tp) patterns.add(tp)
      }
      return [...patterns]
    }
    return { itemOpts: opts, resolve: resolveMenu }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [type, sqlConns, apiConns, menus, dashboardIds])

  const add = () => {
    if (!item) return
    const pref = effect === 'deny' ? '!' : ''
    const next = [...value]
    for (const p of resolve(item)) { const s = pref + p; if (!next.includes(s)) next.push(s) }
    onChange(next)
    setItem('')
  }

  const typeOpts: SearchSelectOption[] = [
    { value: 'menu', label: t('settings.access.tMenu', 'Menu') },
    { value: 'dashboard', label: t('settings.access.tDashboard', 'Dashboard') },
    { value: 'connector', label: t('settings.access.tConnector', 'Connector (all queries)') },
    { value: 'query', label: t('settings.access.tQuery', 'Query') },
    { value: 'api', label: t('settings.access.tApi', 'API endpoint') },
    { value: 'ai', label: t('settings.access.tAi', 'AI assistant') },
  ]

  return (
    <div>
      <BaselineRow>
        <span style={{ fontSize: fontSize.sm, color: colors.text.muted, fontWeight: 600 }}>{t('settings.access.baseline', 'Baseline')}:</span>
        <Seg>
          <SegBtn $active={!hasStar} onClick={() => setBaseline(false)}><Ban size={13} /> {t('settings.access.noAccess', 'No access')}</SegBtn>
          <SegBtn $active={hasStar} $tone="allow" onClick={() => setBaseline(true)}><Check size={13} /> {t('settings.access.fullAccess', 'Full access')}</SegBtn>
        </Seg>
        <span style={{ fontSize: fontSize.micro, color: colors.text.muted }}>{hasStar ? t('settings.access.fullHint', 'everything, minus the denies below') : t('settings.access.noneHint', 'nothing, plus the allows below')}</span>
      </BaselineRow>

      <Chips style={{ marginTop: 10 }}>
        {value.filter((p) => p !== '*' && p !== '!*').map((p) => {
          const deny = p.startsWith('!')
          return <PermChip key={p} $deny={deny}>{deny ? p.slice(1) : p}<button type="button" onClick={() => onChange(value.filter((x) => x !== p))} title={t('common.delete')}><X size={11} /></button></PermChip>
        })}
        {value.filter((p) => p !== '*' && p !== '!*').length === 0 && <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('settings.access.noRules', 'No rules yet.')}</span>}
      </Chips>

      <AddRow>
        <Seg>
          <SegBtn $active={effect === 'allow'} $tone="allow" onClick={() => setEffect('allow')}><Check size={13} /> {t('settings.access.allow', 'Allow')}</SegBtn>
          <SegBtn $active={effect === 'deny'} $tone="deny" onClick={() => setEffect('deny')}><Ban size={13} /> {t('settings.access.deny', 'Deny')}</SegBtn>
        </Seg>
        <div style={{ minWidth: 140 }}>
          <SearchSelect value={type} onChange={(v) => { setType(v as RuleType); setItem('') }} options={typeOpts} />
        </div>
        <div style={{ minWidth: 240, flex: 1 }}>
          <SearchSelect value={item} onChange={setItem} options={itemOpts} placeholder={t('settings.access.pickItem', 'Pick a {{type}}…', { type })} allowCustom={type !== 'connector' && type !== 'dashboard' && type !== 'menu'} />
        </div>
        <Button $size="sm" $variant="ghost" onClick={add} disabled={!item}><Plus size={13} /> {t('settings.access.addRule', 'Add')}</Button>
      </AddRow>
    </div>
  )
}

// ── role editor ─────────────────────────────────────────────────────────────────────────
function RoleModal({ initial, takenNames, menus, dashboardIds, onSaved, onClose }: { initial: AppRole | null; takenNames: string[]; menus: MenusCatalog; dashboardIds: string[]; onSaved: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const isNew = initial == null
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [permissions, setPermissions] = useState<string[]>(initial?.permissions ?? [])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const save = async () => {
    const n = name.trim()
    if (!n) { setError(t('settings.access.errName', 'A role name is required.')); return }
    if (isNew && takenNames.includes(n)) { setError(t('settings.access.errNameTaken', 'That role name is taken.')); return }
    setBusy(true); setError(null)
    try {
      await api.put(`/admin/access/roles/${encodeURIComponent(n)}`, { permissions, description: description.trim() || null })
      onSaved()
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <Overlay onClick={onClose}>
      <Box onClick={(e) => e.stopPropagation()}>
        <Header><Shield size={16} color={colors.blue.main} /><span className="title">{isNew ? t('settings.access.addRole', 'Add role') : `[roles.${initial?.name}]`}</span></Header>
        <ModalBody>
          {error && <Banner $tone="error">{error}</Banner>}
          <Field label={t('settings.access.roleName', 'Role name')}>
            <Input value={name} disabled={!isNew} onChange={(e) => setName(e.target.value)} placeholder="reader" />
          </Field>
          <Field label={t('settings.access.description', 'Description')}>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          <PermissionPicker value={permissions} onChange={setPermissions} menus={menus} dashboardIds={dashboardIds} />
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={() => void save()} disabled={busy}>{busy ? <SpinnerRing size={13} thickness={2} /> : t('common.save')}</Button>
        </ModalFooter>
      </Box>
    </Overlay>
  )
}

// ── user editor ─────────────────────────────────────────────────────────────────────────
function UserModal({ initial, roleNames, onSaved, onClose }: { initial: AppUser | null; roleNames: string[]; onSaved: () => void; onClose: () => void }) {
  const { t } = useTranslation()
  const isNew = initial == null
  const [username, setUsername] = useState(initial?.username ?? '')
  const [fullName, setFullName] = useState(initial?.full_name ?? '')
  const [email, setEmail] = useState(initial?.email ?? '')
  const [roles, setRoles] = useState<string[]>(initial?.roles ?? [])
  const [isActive, setIsActive] = useState(initial?.is_active ?? true)
  const [isSuperuser, setIsSuperuser] = useState(initial?.is_superuser ?? false)
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const roleOpts: SearchSelectOption[] = roleNames.map((r) => ({ value: r, label: r, mono: r }))
  const addRole = (r: string) => { if (r && !roles.includes(r)) setRoles([...roles, r]) }

  const save = async () => {
    const u = username.trim()
    if (!u) { setError(t('settings.access.errUsername', 'A username is required.')); return }
    if (isNew && password.length < 8) { setError(t('profile.passwordTooShort')); return }
    setBusy(true); setError(null)
    try {
      if (isNew) {
        await api.post('/admin/access/users', { username: u, password, email: email.trim() || null, full_name: fullName.trim() || null, roles, is_superuser: isSuperuser, is_active: isActive })
      } else {
        await api.patch(`/admin/access/users/${encodeURIComponent(u)}`, { email: email.trim() || null, full_name: fullName.trim() || null, roles, is_active: isActive, is_superuser: isSuperuser })
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
  const [tab, setTab] = useState<'users' | 'roles'>('users')
  const [users, setUsers] = useState<AppUser[] | null>(null)
  const [roles, setRoles] = useState<AppRole[] | null>(null)
  const [menus, setMenus] = useState<MenusCatalog>({})
  const [dashboardIds, setDashboardIds] = useState<string[]>([])
  const [error, setError] = useState<string | null>(null)
  const [userEdit, setUserEdit] = useState<AppUser | null | undefined>(undefined)
  const [roleEdit, setRoleEdit] = useState<AppRole | null | undefined>(undefined)

  const load = useCallback(() => {
    setError(null)
    Promise.all([
      api.get<{ users: AppUser[] }>('/admin/access/users'),
      api.get<{ roles: AppRole[] }>('/admin/access/roles'),
      api.get<{ menus: MenusCatalog }>('/admin/config/menus/parsed').catch(() => ({ menus: {} as MenusCatalog })),
      api.get<{ dashboards: Record<string, unknown> }>('/admin/config/dashboards/parsed').catch(() => ({ dashboards: {} })),
    ])
      .then(([u, r, m, d]) => { setUsers(u.users); setRoles(r.roles); setMenus(m.menus ?? {}); setDashboardIds(Object.keys(d.dashboards ?? {})) })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }, [t])
  useEffect(load, [load])

  const roleNames = useMemo(() => (roles ?? []).map((r) => r.name), [roles])

  if (error && !users) return <Banner $tone="error">{error}</Banner>
  if (!users || !roles) return <Centered />

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
              <Item key={u.username}>
                <Users size={15} color={colors.text.muted} />
                <span className="text">
                  <span className="name">{u.username}{u.full_name ? ` — ${u.full_name}` : ''}</span>
                  <span className="sub">
                    {!u.is_active && <Tag $tone="red">{t('settings.access.inactive', 'inactive')}</Tag>}
                    {u.is_superuser && <Tag $tone="orange">{t('profile.superuser')}</Tag>}
                    {u.roles.map((r) => <Tag key={r} $tone="blue">{r}</Tag>)}
                    {u.roles.length === 0 && !u.is_superuser && <span>{t('profile.noRoles')}</span>}
                  </span>
                </span>
                <IconBtn onClick={() => setUserEdit(u)} title={t('common.edit', 'Edit')}><Pencil size={14} /></IconBtn>
              </Item>
            ))}
          </List>
        </>
      ) : (
        <>
          <Bar><div style={{ flex: 1 }} /><Button $size="sm" $variant="primary" onClick={() => setRoleEdit(null)}><Plus size={13} /> {t('settings.access.addRole', 'Add role')}</Button></Bar>
          <List>
            {roles.map((r) => (
              <Item key={r.name}>
                <Shield size={15} color={colors.text.muted} />
                <span className="text">
                  <span className="name">{r.name}</span>
                  <span className="sub">
                    {r.description || ''}{r.description ? ' · ' : ''}
                    {r.permissions.includes('*') ? <Tag $tone="green">{t('settings.access.fullAccess', 'Full access')}</Tag> : null}
                    {t('settings.access.permCount', '{{n}} rule(s)', { n: r.permissions.filter((p) => p !== '*').length })}
                  </span>
                </span>
                <IconBtn onClick={() => setRoleEdit(r)} title={t('common.edit', 'Edit')}><Pencil size={14} /></IconBtn>
              </Item>
            ))}
          </List>
        </>
      )}

      {userEdit !== undefined && (
        <UserModal initial={userEdit} roleNames={roleNames} onClose={() => setUserEdit(undefined)} onSaved={() => { setUserEdit(undefined); load() }} />
      )}
      {roleEdit !== undefined && (
        <RoleModal initial={roleEdit} takenNames={roleNames} menus={menus} dashboardIds={dashboardIds} onClose={() => setRoleEdit(undefined)} onSaved={() => { setRoleEdit(undefined); load() }} />
      )}
    </Shell>
  )
}
