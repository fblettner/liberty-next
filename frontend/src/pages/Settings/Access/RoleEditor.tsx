// The role editor panel (Settings → Access → Roles): name + description, the baseline, the menu
// tree, the rules beyond menus, and the resulting permission strings for the curious. It edits
// `RoleModel` and writes `buildPermissions(model)` — the same strings the chip editor wrote.
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Ban, Check, ChevronDown, ChevronRight, Shield } from 'lucide-react'
import { api, ApiError } from '../../../api/client'
import { Banner, Button, Field, Input, SpinnerRing } from '../../../common'
import { colors, fontSize, fonts, radius } from '../../../theme'
import MenuTree from './MenuTree'
import OtherAccess from './OtherAccess'
import { buildPermissions, nodeEffects, parsePermissions, type MenuForest, type NodeState, type RoleModel } from './permissions'

export interface AppRole { name: string; permissions: string[]; description: string | null; scope?: unknown[] }

const Panel = styled.div`
  flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: ${colors.bg.card}; overflow: hidden;
`
const Head = styled.div`
  display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0;
  & .title { font-size: ${fontSize.lg}; font-weight: 700; color: ${colors.text.primary}; font-family: ${fonts.mono}; }
  & .dirty { font-size: ${fontSize.micro}; color: ${colors.orange.main}; font-weight: 600; }
`
const Body = styled.div`flex: 1; min-height: 0; overflow-y: auto; padding: 14px 16px; display: flex; flex-direction: column; gap: 14px;`
const Foot = styled.div`display: flex; justify-content: flex-end; gap: 8px; padding: 10px 16px; border-top: 1px solid ${colors.border}; flex-shrink: 0;`
const Section = styled.div`
  & > .h { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: ${fontSize.sm}; font-weight: 700; color: ${colors.text.primary}; }
  & > .h .hint { font-weight: 500; color: ${colors.text.muted}; }
`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean; $tone?: 'allow' | 'deny' }>`
  display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; border: 0; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600;
  background: ${({ $active, $tone }) => (!$active ? 'transparent' : $tone === 'deny' ? colors.red.bg : colors.green.bg)};
  color: ${({ $active, $tone }) => (!$active ? colors.text.muted : $tone === 'deny' ? colors.red.main : colors.green.main)};
  & + & { border-left: 1px solid ${colors.border}; }
`
const Raw = styled.div`
  & summary { cursor: pointer; font-size: ${fontSize.sm}; font-weight: 700; color: ${colors.text.primary}; display: inline-flex; align-items: center; gap: 6px; list-style: none; }
  & summary::-webkit-details-marker { display: none; }
  & pre { margin: 8px 0 0; padding: 8px 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
    font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; white-space: pre-wrap; word-break: break-all; }
  & pre .deny { color: ${colors.red.main}; }
`

interface Props {
  initial: AppRole | null
  takenNames: string[]
  forest: MenuForest
  dashboardIds: string[]
  onSaved: (name: string) => void
  onCancel: () => void
}

export default function RoleEditor({ initial, takenNames, forest, dashboardIds, onSaved, onCancel }: Props) {
  const { t } = useTranslation()
  const isNew = initial == null
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [model, setModel] = useState<RoleModel>(() => parsePermissions(initial?.permissions ?? [], forest))
  const [rawOpen, setRawOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const permissions = useMemo(() => buildPermissions(model, forest), [model, forest])
  const effects = useMemo(() => nodeEffects(model, forest), [model, forest])
  const dirty = isNew || description !== (initial?.description ?? '') || JSON.stringify(permissions) !== JSON.stringify(initial?.permissions ?? [])

  const setState = (key: string, state: NodeState | null) => setModel((m) => {
    const states = { ...m.states }
    if (state == null) delete states[key]; else states[key] = state
    return { ...m, states }
  })

  const save = async () => {
    const n = name.trim()
    if (!n) { setError(t('settings.access.errName', 'A role name is required.')); return }
    if (isNew && takenNames.includes(n)) { setError(t('settings.access.errNameTaken', 'That role name is taken.')); return }
    setBusy(true); setError(null)
    try {
      // `scope` omitted: the role's data-scope rules are kept as they are.
      await api.put(`/admin/access/roles/${encodeURIComponent(n)}`, { permissions, description: description.trim() || null })
      onSaved(n)
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <Panel>
      <Head>
        <Shield size={16} color={colors.blue.main} />
        <span className="title">{isNew ? t('settings.access.addRole', 'Add role') : `[roles.${initial?.name}]`}</span>
        {dirty && !isNew && <span className="dirty">{t('settings.access.unsaved', 'unsaved changes')}</span>}
      </Head>
      <Body>
        {error && <Banner $tone="error">{error}</Banner>}
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(160px, 1fr) 2fr', gap: 12 }}>
          <Field label={t('settings.access.roleName', 'Role name')}>
            <Input value={name} disabled={!isNew} onChange={(e) => setName(e.target.value)} placeholder="reader" />
          </Field>
          <Field label={t('settings.access.description', 'Description')}>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>

        <Section>
          <div className="h">
            {t('settings.access.baseline', 'Baseline')}
            <Seg>
              <SegBtn $active={!model.full} onClick={() => setModel((m) => ({ ...m, full: false }))}><Ban size={13} /> {t('settings.access.noAccess', 'No access')}</SegBtn>
              <SegBtn $active={model.full} $tone="allow" onClick={() => setModel((m) => ({ ...m, full: true }))}><Check size={13} /> {t('settings.access.fullAccess', 'Full access')}</SegBtn>
            </Seg>
            <span className="hint">{model.full ? t('settings.access.fullHint', 'everything, minus what you deny below') : t('settings.access.noneHint', 'nothing, plus what you allow below')}</span>
          </div>
        </Section>

        <Section>
          <div className="h">{t('settings.access.menus', 'Menus')} <span className="hint">{t('settings.access.menusHint', 'what the sidebar shows — allowing a menu also lets its screens run')}</span></div>
          <MenuTree forest={forest} effects={effects} onSetState={setState} />
        </Section>

        <Section>
          <div className="h">{t('settings.access.other', 'Beyond the menus')} <span className="hint">{t('settings.access.otherHint', 'connectors, queries, endpoints, dashboards, the assistant')}</span></div>
          <OtherAccess value={model.other} onChange={(other) => setModel((m) => ({ ...m, other }))} dashboardIds={dashboardIds} />
        </Section>

        <Raw>
          <details open={rawOpen} onToggle={(e) => setRawOpen((e.currentTarget as HTMLDetailsElement).open)}>
            <summary>{rawOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {t('settings.access.raw', 'Resulting permissions')} <span style={{ fontWeight: 500, color: colors.text.muted }}>({permissions.length})</span></summary>
            <pre>{permissions.length === 0 ? t('settings.access.noRules', 'No rules yet.') : permissions.map((p) => <span key={p} className={p.startsWith('!') ? 'deny' : undefined}>{p}{'\n'}</span>)}</pre>
          </details>
        </Raw>
      </Body>
      <Foot>
        <Button $size="sm" $variant="ghost" onClick={onCancel}>{t('common.cancel', 'Cancel')}</Button>
        <Button $size="sm" $variant="primary" onClick={() => void save()} disabled={busy || (!dirty && !isNew)}>{busy ? <SpinnerRing size={13} thickness={2} /> : t('common.save')}</Button>
      </Foot>
    </Panel>
  )
}
