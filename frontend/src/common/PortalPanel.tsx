// A floating panel portalled to <body> and positioned with `position: fixed` against its
// trigger's bounding rect, so it escapes scrollable or `overflow: hidden` parents (modals,
// drawers, scroll containers, the glass cards' stacking contexts). Opens below the trigger, or
// above when there is not enough room below; aligned on the trigger's left edge, or its right
// edge when the panel would run off the right of the window. (Port of nomaubl's PortalPanel.)
import { useEffect, useState, type ReactNode, type RefObject } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { colors, radius, shadow } from '../theme'

const Panel = styled.div`
  position: fixed; z-index: 1000;
  width: max-content;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  box-shadow: ${shadow.lg}; overflow: hidden; display: flex; flex-direction: column;
`
// Transparent layer under the open panel: a click outside closes the panel and stops there,
// instead of also landing on what lies underneath.
const Backdrop = styled.div`position: fixed; inset: 0; z-index: 999; background: transparent;`

interface AnchorRect { top: number; left: number; right: number; bottom: number; width: number }

export function PortalPanel({
  triggerRef, panelRef, children, maxWidth = 560, minWidth = 240, roomBelow = 320, onDismiss,
}: {
  triggerRef: RefObject<HTMLElement | null>
  panelRef?: RefObject<HTMLDivElement | null>
  children: ReactNode
  /** Widest the panel may grow (content decides below that). */
  maxWidth?: number
  minWidth?: number
  /** Room needed below the trigger before the panel flips above it. */
  roomBelow?: number
  /** Close the panel on a click outside it; the click goes no further. */
  onDismiss?: () => void
}) {
  const [rect, setRect] = useState<AnchorRect | null>(null)

  useEffect(() => {
    const update = () => {
      const el = triggerRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      setRect({ top: r.top, left: r.left, right: r.right, bottom: r.bottom, width: r.width })
    }
    update()
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [triggerRef])

  if (!rect) return null
  const spaceBelow = window.innerHeight - rect.bottom
  const flip = spaceBelow < roomBelow && rect.top > spaceBelow
  const roomRight = window.innerWidth - rect.left - 8
  const roomLeft = rect.right - 8
  const alignRight = roomRight < Math.min(maxWidth, Math.max(roomLeft, minWidth)) && roomLeft > roomRight
  const cap = Math.max(minWidth, Math.min(maxWidth, alignRight ? roomLeft : roomRight))
  const style: React.CSSProperties = {
    minWidth: Math.min(Math.max(rect.width, minWidth), cap),
    maxWidth: cap,
    ...(alignRight ? { right: window.innerWidth - rect.right } : { left: rect.left }),
    ...(flip ? { bottom: window.innerHeight - rect.top + 4 } : { top: rect.bottom + 4 }),
  }
  return createPortal(
    <>
      {onDismiss && <Backdrop onMouseDown={(e) => e.preventDefault()} onClick={(e) => { e.stopPropagation(); onDismiss() }} />}
      <Panel ref={panelRef} style={style}>{children}</Panel>
    </>,
    document.body,
  )
}
