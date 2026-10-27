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
import { colors, glass, shadow } from '../theme'
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

const Body = styled.div`
  flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden;
`

export interface AiDrawerProps {
  open: boolean
  onClose: () => void
}

export default function AiDrawer({ open, onClose }: AiDrawerProps) {
  return (
    <>
      <Backdrop $open={open} onClick={onClose} />
      <Drawer $open={open} aria-hidden={!open}>
        <Body>
          {/* The Chat owns the drawer's header (title, model, new conversation, close). It mounts
              only while the drawer is open; the conversation itself is kept by the Chat in
              localStorage, so closing and reopening resumes it. */}
          {open && (
            <Suspense fallback={<Centered />}>
              <Chat onClose={onClose} />
            </Suspense>
          )}
        </Body>
      </Drawer>
    </>
  )
}
