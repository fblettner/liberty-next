// The bordered "context bar" header + scrolling content area every page uses.
// (See nomaubl's common/PageLayout.tsx.)
import { type ReactNode } from 'react'
import styled from '@emotion/styled'
import { colors, radius, fontSize, glass } from '../theme'

const PageWrap = styled.div`
  display: flex;
  flex-direction: column;
  height: 100%;
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

const PageContent = styled.div`
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 18px;
  border: 1px solid ${colors.border};
  border-top: none;
  border-radius: 0 0 ${radius.lg} ${radius.lg};
  background: var(--ghost-bg, ${colors.bg.card});
`

export function PageLayout({
  icon,
  title,
  description,
  headerRight,
  children,
}: {
  icon?: ReactNode
  title: ReactNode
  description?: ReactNode
  headerRight?: ReactNode
  children: ReactNode
}) {
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
      <PageContent>{children}</PageContent>
    </PageWrap>
  )
}
