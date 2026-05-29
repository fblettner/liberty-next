// THE scope row for every per-connector Settings editor (Connectors / Dictionary / Menus /
// Screens / Charts / Dashboards). One component, used everywhere — `Scope: <chip> <chip> … [+ Add
// scope]`. The scope is always a connector; picking a chip narrows the editor to it. "Add scope"
// (when wired) is a chip pinned at the right of the chips. Optional `leading`/`right` slots let an
// editor put status text on the left and save-flow buttons on the far right without re-inventing
// the row.
import type { ReactNode } from 'react'
import styled from '@emotion/styled'
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { colors, fontSize, fonts } from '../../theme'

const Bar = styled.div`display: flex; flex-wrap: wrap; gap: 4px; align-items: center; flex-shrink: 0;`
const Label = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-weight: 600; margin-right: 2px;`
const Chip = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & svg { color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)}; }
  &:hover { color: ${colors.text.primary}; }
  &:disabled { opacity: 0.4; cursor: not-allowed; }
`
const Empty = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted};`
const Right = styled.span`margin-left: auto; display: inline-flex; align-items: center; gap: 6px; flex-wrap: wrap;`

export interface ScopeOption { value: string; label: string }

export function ScopeBar({
  scopes, value, onChange, label, emptyHint,
  onAddScope, addScopeTitle, addScopeDisabled,
  leading, right,
}: {
  scopes: ScopeOption[]
  value: string
  onChange: (v: string) => void
  /** Leading label; defaults to "Scope". */
  label?: string
  /** Shown when there are no scopes to pick. */
  emptyHint?: string
  /** When set, renders an "Add scope" chip pinned at the right of the scope chips. */
  onAddScope?: () => void
  addScopeTitle?: string
  addScopeDisabled?: boolean
  /** Optional node rendered before the label (e.g. status text). */
  leading?: ReactNode
  /** Optional node pinned at the far right (e.g. Save / Delete buttons). */
  right?: ReactNode
}) {
  const { t } = useTranslation()
  return (
    <Bar>
      {leading}
      <Label>{label ?? t('settings.scope', 'Scope')}:</Label>
      {scopes.length === 0
        ? <Empty>{emptyHint ?? t('settings.scopeEmpty', '—')}</Empty>
        : scopes.map((s) => (
            <Chip key={s.value || '_'} type="button" $active={s.value === value} onClick={() => onChange(s.value)}>{s.label}</Chip>
          ))}
      {onAddScope && (
        <Chip type="button" disabled={addScopeDisabled} title={addScopeTitle}
          onClick={() => { if (!addScopeDisabled) onAddScope() }}>
          <Plus size={12} /> {t('settings.addScope', 'Add scope')}
        </Chip>
      )}
      {right && <Right>{right}</Right>}
    </Bar>
  )
}
