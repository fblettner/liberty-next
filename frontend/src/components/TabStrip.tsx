// The tab bar at the top of the content area — one tab per open `/sql` / `/http` screen
// (see tabs/TabsContext). When no tabs are open it falls back to the "Liberty" workspace
// title (so the bar always has something). Clicking a tab activates it + navigates to its
// route; the × closes it (and navigates to the next tab, or to Connectors if it was the last).
import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Table as TableIcon, Globe, Workflow, X, ChevronLeft, ChevronRight, SlidersHorizontal, Activity } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import { useTabs, tabPath, type Tab } from '../tabs/TabsContext'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { useBranding } from '../branding/BrandingContext'
import { findMenuLabelWithApp } from '../services/menuLabels'

const Bar = styled.div`
  display: flex; align-items: stretch; gap: 4px; padding: 8px 16px 0; flex-shrink: 0;
  /* Fixed header height: 8px top padding + the 42px tab row = 50px, which lines the bottom
     divider up with the floating utility pill's bottom edge. The empty (no-tab) state used to
     fall back to a shorter 40px, so its divider sat too high — pin the same 50px here so the
     header height is constant whether or not tabs are open. */
  min-height: 50px; border-bottom: 1px solid ${colors.border};
  /* Keep clear of the fixed utility pill floating over the top-right (Layout publishes its width as
     --utilbar-width). +28px = its 16px right offset + a 12px gap, so the right arrow / close-all and
     the last tab never slide under it. Falls back to a sane reserve before the first measure. */
  padding-right: calc(var(--utilbar-width, 320px) + 28px);
`
// The tabs scroll inside here (the arrows + close-all stay pinned outside). overflow-x matches the
// old single-element bar so the active tab's seamless-bottom trick is unchanged.
const Scroller = styled.div`
  display: flex; align-items: stretch; gap: 4px; flex: 1; min-width: 0;
  /* overflow-y MUST be pinned: setting only overflow-x to auto makes the browser compute
     overflow-y as auto too, and the 42px-tall tabs overflow the strip, so a phantom vertical
     scrollbar capsule appears in the header (it scrolls nothing). Hidden removes it. */
  overflow-x: auto; overflow-y: hidden; scrollbar-width: thin;
`
// Edge buttons (the tab scroll arrows) — compact square toolbar controls, vertically centred in
// the strip and lifted just above its bottom border to line up with the tabs. (Close-all moved to
// the top-right utility pill so it aligns with the other toolbar controls.)
const EdgeBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; flex-shrink: 0;
  width: 30px; height: 30px; align-self: center; margin-bottom: 6px; padding: 0;
  border: 1px solid transparent; border-radius: ${radius.md};
  background: transparent;
  color: ${colors.text.secondary};
  cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
  & svg { display: block; }   /* lift the inline baseline gap that nudged the icon down a pixel */
  &:hover:not(:disabled) {
    background: var(--hover-subtle);
    border-color: ${colors.border};
    color: ${colors.text.primary};
  }
  &:disabled { opacity: 0.35; cursor: default; }
`
const TitleBlock = styled.div`
  /* Centre the title vertically in the now-taller (50px) bar instead of stretching + sitting high.
     align-self center opts out of the Bar's align-items stretch so it keeps its natural height. */
  align-self: center;
  display: flex; align-items: baseline; gap: 10px; padding: 0 6px;
  & .name { font-size: ${fontSize['2xl']}; font-weight: 700; letter-spacing: -0.3px; line-height: 1; color: ${colors.blue.main}; }
  & .sub { font-size: ${fontSize.base}; color: ${colors.text.muted}; }
