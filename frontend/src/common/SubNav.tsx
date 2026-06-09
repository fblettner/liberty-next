// Shared editor sub-navigation bar — the single, consistent "where am I + go back" strip used by
// every Settings editor that drills into a sub-item. Two deliberate rules (see the editor UX audit):
//
//   1. The BACK button is always FIRST and LEFT (the primary "go up" affordance), labelled with its
//      destination ("← All tables", "← Columns of f0005") so it's self-explanatory.
//   2. The breadcrumb is CONTEXT only ("you are here"); it sits next to the back button. A right
//      slot carries secondary actions (e.g. "Open visual").
//
// The bar is ``position: sticky; top: 0`` with an OPAQUE background, so it never scrolls out of view
// inside a long form — fixing the old right-aligned, scroll-away back button. This is the piece the
// future EditorShell / EditorModalShell compose; adopting it here first lets the two reference
// screens be evaluated before the full frame migration.
import type { ReactNode } from 'react'
import styled from '@emotion/styled'
import { ChevronRight, ArrowLeft, Layers } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'

/** One breadcrumb segment. ``onClick`` present → clickable (jumps back to that level). */
export interface SubNavCrumb {
  label: ReactNode
  onClick?: () => void
}

// Background MUST be opaque (the dropdown tint composited over the base) — a sticky bar with a
// translucent background lets the scrolling form bleed through it. Same trick as DataTable's header.
const Bar = styled.nav`
  position: sticky; top: 0; z-index: 5;
  display: flex; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 14px;
  padding: 7px 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: linear-gradient(${colors.bg.dropdown}, ${colors.bg.dropdown}), ${colors.bg.base};
`
const Back = styled.button`
  display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 10px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border}; background: ${colors.bg.card}; color: ${colors.text.secondary};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer; flex-shrink: 0;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`
const Crumbs = styled.div`display: flex; align-items: center; flex-wrap: wrap; gap: 3px; min-width: 0;`
const Lead = styled.span`color: ${colors.text.muted}; display: inline-flex; margin-right: 2px;`
const Sep = styled(ChevronRight)`flex-shrink: 0; color: ${colors.text.muted}; opacity: 0.4;`
const Crumb = styled.button<{ $current?: boolean }>`
  border: none; background: none; padding: 3px 6px; border-radius: ${radius.sm};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  font-weight: ${({ $current }) => ($current ? 600 : 400)};
  color: ${({ $current }) => ($current ? colors.text.primary : colors.text.muted)};
  cursor: ${({ $current }) => ($current ? 'default' : 'pointer')};
  ${({ $current }) => (!$current ? `&:hover { color: ${colors.blue.main}; background: var(--hover-subtle); }` : '')}
`
const Right = styled.div`margin-left: auto; display: inline-flex; align-items: center; gap: 6px; flex-shrink: 0;`

export function SubNav({ onBack, backLabel, crumbs, lead = true, right }: {
  /** When set, renders the left back button. */
  onBack?: () => void
  /** Destination label shown on the back button (the level you'll land on). */
  backLabel?: ReactNode
  /** Context breadcrumb — last entry is the current level (bold, non-clickable). */
  crumbs?: SubNavCrumb[]
  /** Show the leading "config tree" icon before the crumbs (default true). */
  lead?: boolean
  /** Secondary actions, right-aligned. */
  right?: ReactNode
}) {
  return (
    <Bar aria-label="navigation">
      {onBack && <Back type="button" onClick={onBack}><ArrowLeft size={13} /> {backLabel}</Back>}
      {crumbs && crumbs.length > 0 && (
        <Crumbs>
          {lead && <Lead title="you're editing one item of the config tree"><Layers size={13} /></Lead>}
          {crumbs.map((c, i) => (
            <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, minWidth: 0 }}>
              {i > 0 && <Sep size={13} />}
              <Crumb type="button" $current={i === crumbs.length - 1} disabled={!c.onClick}
                onClick={() => c.onClick?.()}>{c.label}</Crumb>
            </span>
          ))}
        </Crumbs>
      )}
      {right && <Right>{right}</Right>}
    </Bar>
  )
}
