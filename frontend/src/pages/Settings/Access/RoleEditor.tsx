// The role editor panel (Liberty Next → Administer → Access → Roles): name + description, the baseline, the menu
// tree, the rules beyond menus, and the resulting permission strings for the curious. It edits
// `RoleModel` and writes `buildPermissions(model)` — the same strings the chip editor wrote.
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Ban, Check, Database, KeyRound, Shield } from 'lucide-react'
import { api, ApiError } from '../../../api/client'
import { Banner, Button, Field, Input, SpinnerRing } from '../../../common'
import { colors, fontSize, fonts, radius } from '../../../theme'
import type { DictionaryDoc } from '../../../types/config'
import MenuTree from './MenuTree'
import OtherAccess from './OtherAccess'
import ScopeRulesEditor from './ScopeRulesEditor'
import { buildPermissions, nodeEffects, parsePermissions, type MenuForest, type NodeState, type RoleModel } from './permissions'
import { cleanRule, type ScopeRule } from './scopeRules'

export interface AppRole { name: string; permissions: string[]; description: string | null; scope?: ScopeRule[] | null }

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
const Tabs = styled.div`display: flex; gap: 14px; padding: 0 16px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0;`
const TabBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; padding: 9px 2px; margin-bottom: -1px; border: none; background: transparent; cursor: pointer;
  font-size: ${fontSize.sm}; font-weight: 600; font-family: ${fonts.sans};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  border-bottom: 2px solid ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  &:hover { color: ${colors.text.primary}; }
  & .n { font-weight: 500; color: ${colors.text.muted}; }
`
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
interface Props {
  initial: AppRole | null
  takenNames: string[]
  forest: MenuForest
  dashboardIds: string[]
  dictionary: DictionaryDoc | null
  attributeNames: string[]
  /** connector → result column names seen on its screens (for rules on columns without a dictionary item). */
  columnNames: Record<string, string[]>
  onSaved: (name: string) => void
  onCancel: () => void
}

export default function RoleEditor({ initial, takenNames, forest, dashboardIds, dictionary, attributeNames, columnNames, onSaved, onCancel }: Props) {
  const { t } = useTranslation()
  const isNew = initial == null
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [model, setModel] = useState<RoleModel>(() => parsePermissions(initial?.permissions ?? [], forest))
  const [scope, setScope] = useState<ScopeRule[]>(() => (initial?.scope ?? []).map(cleanRule))
  const [tab, setTab] = useState<'access' | 'scope'>('access')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const permissions = useMemo(() => buildPermissions(model, forest), [model, forest])
  const effects = useMemo(() => nodeEffects(model, forest), [model, forest])
  const scopeDirty = JSON.stringify(scope) !== JSON.stringify((initial?.scope ?? []).map(cleanRule))
  const dirty = isNew || description !== (initial?.description ?? '') || JSON.stringify(permissions) !== JSON.stringify(initial?.permissions ?? []) || scopeDirty

  const setState = (key: string, state: NodeState | null) => setModel((m) => {
    const states = { ...m.states }
    if (state == null) delete states[key]; else states[key] = state
    return { ...m, states }
  })

  const save = async () => {
    const n = name.trim()
    if (!n) { setError(t('settings.access.errName')); return }
    if (isNew && takenNames.includes(n)) { setError(t('settings.access.errNameTaken')); return }
    setBusy(true); setError(null)
    try {
      await api.put(`/admin/access/roles/${encodeURIComponent(n)}`, { permissions, description: description.trim() || null, scope })
      onSaved(n)
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <Panel>
      <Head>
        <Shield size={16} color={colors.blue.main} />
        <span className="title">{isNew ? t('settings.access.addRole') : `[roles.${initial?.name}]`}</span>
        {dirty && !isNew && <span className="dirty">{t('settings.access.unsaved')}</span>}
      </Head>
      <Tabs>
        <TabBtn $active={tab === 'access'} onClick={() => setTab('access')}><KeyRound size={13} /> {t('settings.access.tabAccess')}</TabBtn>
        <TabBtn $active={tab === 'scope'} onClick={() => setTab('scope')}><Database size={13} /> {t('settings.access.tabScope')} <span className="n">{scope.length ? `· ${scope.length}` : ''}</span></TabBtn>
      </Tabs>
      <Body>
        {error && <Banner $tone="error">{error}</Banner>}
        {tab === 'scope' ? (
          <>
            <ScopeRulesEditor value={scope} onChange={setScope} dictionary={dictionary} attributeNames={attributeNames} columnNames={columnNames} />
          </>
        ) : (
        <>
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(160px, 1fr) 2fr', gap: 12 }}>
          <Field label={t('settings.access.roleName')}>
            <Input value={name} disabled={!isNew} onChange={(e) => setName(e.target.value)} placeholder="reader" />
          </Field>
          <Field label={t('settings.access.description')}>
            <Input value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
        </div>

        <Section>
          <div className="h">
            {t('settings.access.baseline')}
            <Seg>
              <SegBtn $active={!model.full} onClick={() => setModel((m) => ({ ...m, full: false }))}><Ban size={13} /> {t('settings.access.noAccess')}</SegBtn>
              <SegBtn $active={model.full} $tone="allow" onClick={() => setModel((m) => ({ ...m, full: true }))}><Check size={13} /> {t('settings.access.fullAccess')}</SegBtn>
            </Seg>
            <span className="hint">{model.full ? t('settings.access.fullHint') : t('settings.access.noneHint')}</span>
          </div>
        </Section>

        <Section>
          <div className="h">{t('settings.access.menus')}</div>
          <MenuTree forest={forest} effects={effects} onSetState={setState} />
        </Section>

        <Section>
          <div className="h">{t('settings.access.other')}</div>
          <OtherAccess value={model.other} onChange={(other) => setModel((m) => ({ ...m, other }))} dashboardIds={dashboardIds} />
        </Section>

        </>
        )}
      </Body>
      <Foot>
        <Button $size="sm" $variant="ghost" onClick={onCancel}>{t('common.cancel')}</Button>
        <Button $size="sm" $variant="primary" onClick={() => void save()} disabled={busy || (!dirty && !isNew)}>{busy ? <SpinnerRing size={13} thickness={2} /> : t('common.save')}</Button>
      </Foot>
    </Panel>
  )
}
