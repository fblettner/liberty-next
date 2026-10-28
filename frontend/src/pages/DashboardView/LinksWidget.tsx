// A card of links — a domain's entry points. A `menu` link points at a menu item of the
// dashboard's app: resolved in the workspace's permission-pruned, localized tree, so it shows
// only what the user can open, under the item's own label when the link gives none. A `url`
// link opens in a new tab.
import styled from '@emotion/styled'
import { useNavigate } from 'react-router-dom'
import { ArrowRight, ExternalLink, type LucideIcon } from 'lucide-react'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { iconFor } from '../../components/SidebarMenu'
import { findMenuItem, leafPath } from '../../services/menuPath'
import type { LinksWidgetWire } from '../../types/dashboards'
import { Card, Empty, Sub, Title } from './cardParts'
import { colors, fontSize, fonts, radius } from '../../theme'

const Grid = styled.div<{ $cols: number }>`
  display: grid; grid-template-columns: repeat(${({ $cols }) => $cols}, minmax(0, 1fr)); gap: 8px; overflow: auto; min-height: 0;
`
const Entry = styled.button`
  display: flex; align-items: flex-start; gap: 10px; text-align: left; padding: 9px 10px; min-width: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.primary};
  font-family: ${fonts.sans}; font-size: ${fontSize.base}; cursor: pointer; text-decoration: none;
  &:hover { border-color: ${colors.blue.border}; background: ${colors.bg.dropdown}; }
  & .ic { flex: none; margin-top: 2px; color: ${colors.blue.main}; }
  & .t { font-weight: 600; display: flex; align-items: center; gap: 6px; }
  & .d { font-size: ${fontSize.sm}; color: ${colors.text.muted}; margin-top: 2px; }
  & .arrow { margin-left: auto; flex: none; color: ${colors.text.muted}; margin-top: 3px; }
`

export function LinksWidget({ widget }: { widget: LinksWidgetWire }) {
  const { menus } = useWorkspace()
  const navigate = useNavigate()
  interface Entry { key: string; label: string; description: string | null; Icon: LucideIcon; external: string | null; path: string | null }
  const entries: Entry[] = widget.links.flatMap((link): Entry[] => {
    if (link.url) {
      const Icon = iconFor(link.icon ?? undefined) ?? ExternalLink
      return [{ key: link.url, label: link.label ?? link.url, description: link.description ?? null, Icon, external: link.url, path: null }]
    }
    const item = link.menu ? findMenuItem(menus, widget.app, link.menu) : undefined
    const path = item ? leafPath(item, widget.app) : null
    if (!item || !path) return []           // not visible to this user, or not a leaf
    const Icon = iconFor(link.icon ?? item.icon ?? undefined) ?? ArrowRight
    return [{ key: link.menu as string, label: link.label ?? item.label, description: link.description ?? null, Icon, external: null, path }]
  })
  return (
    <Card>
      {(widget.label || widget.description) && (
        <div>
          {widget.label && <Title>{widget.label}</Title>}
          {widget.description && <Sub>{widget.description}</Sub>}
        </div>
      )}
      {entries.length === 0 ? <Empty>—</Empty> : (
        <Grid $cols={Math.max(1, widget.columns)}>
          {entries.map((e) => (
            <Entry key={e.key} type="button" onClick={() => { if (e.external) window.open(e.external, '_blank', 'noreferrer'); else if (e.path) navigate(e.path) }}>
              <e.Icon className="ic" size={16} />
              <div style={{ minWidth: 0 }}>
                <div className="t">{e.label}{e.external && <ExternalLink size={11} />}</div>
                {e.description && <div className="d">{e.description}</div>}
              </div>
              {!e.external && <ArrowRight className="arrow" size={13} />}
            </Entry>
          ))}
        </Grid>
      )}
    </Card>
  )
}
