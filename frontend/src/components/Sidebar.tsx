// Left navigation rail — open, collapsed to its icons, or open on hover (the icon rail keeps its
// width in the layout and the menu floats over the page while the mouse is on it — nomaubl's auto
// collapse); the mode is persisted to localStorage. On a phone-width window it is a drawer over the
// page instead, opened from the header's button (Layout holds its state).
// Adapted from nomaubl's Sidebar.
// It shows the picked workspace's menu tree (SidebarMenu): an application's, or the Liberty Next
// workspace's (the framework's pages, superusers — picked like an application).
import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, PanelLeftDashed } from 'lucide-react'
import { colors, fontSize, fonts, radius, glass, shadow } from '../theme'
import { resolveApiPath } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { useViewport } from '../common/useViewport'
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

// The open menu's width is the user's: dragged at its right edge, kept (a double click gives the default back).
const WIDTH_KEY = 'liberty.sidebar.width'
const WIDTH_DEFAULT = 210, WIDTH_MIN = 180, WIDTH_MAX = 460
const clampWidth = (w: number) => Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, Math.round(w)))
function readWidth(): number {
  try {
    const w = Number(localStorage.getItem(WIDTH_KEY))
    return w ? clampWidth(w) : WIDTH_DEFAULT
  } catch { return WIDTH_DEFAULT }
}
// The edge that is dragged: a few pixels wide, shown on hover and while it moves.
const Resizer = styled.div<{ $active?: boolean }>`
  position: absolute; top: 12px; bottom: 12px; right: 0; width: 6px; cursor: col-resize; z-index: 2;
  border-radius: 3px; background: ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  &:hover { background: ${colors.blue.border}; }
`

// Open on hover: the rail holds the icon width in the layout; the navigation floats above it.
const Rail = styled.div`position: relative; flex-shrink: 0; width: 64px; z-index: 60;`

// Phone or split window: the navigation is a drawer over the page, opened from the header.
const DrawerBackdrop = styled.div`position: fixed; inset: 0; z-index: 290; background: var(--overlay);`