`
const TabBtn = styled.button<{ $active?: boolean }>`
  /* Two-line layout: app name on top (small/muted), screen label below.
     Single-line fallback when no app context is known (e.g. nomaflow run tabs
     whose target isn't in any menu tree). Slight height bump from 30px → 42px
     to fit both lines without crowding. */
  display: inline-flex; align-items: center; gap: 6px; max-width: 240px; min-height: 42px; padding: 4px 6px 4px 10px;
  border: 1px solid ${({ $active }) => ($active ? colors.border : 'transparent')};
  border-bottom: 1px solid ${({ $active }) => ($active ? colors.bg.card : 'transparent')};
  border-radius: ${radius.md} ${radius.md} 0 0; margin-bottom: -1px;
  background: ${({ $active }) => ($active ? colors.bg.card : 'transparent')};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer; white-space: nowrap; flex-shrink: 0;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
  &:hover { color: ${colors.text.primary}; background: ${({ $active }) => ($active ? colors.bg.card : 'var(--hover-subtle)')}; }
  & .text { display: flex; flex-direction: column; align-items: flex-start; gap: 1px; min-width: 0; }
  & .app { font-size: ${fontSize.micro}; color: ${colors.text.muted}; line-height: 1; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
  & .label { line-height: 1.15; max-width: 200px; overflow: hidden; text-overflow: ellipsis; }
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
  const { menus, connectors, findScreenById } = useWorkspace()
  const { appName } = useBranding()
  const navigate = useNavigate()

  const scrollerRef = useRef<HTMLDivElement>(null)
  const [scroll, setScroll] = useState({ overflow: false, atStart: true, atEnd: false })

  // Whether the tabs overflow the strip (→ show the arrows) + where the scroll sits (→ enable
  // /disable each arrow). Recomputed on scroll, on resize, and whenever the tab set changes.
  const measure = useCallback(() => {
    const el = scrollerRef.current
    if (!el) return
    setScroll({
      overflow: el.scrollWidth > el.clientWidth + 1,
      atStart: el.scrollLeft <= 0,
      atEnd: el.scrollLeft + el.clientWidth >= el.scrollWidth - 1,
    })
  }, [])
  useEffect(() => {
    measure()
    const el = scrollerRef.current
    if (!el) return
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [measure, tabs.length])
  // Keep the active tab in view when it changes — horizontal-only so it never nudges the page.
  useEffect(() => {
    const el = scrollerRef.current
    const act = el?.querySelector<HTMLElement>('[data-active="true"]')
    if (!el || !act) return
    const l = act.offsetLeft
    const r = l + act.offsetWidth
    if (l < el.scrollLeft) el.scrollTo({ left: l - 8, behavior: 'smooth' })
    else if (r > el.scrollLeft + el.clientWidth) el.scrollTo({ left: r - el.clientWidth + 8, behavior: 'smooth' })
  }, [activeId, tabs.length])

  if (tabs.length === 0) {
    return (
      <Bar>
        <TitleBlock>
          <span className="name">{appName || t('app.title')}</span>
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
  const by = (dx: number) => scrollerRef.current?.scrollBy({ left: dx, behavior: 'smooth' })

  return (
    <Bar>
      {scroll.overflow && (
        <EdgeBtn onClick={() => by(-240)} disabled={scroll.atStart}
          title={t('tabs.scrollLeft', 'Scroll left')} aria-label={t('tabs.scrollLeft', 'Scroll left')}>
          <ChevronLeft size={16} />
        </EdgeBtn>
      )}
      <Scroller ref={scrollerRef} onScroll={measure}>
        {tabs.map((tab) => {
          const Icon =
            tab.kind === 'http' ? Globe :
            tab.kind === 'nomaflow_run' ? Workflow :
            tab.kind === 'settings' ? SlidersHorizontal :
            tab.kind === 'monitoring' ? Activity :
            TableIcon
          // Resolve label + owning app via the menu tree so duplicates like "Users" under
          // nomasx1 vs ldap stay distinguishable (app name shown as a small line above).
          // Nomaflow run + Settings tabs aren't menu-tree entries — label them directly with
          // no app top-line.
          let label: string
          let appLabel: string | undefined
          if (tab.kind === 'nomaflow_run') {
            label = t('tabs.nomaflowRun', 'Run {{id}}', { id: tab.target.slice(0, 8) })
          } else if (tab.kind === 'settings') {
            label = t('nav.settings', 'Settings')
          } else if (tab.kind === 'monitoring') {
            label = t('nav.monitoring', 'Monitoring')
          } else if (tab.kind === 'screen') {
            // A screen tab IS identified by its screen, so always show the SCREEN's own label —
            // consistent whether it was opened from a menu or a row-menu drill, and not the menu
            // item's name (which can differ from the screen, and differs per entry point). The
            // menu label is only a fallback for a screen with no label of its own.
            const scr = findScreenById(tab.connector, tab.target)
            const hit = findMenuLabelWithApp(menus, tab as { kind: 'screen'; connector: string; target: string })
            label = scr?.label ?? hit?.label ?? tab.target
            appLabel = scr?.app ?? hit?.appLabel
          } else {
            const hit = findMenuLabelWithApp(menus, tab as { kind: 'sql' | 'http' | 'dashboard'; connector: string; target: string })
            // sql / http: menu label first; but a drill-navigate to a query that isn't in any menu
            // would otherwise show the raw query name — fall back to the query's friendly label.
            const cm = (connectors ?? []).find((c) => c.name === tab.connector)
            const q = tab.kind === 'sql' && cm?.type === 'sql'
              ? cm.queries.find((x) => x.name === tab.target)
              : undefined
            label = hit?.label ?? q?.description ?? q?.label ?? tab.target
            appLabel = hit?.appLabel
          }
          const tip = tab.connector || tab.target ? `${label} — ${tab.connector}.${tab.target}` : label
          return (
            <TabBtn key={tab.id} data-active={tab.id === activeId} $active={tab.id === activeId} onClick={() => goTo(tab)} title={tip}>
              <Icon className="tic" size={13} />
              <span className="text">
                {appLabel && <span className="app">{appLabel}</span>}
                <span className="label">{label}</span>
              </span>
              <CloseX onClick={(e) => onClose(e, tab)} title={t('tabs.close')}><X size={12} /></CloseX>
            </TabBtn>
          )
        })}
      </Scroller>
      {scroll.overflow && (
        <EdgeBtn onClick={() => by(240)} disabled={scroll.atEnd}
          title={t('tabs.scrollRight', 'Scroll right')} aria-label={t('tabs.scrollRight', 'Scroll right')}>
          <ChevronRight size={16} />
        </EdgeBtn>
      )}
    </Bar>
  )
}
