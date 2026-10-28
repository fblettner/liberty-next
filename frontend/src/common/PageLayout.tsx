// The bordered "context bar" header + scrolling content area every page uses.
// (See nomaubl's common/PageLayout.tsx.) Two modes for a page hosted by another page:
// ``embedded`` drops the header and the outer padding (just the content surface, filling its
// parent); ``bare`` keeps the header and renders the children straight under it, each bringing
// its own surface (a host page whose tabs are pages).
import { type ReactNode } from 'react'
import styled from '@emotion/styled'
import { colors, radius, fontSize, glass } from '../theme'

const PageWrap = styled.div`
  display: flex;
  flex-direction: column;
  height: 100%;
  /* As a flex item itself (inside TabHost / Outlet area), default min-height is auto
     (= content size); without this override the wrap would grow to the table's natural row
     count and the page would scroll instead of the table. */
  min-height: 0;
  overflow: hidden;
  padding: 16px;
`

const ContextBar = styled.div`
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  border: 1px solid ${colors.border};
  border-radius: ${radius.lg} ${radius.lg} 0 0;
  background: var(--ghost-bg, ${colors.bg.card});
  ${glass.surface}
  flex-shrink: 0;
`

const ContextIcon = styled.span`
  display: flex;
  align-items: center;
  color: ${colors.blue.main};
  flex-shrink: 0;
`

const ContextText = styled.div`
  flex: 1;
  min-width: 0;
`

const ContextTitle = styled.div`
  font-size: ${fontSize.lg};
  font-weight: 600;
  color: ${colors.text.primary};
  display: flex;
  align-items: baseline;
  gap: 8px;
  flex-wrap: wrap;
`

const ContextDesc = styled.div`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
  margin-top: 2px;
`

const PageContent = styled.div<{ $embedded?: boolean }>`
  flex: 1;
  min-height: 0;
  /* A flex column so a child can use 'flex: 1; min-height: 0' to claim the remaining space
     (e.g. TableView's Stack → DataTable's Wrap → TableScroll all flex-fill, so the data rows
     scroll inside the TanStack table while the filter panel / toolbar above stay put).
     Block children take their natural size by default — pages that don't use the flex chain
     are unaffected. */
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  padding: 18px;
  border: 1px solid ${colors.border};
  border-top: ${({ $embedded }) => ($embedded ? `1px solid ${colors.border}` : 'none')};
  border-radius: ${({ $embedded }) => ($embedded ? radius.lg : `0 0 ${radius.lg} ${radius.lg}`)};
  background: var(--ghost-bg, ${colors.bg.card});
`

export function PageLayout({
  icon,
  title,
  description,
  headerRight,
  children,
  embedded,
  bare,
}: {
  icon?: ReactNode
  title?: ReactNode
  description?: ReactNode
  headerRight?: ReactNode
  children: ReactNode
  /** Rendered inside another page: no header, no outer padding, the usual content surface. */
  embedded?: boolean
  /** Header only: the children go straight under it, each with its own content surface. */
  bare?: boolean
}) {
  if (embedded) return <PageContent $embedded>{children}</PageContent>
  return (
    <PageWrap>
      <ContextBar>
        {icon && <ContextIcon>{icon}</ContextIcon>}
        <ContextText>
          <ContextTitle>{title}</ContextTitle>
          {description && <ContextDesc>{description}</ContextDesc>}
        </ContextText>
        {headerRight}
      </ContextBar>
      {bare ? children : <PageContent>{children}</PageContent>}
    </PageWrap>
  )
}
