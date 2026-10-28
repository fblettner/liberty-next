// Left navigation rail — open, collapsed to its icons, or open on hover (the icon rail keeps its
// width in the layout and the menu floats over the page while the mouse is on it — nomaubl's auto
// collapse); the mode is persisted to localStorage.
// react-router NavLinks with lucide icons. Adapted from nomaubl's Sidebar.
// When a workspace app is active it leads with that app's menu tree (SidebarMenu),
// then a divider, then the framework links (Connectors / Assistant / Settings).
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { NavLink } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { SlidersHorizontal, Activity, ChevronLeft, ChevronRight, Wand2, PanelLeftDashed } from 'lucide-react'
import { colors, fontSize, fonts, radius, glass } from '../theme'
import { resolveApiPath } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useWorkspace } from '../workspace/WorkspaceContext'
import SidebarMenu from './SidebarMenu'
import WorkspaceSelect from './WorkspaceSelect'

const ScreenAssistantModal = lazy(() => import('../pages/ScreenAssistant'))

const COLLAPSE_KEY = 'liberty.sidebar.collapsed'
const MODE_KEY = 'liberty.sidebar.mode'
type Mode = 'open' | 'collapsed' | 'hover'
/** The saved mode; the older collapsed flag still reads. */
function readMode(): Mode {
  try {
    const m = localStorage.getItem(MODE_KEY)
    if (m === 'open' || m === 'collapsed' || m === 'hover') return m
    return localStorage.getItem(COLLAPSE_KEY) === 'true' ? 'collapsed' : 'open'
  } catch { return 'open' }
}

// Open on hover: the rail holds the icon width in the layout; the navigation floats above it.
const Rail = styled.div`position: relative; flex-shrink: 0; width: 64px; z-index: 60;`

const Nav = styled.nav<{ $collapsed: boolean; $floating?: boolean }>`
  width: ${({ $collapsed }) => ($collapsed ? '56px' : '210px')};
  flex-shrink: 0;
  ${({ $floating, $collapsed }) => ($floating ? `position: absolute; top: 0; bottom: 0; left: 0;${$collapsed ? '' : ` background: ${colors.bg.modal}; box-shadow: 0 12px 40px var(--shadow-strong, rgba(0,0,0,0.35));`}` : '')}
  background: var(--ghost-bg, ${colors.bg.card});
  border: 1px solid ${colors.border};
  border-radius: ${radius.xl};
  margin: 8px 0 8px 8px;
  display: flex;
  flex-direction: column;
  padding: 14px 0 10px;
  overflow: hidden;
  transition: width 0.2s ease;
  ${glass.surface}
  ${glass.specularSm}
`

const Brand = styled.div<{ $collapsed: boolean }>`
  display: flex;
  align-items: center;
  gap: 9px;
  padding: ${({ $collapsed }) => ($collapsed ? '0 0 14px' : '0 16px 14px')};
  justify-content: ${({ $collapsed }) => ($collapsed ? 'center' : 'flex-start')};
  border-bottom: 1px solid ${colors.border};
  margin-bottom: 12px;
`

// The brand icon — same gradient SVG mark we use as the favicon, served from /favicon.svg
// (frontend/public/favicon.svg). The wrapper is just a sized box; the actual mark is the SVG.
const BrandMark = styled.span`
  width: 26px;
  height: 26px;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  & img { width: 22px; height: 22px; display: block; }
`

const BrandName = styled.span`
  font-family: ${fonts.sans};
  font-size: ${fontSize.md};
  font-weight: 600;
  color: ${colors.text.primary};
  white-space: nowrap;
`

const Items = styled.div`
  padding: 0 8px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 2px;
  /* Without this, a long menu's rows get flex-shrunk to fit instead of scrolling,
     which clips their text — keep every row at its natural height and let the rail scroll. */
  & > * {
    flex-shrink: 0;
  }
`

