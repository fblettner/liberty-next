// The current app's navigation tree, rendered in the Sidebar. Folders are
// collapsible (default: expanded — these menus are shallow); leaves link to the
// query (TableView) / endpoint (HttpRunner) screens. Permission pruning + label
// localization happened server-side (GET /api/menus), so this is pure rendering.
import { useState } from 'react'
import { NavLink } from 'react-router-dom'
import styled from '@emotion/styled'
import { ChevronRight, ChevronDown } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import type { AppMenuTree, MenuNode } from '../types/menus'

const SectionLabel = styled.div`
  font-size: ${fontSize.micro};
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: ${colors.text.muted};
  padding: 10px 10px 5px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
`

const FolderRow = styled.button<{ $depth: number }>`
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  border: 1px solid transparent;
  border-radius: ${radius.md};
  background: none;
  cursor: pointer;
  font-size: ${fontSize.base};
  font-family: ${fonts.sans};
  font-weight: 500;
  color: ${colors.text.secondary};
  padding: 7px 8px 7px ${({ $depth }) => 8 + $depth * 12}px;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: background 0.12s, color 0.12s;
  & svg { flex-shrink: 0; opacity: 0.7; }
  &:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
`

const Leaf = styled(NavLink)<{ $depth: number }>`
  display: block;
  border: 1px solid transparent;
  border-radius: ${radius.md};
  font-size: ${fontSize.base};
  font-family: ${fonts.sans};
  color: ${colors.text.secondary};
  padding: 7px 8px 7px ${({ $depth }) => 8 + $depth * 12 + 14}px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; text-decoration: none; }
  &.active { background: ${colors.blue.bg}; border-color: ${colors.blue.border}; color: ${colors.text.primary}; font-weight: 500; }
`

const DeadLeaf = styled.span<{ $depth: number }>`
  display: block;
  font-size: ${fontSize.base};
  font-family: ${fonts.sans};
  color: ${colors.text.muted};
  padding: 7px 8px 7px ${({ $depth }) => 8 + $depth * 12 + 14}px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
`

function leafPath(node: MenuNode): string | null {
  if (!node.connector || !node.target) return null
  const c = encodeURIComponent(node.connector)
  const t = encodeURIComponent(node.target)
  return node.type === 'endpoint' ? `/http/${c}/${t}` : `/sql/${c}/${t}`
}

function Node({ node, depth }: { node: MenuNode; depth: number }) {
  const [open, setOpen] = useState(true) // shallow menus → expanded by default
  if (node.items) {
    return (
      <>
        <FolderRow $depth={depth} onClick={() => setOpen((o) => !o)} title={node.label}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          {node.label}
        </FolderRow>
        {open && node.items.map((c) => <Node key={c.id} node={c} depth={depth + 1} />)}
      </>
    )
  }
  const to = leafPath(node)
  return to ? (
    <Leaf to={to} $depth={depth} title={node.label}>
      {node.label}
    </Leaf>
  ) : (
    <DeadLeaf $depth={depth} title={node.label}>
      {node.label}
    </DeadLeaf>
  )
}

export default function SidebarMenu({ menu }: { menu: AppMenuTree }) {
  if (menu.items.length === 0) return null
  return (
    <>
      <SectionLabel title={menu.label}>{menu.label}</SectionLabel>
      {menu.items.map((node) => (
        <Node key={node.id} node={node} depth={0} />
      ))}
    </>
  )
}
