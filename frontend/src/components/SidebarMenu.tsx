// The current app's navigation tree, rendered in the Sidebar. Folders are
// collapsible — top-level ones open by default, deeper ones closed, and any
// folder on the path to the screen you're currently on auto-opens. Leaves link
// to the query (TableView) / endpoint (HttpRunner) screens. Permission pruning +
// label localization happened server-side (GET /api/menus), so this is pure rendering.
import { useMemo, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import styled from '@emotion/styled'
import { ChevronRight, ChevronDown } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import type { AppMenuTree, MenuNode } from '../types/menus'

const INDENT = 12 // px per nesting level
const CHEVRON_W = 14 // a leaf is indented this much extra so its label lines up with folder labels

const SectionLabel = styled.div`
  font-size: ${fontSize.micro};
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: ${colors.text.muted};
  padding: 6px 10px 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
`

const rowBase = `
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  border: 1px solid transparent;
  border-radius: ${radius.md};
  font-size: ${fontSize.base};
  font-family: ${fonts.sans};
  line-height: 1.3;
  text-align: left;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
`

const FolderRow = styled.button<{ $depth: number }>`
  ${rowBase}
  background: none;
  cursor: pointer;
  font-weight: 600;
  color: ${colors.text.secondary};
  padding: 7px 8px 7px ${({ $depth }) => 8 + $depth * INDENT}px;
  & svg { flex-shrink: 0; opacity: 0.6; }
  &:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
`

const Leaf = styled(NavLink)<{ $depth: number }>`
  ${rowBase}
  display: block; /* not flex — a single text run */
  color: ${colors.text.muted};
  padding: 7px 8px 7px ${({ $depth }) => 8 + $depth * INDENT + CHEVRON_W}px;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; text-decoration: none; }
  &.active { background: ${colors.blue.bg}; border-color: ${colors.blue.border}; color: ${colors.text.primary}; font-weight: 500; }
`

const DeadLeaf = styled.span<{ $depth: number }>`
  ${rowBase}
  display: block;
  color: ${colors.text.muted};
  opacity: 0.6;
  padding: 7px 8px 7px ${({ $depth }) => 8 + $depth * INDENT + CHEVRON_W}px;
`

function leafPath(node: MenuNode): string | null {
  if (!node.connector || !node.target) return null
  const c = encodeURIComponent(node.connector)
  const t = encodeURIComponent(node.target)
  return node.type === 'endpoint' ? `/http/${c}/${t}` : `/sql/${c}/${t}`
}

/** Folder ids on the path to the leaf whose route is `activePath` — so the menu opens to "you are here". */
function ancestorsOfActive(nodes: MenuNode[], activePath: string): Set<string> {
  const out = new Set<string>()
  const walk = (ns: MenuNode[], ancestors: string[]): boolean => {
    for (const n of ns) {
      if (n.items) {
        if (walk(n.items, [...ancestors, n.id])) return true
      } else if (leafPath(n) === activePath) {
        ancestors.forEach((a) => out.add(a))
        return true
      }
    }
    return false
  }
  walk(nodes, [])
  return out
}

function Node({ node, depth, openIds }: { node: MenuNode; depth: number; openIds: Set<string> }) {
  const [open, setOpen] = useState(() => depth === 0 || openIds.has(node.id))
  if (node.items) {
    return (
      <>
        <FolderRow $depth={depth} onClick={() => setOpen((o) => !o)} title={node.label}>
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          {node.label}
        </FolderRow>
        {open && node.items.map((c) => <Node key={c.id} node={c} depth={depth + 1} openIds={openIds} />)}
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
  const { pathname } = useLocation()
  const openIds = useMemo(() => ancestorsOfActive(menu.items, pathname), [menu, pathname])
  if (menu.items.length === 0) return null
  return (
    <>
      <SectionLabel title={menu.label}>{menu.label}</SectionLabel>
      {menu.items.map((node) => (
        <Node key={node.id} node={node} depth={0} openIds={openIds} />
      ))}
    </>
  )
}