const Nav = styled.nav<{ $collapsed: boolean; $floating?: boolean; $drawer?: boolean; $open?: boolean; $width: number; $resizing?: boolean }>`
  width: ${({ $collapsed, $width }) => ($collapsed ? '56px' : `${$width}px`)};
  flex-shrink: 0;
  position: relative;
  ${({ $floating, $collapsed }) => ($floating ? `position: absolute; top: 0; bottom: 0; left: 0;${$collapsed ? '' : ` background: ${colors.bg.modal}; box-shadow: 0 12px 40px var(--shadow-strong, rgba(0,0,0,0.35));`}` : '')}
  background: var(--ghost-bg, ${colors.bg.card});
  border: 1px solid ${colors.border};
  border-radius: ${radius.xl};
  margin: 8px 0 8px 8px;
  display: flex;
  flex-direction: column;
  padding: 14px 0 10px;
  overflow: hidden;
  /* no easing while the edge is dragged: the width follows the pointer */
  transition: ${({ $resizing }) => ($resizing ? 'none' : 'width 0.2s ease')};
  ${glass.surface}
  ${glass.specularSm}
  /* The drawer stays mounted while closed (the app switcher inside resolves the current app). */
  ${({ $drawer, $open }) => ($drawer ? `
    position: fixed; top: 0; bottom: 0; left: 0; z-index: 300;
    width: min(280px, calc(100vw - 56px));
    display: ${$open ? 'flex' : 'none'};
    background: ${colors.bg.modal};
    box-shadow: ${shadow.xl};
    transition: none;
  ` : '')}
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

export default function Sidebar({ drawerOpen = false, onDrawerClose }: {
  /** Phone-width window only: the drawer is shown. */
  drawerOpen?: boolean
  onDrawerClose?: () => void
}) {
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
  // a phone-width window: a drawer, always in full. A tablet-width one: the icon rail, whatever the
  // saved mode — the page needs the width — opened over the page by the mouse or by its button (a
  // finger does not hover). The saved mode applies again on a wide window.
  const { compact: drawer, narrow } = useViewport()
  const rail = narrow && !drawer
  const floating = !drawer && (mode === 'hover' || rail)
  const collapsed = drawer ? false : floating ? !hover : mode === 'collapsed'
  const toggle = () => setMode(mode === 'collapsed' ? 'open' : 'collapsed')
  // opened by its button on a tablet: a tap anywhere else folds it back
  useEffect(() => {
    if (!rail || !hover) return
    const away = (e: PointerEvent) => { if (!navRef.current?.contains(e.target as Node)) setHover(false) }
    document.addEventListener('pointerdown', away)
    return () => document.removeEventListener('pointerdown', away)
  }, [rail, hover])
  // a page picked from the floating navigation folds it back at once; from the drawer, closes it
  const onPick = (e: React.MouseEvent) => {
    if (!(e.target as HTMLElement).closest('a')) return
    if (floating) { window.clearTimeout(timer.current); setHover(false) }
    if (drawer) onDrawerClose?.()
  }
  // the open menu's width, dragged at its right edge
  const [width, setWidth] = useState(readWidth)
  const [resizing, setResizing] = useState(false)
  const startResize = (e: React.PointerEvent) => {
    e.preventDefault()
    const from = e.clientX, was = width
    let last = was
    setResizing(true)
    const move = (ev: PointerEvent) => { last = clampWidth(was + ev.clientX - from); setWidth(last) }
    const up = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', up)
      setResizing(false)
      try { localStorage.setItem(WIDTH_KEY, String(last)) } catch { /* ignore */ }
    }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  const resetWidth = () => { setWidth(WIDTH_DEFAULT); try { localStorage.removeItem(WIDTH_KEY) } catch { /* ignore */ } }
  const [assistantOpen, setAssistantOpen] = useState(false)
  const openAssistant = () => { setAssistantOpen(true); if (drawer) onDrawerClose?.() }

  const nav = (
    <Nav ref={navRef} $collapsed={collapsed} $floating={floating} $drawer={drawer} $open={drawerOpen} $width={width} $resizing={resizing} onClickCapture={onPick}
      onMouseEnter={floating ? () => hoverTo(true) : undefined} onMouseLeave={floating ? () => hoverTo(false) : undefined}>
      <Brand $collapsed={collapsed}>
        <BrandMark><img src={resolveApiPath("/favicon.svg")} alt={t('app.title')} /></BrandMark>
        {!collapsed && <BrandName>{t('app.title')}</BrandName>}
      </Brand>

      {/* App switcher — moved here from the top utility pill. Stays mounted even when collapsed
          (it renders null then) so its auto-init effect keeps resolving the current app. */}
      <WorkspaceSelect collapsed={collapsed} />

      {/* The picked workspace's menu: an application's, or Liberty Next's (the framework's pages,
          superusers) — the framework links no longer take room under the applications' menus. */}
      <Items>
        {currentMenu && !collapsed && (
          <SidebarMenu menu={currentMenu} onAction={(a) => { if (a === 'assistant') openAssistant() }} />
        )}
      </Items>

      {/* the drawer has one shape: no mode to switch */}
      {!drawer && <Bottom>
        {/* open ↔ collapsed; "open on hover" is the second switch (shown while the menu is open) */}
        {!floating && (
          <CollapseBtn $collapsed={collapsed} onClick={toggle} title={collapsed ? t('common.expand') : undefined}>
            {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
            {!collapsed && t('common.collapse')}
          </CollapseBtn>
        )}
        {/* tablet width: one button, it opens the menu over the page and folds it back */}
        {rail && (
          <CollapseBtn $collapsed={collapsed} onClick={() => { window.clearTimeout(timer.current); setHover(collapsed) }}
            title={collapsed ? t('common.expand') : undefined}>
            {collapsed ? <ChevronRight size={15} /> : <ChevronLeft size={15} />}
            {!collapsed && t('common.collapse')}
          </CollapseBtn>
        )}
        {!collapsed && !rail && (
          <CollapseBtn $collapsed={false} onClick={() => setMode(floating ? 'open' : 'hover')} title={t(floating ? 'nav.keepOpenHint' : 'nav.openOnHoverHint')}>
            <PanelLeftDashed size={15} style={{ color: floating ? colors.blue.main : undefined }} />
            {t(floating ? 'nav.keepOpen' : 'nav.openOnHover')}
          </CollapseBtn>
        )}
      </Bottom>}

      {/* the open menu is as wide as the user drags it (not the icon rail, not the phone's drawer) */}
      {!collapsed && !drawer && (
        <Resizer $active={resizing} onPointerDown={startResize} onDoubleClick={resetWidth} title={t('nav.resize')} />
      )}

      {/* Superuser-only wizard — a modal so it opens over anything and closes cleanly (X / Esc /
          click-outside) without leaving a workspace tab behind. Lazy so it stays out of the shell chunk. */}
      {assistantOpen && user?.is_superuser && (
        <Suspense fallback={null}>
          <ScreenAssistantModal onClose={() => setAssistantOpen(false)} />
        </Suspense>
      )}
    </Nav>
  )
  if (drawer) return <>{drawerOpen && <DrawerBackdrop onClick={onDrawerClose} />}{nav}</>
  return floating ? <Rail>{nav}</Rail> : nav
}
