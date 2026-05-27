// Right-side AI assistant drawer — slides in from the right edge when the operator
// hits the Sparkles icon in the utility bar. Replaces the v1 `/chat` full-page route
// (removed from App.tsx + Sidebar) with an in-context pane the operator can keep open
// while working on a screen. Click-outside or the close × dismisses.
//
// Mounted at the Layout level so the drawer floats above every page in the workspace
// (tab content, framework pages, dialogs are below). Width is fixed at 420px — enough
// for the chat composer + a few markdown bubbles without crowding; on narrow viewports
// it caps at 100vw so it still fits.
import { lazy, Suspense } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import { colors, fontSize, fonts, glass, radius, shadow } from '../theme'
import { Centered } from '../common'

// Lazy-load the Chat page — keeps the drawer's bundle out of the main chunk so the
// 95% of sessions that never open the assistant don't pay for it.
const Chat = lazy(() => import('../pages/Chat'))

const Backdrop = styled.div<{ $open: boolean }>`
  position: fixed; inset: 0; z-index: 998;
  background: ${({ $open }) => ($open ? 'rgba(0, 0, 0, 0.18)' : 'transparent')};
  pointer-events: ${({ $open }) => ($open ? 'auto' : 'none')};
  transition: background 0.18s ease;
`

const Drawer = styled.aside<{ $open: boolean }>`
  position: fixed; top: 0; right: 0; bottom: 0; z-index: 999;
  width: min(420px, 100vw);
  background: var(--bg-modal, ${colors.bg.card});
  border-left: 1px solid ${colors.border};
  box-shadow: ${shadow.lg};
  ${glass.surface}
  display: flex; flex-direction: column;
  transform: translateX(${({ $open }) => ($open ? '0' : '100%')});
  transition: transform 0.22s cubic-bezier(0.22, 0.61, 0.36, 1);
`

const Header = styled.div`
  display: flex; align-items: center; justify-content: space-between;
  padding: 10px 12px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0;
  font-family: ${fonts.sans}; font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary};
`

const CloseBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center;
  width: 26px; height: 26px; padding: 0; border: 0; border-radius: ${radius.sm};
  background: transparent; cursor: pointer; color: ${colors.text.muted};
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`

const Body = styled.div`
  flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden;
  /* The Chat page was built for a full PageLayout — tighten the inherited padding
     so it fits comfortably in a 420px panel without horizontal scroll. */
  & > * { padding-left: 8px; padding-right: 8px; }
`

export interface AiDrawerProps {
  open: boolean
  onClose: () => void
}

export default function AiDrawer({ open, onClose }: AiDrawerProps) {
  const { t } = useTranslation()
  return (
    <>
      <Backdrop $open={open} onClick={onClose} />
      <Drawer $open={open} aria-hidden={!open}>
        <Header>
          <span>{t('nav.assistant', 'AI Assistant')}</span>
          <CloseBtn type="button" onClick={onClose} title={t('common.close', 'Close')}>
            <X size={16} />
          </CloseBtn>
        </Header>
        <Body>
          {/* Mount the Chat component only when the drawer is open so its state (the
              session messages, the SSE stream) lives only while the operator is using
              it. Closing the drawer destroys the conversation — matches the v1 `/chat`
              page behaviour where navigating away discarded state. */}
          {open && (
            <Suspense fallback={<Centered />}>
              <Chat />
            </Suspense>
          )}
        </Body>
      </Drawer>
    </>
  )
}