const Item = styled(NavLink)<{ $collapsed: boolean }>`
  display: flex;
  align-items: center;
  gap: 10px;
  border-radius: ${radius.md};
  border: 1px solid transparent;
  font-size: ${fontSize.base};
  font-family: ${fonts.sans};
  color: ${colors.text.secondary};
  padding: ${({ $collapsed }) => ($collapsed ? '8px 0' : '8px 10px')};
  justify-content: ${({ $collapsed }) => ($collapsed ? 'center' : 'flex-start')};
  white-space: nowrap;
  overflow: hidden;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; text-decoration: none; }
  &.active {
    background: ${colors.blue.bg};
    border-color: ${colors.blue.border};
    color: ${colors.text.primary};
    font-weight: 500;
  }
`

// Same look as Item, but a <button> — for framework links that open a MODAL instead of navigating
// to a route (the Screen Assistant). No active state (it isn't a route).
const ItemButton = styled.button<{ $collapsed: boolean }>`
  display: flex; align-items: center; gap: 10px; border-radius: ${radius.md};
  border: 1px solid transparent; background: transparent; cursor: pointer; width: 100%;
  font-size: ${fontSize.base}; font-family: ${fonts.sans}; color: ${colors.text.secondary};
  padding: ${({ $collapsed }) => ($collapsed ? '8px 0' : '8px 10px')};
  justify-content: ${({ $collapsed }) => ($collapsed ? 'center' : 'flex-start')};
  white-space: nowrap; overflow: hidden;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`

const GroupLabel = styled.div`
  font-size: ${fontSize.micro};
  font-weight: 700;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: ${colors.text.muted};
  padding: 0 10px 5px;
  flex-shrink: 0;
`

// Pinned framework-link bar — Connectors / Assistant / Settings stay reachable
// while the menu above scrolls. Only rendered when there's an app menu (else
// the same links live at the top of <Items>).
const FrameworkBar = styled.div`
  padding: 8px 8px 0;
  border-top: 1px solid ${colors.border};
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex-shrink: 0;
`

const Bottom = styled.div`
  padding: 8px 8px 0;
  border-top: 1px solid ${colors.border};
  margin-top: auto;
`

const CollapseBtn = styled.button<{ $collapsed: boolean }>`
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  background: none;
  border: 1px solid transparent;
  border-radius: ${radius.md};
  color: ${colors.text.muted};
  font-size: ${fontSize.sm};
  font-family: ${fonts.mono};
  cursor: pointer;
  padding: ${({ $collapsed }) => ($collapsed ? '7px 0' : '7px 10px')};
  justify-content: ${({ $collapsed }) => ($collapsed ? 'center' : 'flex-start')};
  transition: color 0.15s, border-color 0.15s;
  &:hover { color: ${colors.text.secondary}; border-color: ${colors.border}; }
`

// Connectors / Assistant / Settings / API-docs — the framework links. Rendered
// either at the top of <Items> (no app menu) or in <FrameworkBar> (pinned below
// a scrolling menu) by Sidebar() below.
function FrameworkLinks({
  collapsed, superuser, iconSize, t, onOpenAssistant,
}: {
  collapsed: boolean
  superuser: boolean
  iconSize: number
  t: (key: string) => string
  onOpenAssistant: () => void
}) {
  return (
    <>
      {/* Only Settings lives here (superusers). The standalone Connectors page was removed
          (connectors are managed + queried from Settings now); the AI assistant lives in a
          top-toolbar-toggled right-side drawer; API Docs is a developer-only link at /docs.
          Everything else is in the per-app menu trees from /api/menus. */}
      {superuser && (
        <>
          <ItemButton type="button" $collapsed={collapsed} title={collapsed ? t('nav.assistant') : undefined} onClick={onOpenAssistant}>
            <Wand2 size={iconSize} />
            {!collapsed && t('nav.assistant')}
          </ItemButton>
          <Item to="/monitoring" $collapsed={collapsed} title={collapsed ? t('nav.monitoring') : undefined}>
            <Activity size={iconSize} />
            {!collapsed && t('nav.monitoring')}
          </Item>
          <Item to="/settings" $collapsed={collapsed} title={collapsed ? t('nav.settings') : undefined}>
            <SlidersHorizontal size={iconSize} />
            {!collapsed && t('nav.settings')}
          </Item>
        </>
      )}
    </>
  )
}


