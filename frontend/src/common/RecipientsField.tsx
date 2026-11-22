// Who a message goes to, picked: a role, a user or an address — the kind first, then the name
// from the roles and users of the installation (typed when they cannot be read, and for an
// address). Each recipient shows as a chip; a line that names no kind — typed in the file, or in
// the plain field this replaces — is marked: the dispatcher would ignore it.
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { api } from '../api/client'
import { SearchSelect, type SearchSelectOption } from './SearchSelect'
import { Tag } from './Tag'
import { RECIPIENT_KINDS, addRecipient, parseRecipient, type RecipientKind } from '../services/recipients'
import { colors, fontSize, fonts, radius } from '../theme'

const Wrap = styled.div`display: flex; flex-wrap: wrap; gap: 5px; align-items: center;`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean }>`
  padding: 5px 10px; border: 0; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')}; color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  & + & { border-left: 1px solid ${colors.border}; }
`
const Remove = styled.button`
  display: inline-flex; align-items: center; background: none; border: none; padding: 0 0 0 4px; cursor: pointer; color: inherit; opacity: 0.7;
  &:hover { opacity: 1; }
`
const Warn = styled.div`font-size: ${fontSize.micro}; color: ${colors.orange.main}; margin-top: 4px;`

// the roles and users of the installation, asked once (who may not read them still types the name)
let _names: Promise<{ users: string[]; roles: string[] }> | null = null
const names = (): Promise<{ users: string[]; roles: string[] }> => {
  _names ??= Promise.all([
    api.get<{ users: { username: string }[] }>('/admin/access/users').then((r) => r.users.map((u) => u.username)).catch(() => []),
    api.get<{ roles: { name: string }[] }>('/admin/access/roles').then((r) => r.roles.map((x) => x.name)).catch(() => []),
  ]).then(([users, roles]) => ({ users, roles }))
  return _names
}

export function RecipientsField({ value, onChange, extra }: {
  value: string[]
  onChange: (next: string[]) => void
  /** Recipients offered beside the users' names ("whoever started the run"): the line and what to call it. */
  extra?: { value: string; label: string }[]
}) {
  const { t } = useTranslation()
  const [kind, setKind] = useState<RecipientKind>('role')
  const [known, setKnown] = useState<{ users: string[]; roles: string[] }>({ users: [], roles: [] })
  useEffect(() => { let on = true; void names().then((n) => { if (on) setKnown(n) }); return () => { on = false } }, [])
  const options: SearchSelectOption[] = kind === 'role' ? known.roles.map((r) => ({ value: r, label: r }))
    : kind === 'user' ? known.users.map((u) => ({ value: u, label: u })) : []
  const lost = value.filter((line) => { const r = parseRecipient(line); return (r.kind === null || !r.name) && !(extra ?? []).some((e) => e.value === line) })
  return (
    <div data-recipients>
      <Wrap>
        {value.map((line) => {
          const r = parseRecipient(line)
          const special = (extra ?? []).find((e) => e.value === line)
          return (
            <Tag key={line} $tone={special ? 'purple' : r.kind === null || !r.name ? 'orange' : r.kind === 'role' ? 'blue' : r.kind === 'user' ? 'green' : 'neutral'} title={line}>
              {special ? special.label : r.kind ? `${t(`recipients.kind.${r.kind}`)} · ${r.name}` : r.name}
              <Remove type="button" aria-label={t('common.remove')} onClick={() => onChange(value.filter((x) => x !== line))}><X size={11} /></Remove>
            </Tag>
          )
        })}
        <Seg>
          {RECIPIENT_KINDS.map((k) => <SegBtn key={k} type="button" $active={kind === k} onClick={() => setKind(k)}>{t(`recipients.kind.${k}`)}</SegBtn>)}
        </Seg>
        <div style={{ minWidth: 220, flex: 1 }}>
          <SearchSelect sort value="" options={options} allowCustom
            placeholder={kind === 'email' ? 'name@example.com' : t('recipients.pick')}
            onChange={(v) => onChange(addRecipient(value, kind, v))} />
        </div>
        {(extra ?? []).filter((e) => !value.includes(e.value)).map((e) => (
          <SegBtn key={e.value} type="button" style={{ border: `1px dashed ${colors.border}`, borderRadius: radius.md }} onClick={() => onChange([...value, e.value])}>+ {e.label}</SegBtn>
        ))}
      </Wrap>
      {lost.length > 0 && <Warn data-recipients-lost>{t('recipients.lost', { list: lost.join(', ') })}</Warn>}
    </div>
  )
}
