// The current app's navigation tree, rendered in the Sidebar. Folders start
// collapsed (these menus can be large), except any on the path to the screen
// you're currently on, which auto-open. Leaves link to the query (TableView) /
// endpoint (HttpRunner) screens. Each folder's
// children are wrapped so their indent + a thin left "rail" come from the wrap
// (rather than per-row padding) — the rail forms one continuous line under the
// parent's chevron, the standard file-tree look.
//
// Permission pruning + label localization happened server-side (GET /api/menus);
// this is pure rendering. A menu node's optional `icon` field is resolved against
// a small lucide whitelist (so the bundle doesn't have to ship all 1500 icons).
import { useMemo, useState, type ComponentType } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import styled from '@emotion/styled'
import {
  ChevronRight,
  ChevronDown,
  // The icon whitelist — extend as more menus reference icons. Pick from
  // https://lucide.dev/icons (the schema's `icon` field is the lucide name).
  Activity,
  AlertCircle,
  BarChart3,
  Bell,
  Briefcase,
  Building,
  Calendar,
  Clock,
  Database,
  FileText,
  Filter,
  Folder,
  Globe,
  Grid3x3,
  Key,
  Layers,
  Link2,
  Lock,
  Mail,
  Network,
  Package,
  Search,
  Settings,
  Shield,
  Table,
  Tag,
  TrendingUp,
  User,
  UserCheck,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import type { AppMenuTree, MenuNode } from '../types/menus'

const ICONS: Record<string, LucideIcon> = {
  activity: Activity, alert: AlertCircle, barchart: BarChart3, bell: Bell,
  briefcase: Briefcase, building: Building, calendar: Calendar, clock: Clock,
  database: Database, file: FileText, filetext: FileText, filter: Filter,
  folder: Folder, globe: Globe, grid: Grid3x3, key: Key,
  layers: Layers, link: Link2, lock: Lock, mail: Mail,
  network: Network, package: Package, search: Search, settings: Settings,
  shield: Shield, table: Table, tag: Tag, trendingup: TrendingUp,
  user: User, usercheck: UserCheck, users: Users,
}
function iconFor(name: string | undefined): LucideIcon | null {
  return name ? (ICONS[name.toLowerCase().replace(/[^a-z0-9]/g, '')] ?? null) : null
}

const INDENT_PX = 14 // ChildrenWrap margin-left — places the rail under the parent's chevron
const CHEVRON_W = 19 // a leaf's left padding past the row base — lines its label up with folder labels

const SectionLabel = styled.div`
  font-size: ${fontSize.micro};
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: ${colors.text.muted};
  padding: 6px 10px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 0;
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
  flex-shrink: 0;
`

const FolderRow = styled.button`
  ${rowBase}
  background: none;
  cursor: pointer;
  font-weight: 600;
  color: ${colors.text.secondary};
  padding: 7px 8px;
  & svg { flex-shrink: 0; opacity: 0.6; }
  &:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
`

const Leaf = styled(NavLink)`
  ${rowBase}
  color: ${colors.text.muted};
  padding: 7px 8px 7px ${CHEVRON_W + 8}px;
  & svg { flex-shrink: 0; opacity: 0.65; }
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; text-decoration: none; }
  &.active {
    background: ${colors.blue.bg};
    border-color: ${colors.blue.border};
    color: ${colors.text.primary};
    font-weight: 500;
    & svg { opacity: 1; }
  }
`

const DeadLeaf = styled.span`
  ${rowBase}
  color: ${colors.text.muted};
  opacity: 0.55;
  padding: 7px 8px 7px ${CHEVRON_W + 8}px;
`

// A folder's children get this wrap — the indent is its margin, the rail its border.
const ChildrenWrap = styled.div`
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-left: ${INDENT_PX}px;
  border-left: 1px solid ${colors.border};
  flex-shrink: 0;
  min-height: 0;
`

const LeafLabel = styled.span`
  overflow: hidden;
  text-overflow: ellipsis;
`

function leafPath(node: MenuNode): string | null {
  if (!node.target) return null
  const t = encodeURIComponent(node.target)
  // Dashboards have no connector segment — the catalog is flat (keyed by id).
  if (node.type === 'dashboard') return `/dashboard/${t}`
  if (!node.connector) return null
  const c = encodeURIComponent(node.connector)
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

function NodeIcon({ node, fallback }: { node: MenuNode; fallback?: ComponentType<{ size?: number }> }) {
  const Icon = iconFor(node.icon) ?? fallback
  return Icon ? <Icon size={14} /> : null
}

function Node({ node, openIds }: { node: MenuNode; openIds: Set<string> }) {
  const [open, setOpen] = useState(() => openIds.has(node.id))  // collapsed by default; the path to the active screen auto-opens
  if (node.items) {
    const Chev = open ? ChevronDown : ChevronRight
    return (
      <>
        <FolderRow onClick={() => setOpen((o) => !o)} title={node.label}>
          <Chev size={13} />
          <NodeIcon node={node} />
          {node.label}
        </FolderRow>
        {open && (
          <ChildrenWrap>
            {node.items.map((c) => <Node key={c.id} node={c} openIds={openIds} />)}
          </ChildrenWrap>
        )}
      </>
    )
  }
  const to = leafPath(node)
  const inner = (
    <>
      <NodeIcon node={node} />
      <LeafLabel>{node.label}</LeafLabel>
    </>
  )
  return to ? (
    <Leaf to={to} title={node.label}>{inner}</Leaf>
  ) : (
    <DeadLeaf title={node.label}>{inner}</DeadLeaf>
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
        <Node key={node.id} node={node} openIds={openIds} />
      ))}
    </>
  )
}