export default function Sidebar() {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { currentMenu } = useWorkspace()
  const [mode, setModeState] = useState<Mode>(readMode)
  const setMode = (m: Mode) => {
    setModeState(m)
    try { localStorage.setItem(MODE_KEY, m) } catch { /* ignore */ }
  }
  // open on hover: a short delay each way, so crossing the rail on the way to the page does not
  // open it; it stays open while something inside has the focus (the app switcher's search)
  const navRef = useRef<HTMLElement | null>(null)
  const [hover, setHover] = useState(false)
  const timer = useRef<number | undefined>(undefined)
  const hoverTo = (on: boolean) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      if (!on && navRef.current?.contains(document.activeElement)) return
      setHover(on)
    }, on ? 120 : 250)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])
  const floating = mode === 'hover'
  const collapsed = floating ? !hover : mode === 'collapsed'
  const toggle = () => setMode(mode === 'collapsed' ? 'open' : 'collapsed')
  // a page picked from the floating navigation folds it back at once
  const onPick = (e: React.MouseEvent) => {
    if (floating && (e.target as HTMLElement).closest('a')) { window.clearTimeout(timer.current); setHover(false) }
  }
  const iconSize = 16
  const [assistantOpen, setAssistantOpen] = useState(false)
  const openAssistant = () => setAssistantOpen(true)

  const nav = (
    <Nav ref={navRef} $collapsed={collapsed} $floating={floating} onClickCapture={onPick}
      onMouseEnter={floating ? () => hoverTo(true) : undefined} onMouseLeave={floating ? () => hoverTo(false) : undefined}>
      <Brand $collapsed={collapsed}>
        <BrandMark><img src={resolveApiPath("/favicon.svg")} alt={t('app.title')} /></BrandMark>
        {!collapsed && <BrandName>{t('app.title')}</BrandName>}
      </Brand>

      {/* App switcher — moved here from the top utility pill. Stays mounted even when collapsed
          (it renders null then) so its auto-init effect keeps resolving the current app. */}
      <WorkspaceSelect collapsed={collapsed} />

      <Items>
        {currentMenu && !collapsed
          ? <SidebarMenu menu={currentMenu} />
          : <FrameworkLinks collapsed={collapsed} superuser={!!user?.is_superuser} iconSize={iconSize} t={t} onOpenAssistant={openAssistant} />}
      </Items>
      {currentMenu && !collapsed && (
        <FrameworkBar>
          <GroupLabel>{t('app.title')}</GroupLabel>
          <FrameworkLinks collapsed={false} superuser={!!user?.is_superuser} iconSize={iconSize} t={t} onOpenAssistant={openAssistant} />
        </FrameworkBar>
      )}

      <Bottom>
        {/* open ↔ collapsed; "open on hover" is the second switch (shown while the menu is open) */}
        {!floating && (
          <CollapseBtn $collapsed={collapsed} onClick={toggle} title={collapsed ? t('common.expand') : undefined}>
            {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
            {!collapsed && t('common.collapse')}
          </CollapseBtn>
        )}
        {!collapsed && (
          <CollapseBtn $collapsed={false} onClick={() => setMode(floating ? 'open' : 'hover')} title={t(floating ? 'nav.keepOpenHint' : 'nav.openOnHoverHint')}>
            <PanelLeftDashed size={15} style={{ color: floating ? colors.blue.main : undefined }} />
            {t(floating ? 'nav.keepOpen' : 'nav.openOnHover')}
          </CollapseBtn>
        )}
      </Bottom>

      {/* Superuser-only wizard — a modal so it opens over anything and closes cleanly (X / Esc /
          click-outside) without leaving a workspace tab behind. Lazy so it stays out of the shell chunk. */}
      {assistantOpen && user?.is_superuser && (
        <Suspense fallback={null}>
          <ScreenAssistantModal onClose={() => setAssistantOpen(false)} />
        </Suspense>
      )}
    </Nav>
  )
  return floating ? <Rail>{nav}</Rail> : nav
}
