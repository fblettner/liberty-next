// The tab bar at the top of the content area — one tab per open `/sql` / `/http` screen
// (see tabs/TabsContext). When no tabs are open it falls back to the "Liberty" workspace
// title (so the bar always has something). Clicking a tab activates it + navigates to its
// route; the × closes it (and navigates to the next tab, or to Connectors if it was the last).
import type { MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Table as TableIcon, Globe, X } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import { useTabs, tabPath, type Tab } from '../tabs/TabsContext'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { findMenuLabel } from '../services/menuLabels'

const Bar = styled.div`
  display: flex; align-items: stretch; gap: 4px; padding: 8px 16px 0; flex-shrink: 0;
  overflow-x: auto; min-height: 40px; scrollbar-width: thin;
  border-bottom: 1px solid ${colors.border};
`
const TitleBlock = styled.div`
  display: flex; align-items: baseline; gap: 10px; padding: 4px 6px 12px;
  & .name { font-size: ${fontSize['2xl']}; font-weight: 700; letter-spacing: -0.3px; line-height: 1; color: ${colors.blue.main}; }
  & .sub { font-size: ${fontSize.base}; color: ${colors.text.muted}; }
`
const TabBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; max-width: 220px; height: 30px; padding: 0 6px 0 10px;
  border: 1px solid ${({ $active }) => ($active ? colors.border : 'transparent')};
  border-bottom: 1px solid ${({ $active }) => ($active ? colors.bg.card : 'transparent')};
  border-radius: ${radius.md} ${radius.md} 0 0; margin-bottom: -1px;
  background: ${({ $active }) => ($active ? colors.bg.card : 'transparent')};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer; white-space: nowrap; flex-shrink: 0;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
  &:hover { color: ${colors.text.primary}; background: ${({ $active }) => ($active ? colors.bg.card : 'var(--hover-subtle)')}; }
  & .label { overflow: hidden; text-overflow: ellipsis; }
  & svg.tic { flex-shrink: 0; opacity: 0.65; }
`
const CloseX = styled.span`
  display: inline-flex; align-items: center; justify-content: center; width: 16px; height: 16px;
  border-radius: 3px; flex-shrink: 0; color: ${colors.text.muted};
  &:hover { background: var(--hover-subtle); color: ${colors.red.main}; }
`

export default function TabStrip() {
  const { t } = useTranslation()
  const { tabs, activeId, setActive, close } = useTabs()
  const { menus } = useWorkspace()
  const navigate = useNavigate()

  if (tabs.length === 0) {
    return (
      <Bar>
        <TitleBlock>
          <span className="name">{t('app.title')}</span>
          <span className="sub">{t('app.subtitle')}</span>
        </TitleBlock>
      </Bar>
    )
  }

  const goTo = (tab: Tab) => { setActive(tab.id); navigate(tabPath(tab)) }
  const onClose = (e: MouseEvent, tab: Tab) => {
    e.stopPropagation()
    if (tab.id === activeId) {
      const remaining = tabs.filter((x) => x.id !== tab.id)
      const i = tabs.findIndex((x) => x.id === tab.id)
      const next = remaining[Math.min(i, remaining.length - 1)] ?? null
      close(tab.id)
      navigate(next ? tabPath(next) : '/')
    } else {
      close(tab.id)
    }
  }

  return (
    <Bar>
      {tabs.map((tab) => {
        const Icon = tab.kind === 'http' ? Globe : TableIcon
        const label = findMenuLabel(menus, tab) ?? tab.target
        return (
          <TabBtn key={tab.id} $active={tab.id === activeId} onClick={() => goTo(tab)} title={`${label} — ${tab.connector}.${tab.target}`}>
            <Icon className="tic" size={13} />
            <span className="label">{label}</span>
            <CloseX onClick={(e) => onClose(e, tab)} title={t('tabs.close')}><X size={12} /></CloseX>
          </TabBtn>
        )
      })}
    </Bar>
  )
}
