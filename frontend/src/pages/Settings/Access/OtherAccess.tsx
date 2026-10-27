// The rules the menu tree doesn't express: a whole connector, a single query, an API endpoint,
// a dashboard reached outside the menus, the AI assistant, or a raw pattern for experts.
// Shown as chips (green allow / red deny) with the same add row the chip editor had, minus menus.
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Ban, Check, Plus, X } from 'lucide-react'
import { Button, SearchSelect, type SearchSelectOption } from '../../../common'
import { useWorkspace } from '../../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../../theme'

const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 5px; align-items: center;`
export const PermChip = styled.span<{ $deny?: boolean }>`
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
const AddRow = styled.div`display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px;`

type RuleType = 'connector' | 'query' | 'api' | 'dashboard' | 'explorer' | 'ai' | 'pattern'

interface Props { value: string[]; onChange: (next: string[]) => void; dashboardIds: string[] }

export default function OtherAccess({ value, onChange, dashboardIds }: Props) {
  const { t } = useTranslation()
  const { connectors } = useWorkspace()
  const [effect, setEffect] = useState<'allow' | 'deny'>('allow')
  const [type, setType] = useState<RuleType>('connector')
  const [item, setItem] = useState('')

  const sqlConns = useMemo(() => (connectors ?? []).filter((c) => c.type === 'sql'), [connectors])
  const apiConns = useMemo(() => (connectors ?? []).filter((c) => c.type === 'api'), [connectors])

  const itemOpts = useMemo<SearchSelectOption[]>(() => {
    if (type === 'connector') return sqlConns.map((c) => ({ value: `sql:${c.name}:*`, label: `${c.name}  (${t('settings.access.allQueries', 'all queries')})`, mono: `sql:${c.name}:*` }))
    if (type === 'query') return sqlConns.flatMap((c) => c.queries.map((q) => ({ value: `sql:${c.name}:${q.name}`, label: `${c.name} · ${q.label || q.name}`, mono: `sql:${c.name}:${q.name}` })))
    if (type === 'api') return apiConns.flatMap((c) => c.endpoints.map((e) => ({ value: `api:${c.name}:${e.name}`, label: `${c.name} · ${e.label || e.name}`, mono: `api:${c.name}:${e.name}` })))
    if (type === 'dashboard') return dashboardIds.map((d) => ({ value: `dashboard:${d}`, label: d, mono: `dashboard:${d}` }))
    if (type === 'explorer') return sqlConns.map((c) => ({ value: `explorer:${c.name}:*`, label: `${c.name}  (${t('settings.access.allModels', 'all models')})`, mono: `explorer:${c.name}:*` }))
    if (type === 'ai') return [{ value: 'ai:chat', label: t('settings.access.aiChat', 'AI assistant'), mono: 'ai:chat' }]
    return []
  }, [type, sqlConns, apiConns, dashboardIds, t])

  const add = () => {
    const core = item.trim().replace(/^!/, '')
    if (!core) return
    const s = (effect === 'deny' ? '!' : '') + core
    if (!value.includes(s)) onChange([...value, s])
    setItem('')
  }

  const typeOpts: SearchSelectOption[] = [
    { value: 'connector', label: t('settings.access.tConnector', 'Connector (all queries)') },
    { value: 'query', label: t('settings.access.tQuery', 'Query') },
    { value: 'api', label: t('settings.access.tApi', 'API endpoint') },
    { value: 'dashboard', label: t('settings.access.tDashboard', 'Dashboard') },
    { value: 'explorer', label: t('settings.access.tExplorer', 'Data Explorer') },
    { value: 'ai', label: t('settings.access.tAi', 'AI assistant') },
    { value: 'pattern', label: t('settings.access.tPattern', 'Pattern (expert)') },
  ]

  return (
    <div>
      <Chips>
        {value.map((p) => {
          const deny = p.startsWith('!')
          return <PermChip key={p} $deny={deny}>{deny ? p.slice(1) : p}<button type="button" onClick={() => onChange(value.filter((x) => x !== p))} title={t('common.delete')}><X size={11} /></button></PermChip>
        })}
        {value.length === 0 && <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('settings.access.noOther', 'Nothing beyond the menus.')}</span>}
      </Chips>
      <AddRow>
        <Seg>
          <SegBtn $active={effect === 'allow'} $tone="allow" onClick={() => setEffect('allow')}><Check size={13} /> {t('settings.access.allow', 'Allow')}</SegBtn>
          <SegBtn $active={effect === 'deny'} $tone="deny" onClick={() => setEffect('deny')}><Ban size={13} /> {t('settings.access.deny', 'Deny')}</SegBtn>
        </Seg>
        <div style={{ minWidth: 170 }}>
          <SearchSelect value={type} onChange={(v) => { setType(v as RuleType); setItem('') }} options={typeOpts} />
        </div>
        <div style={{ minWidth: 240, flex: 1 }}>
          <SearchSelect value={item} onChange={setItem} options={itemOpts} allowCustom={type === 'pattern' || type === 'query' || type === 'api' || type === 'explorer'}
            placeholder={type === 'pattern' ? 'sql:nomasx1:security_*' : t('settings.access.pickItem', 'Pick…')} />
        </div>
        <Button $size="sm" $variant="ghost" onClick={add} disabled={!item.trim()}><Plus size={13} /> {t('settings.access.addRule', 'Add')}</Button>
      </AddRow>
    </div>
  )
}
