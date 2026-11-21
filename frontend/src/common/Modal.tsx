// Centered overlay modal + a ready-made ConfirmModal. (See nomaubl's styled/modal.ts.)
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { colors, radius, fontSize, shadow, media } from '../theme'
import { Button } from './Button'

export const Overlay = styled.div`
  position: fixed;
  inset: 0;
  background: var(--overlay);
  z-index: 400;
  display: flex;
  align-items: center;
  justify-content: center;
`

// Phone-width window: a window that asks for a height takes the whole screen (100vh is the fallback
// where dvh is unknown). For the presets below, whose own height and radius come after the base
// Modal's rules; the width and the max-* are the base Modal's (!important).
const FULL_SCREEN = `
  height: 100vh;
  height: 100dvh;
  border-radius: 0;
`

export const Modal = styled.div`
  position: relative;
  background: var(--bg-modal);
  backdrop-filter: blur(20px) saturate(160%);
  -webkit-backdrop-filter: blur(20px) saturate(160%);
  border: 1px solid ${colors.border};
  border-radius: ${radius.lg};
  /* Consistent footprint across every dialog — even a one-line Confirm has the same minimum
     presence as a multi-field form. The min() with viewport units keeps it graceful on small
     screens (the min-width drops below 480px when the viewport is narrower, so a 360px-wide
     phone still gets a modal that fits — no horizontal scrollbar). */
  min-width: min(480px, 95vw);
  min-height: min(200px, 80vh);
  max-width: 95vw;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: ${shadow.modal};
  /* Phone-width window: every window takes the full width; a short one (a question, a small form)
     keeps the height of its content, one with a height of its own takes the whole screen.
     !important: the callers size their window with an inline style. */
  ${media.tablet} {
    width: 100vw !important;
    max-width: 100vw !important;
    max-height: 100vh !important;
    max-height: 100dvh !important;
    border-radius: 0;
    border-left: none;
    border-right: none;
    &[style*=' height:'], &[style^='height:'] { height: 100vh !important; height: 100dvh !important; }
  }
`

export const ModalHeader = styled.div`
  padding: 14px 20px;
  border-bottom: 1px solid ${colors.border};
  font-size: ${fontSize.md};
  font-weight: 700;
  color: ${colors.text.primary};
  flex-shrink: 0;
  ${media.tablet} { padding: 10px 12px; }
`

// Body fills the vertical space between Header and Footer. Without 'flex: 1 1 auto' +
// 'min-height: 0' the body sits at its natural content height and the footer floats up
// under it, leaving empty space below — and tall content overflows the modal frame
// instead of scrolling inside the body (the user's complaint on the role-management
// dialog). The 'min-height: 0' is the flex-child quirk that lets the body actually
// shrink to enable its own scroll.
export const ModalBody = styled.div`
  flex: 1 1 auto;
  min-height: 0;
  padding: 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-size: ${fontSize.md};
  line-height: 1.6;
  color: ${colors.text.secondary};
  ${media.tablet} { padding: 12px; }
`

/** A Modal preset for long, multi-tab forms (ScreenDialog and any future "fill out a record"
 *  flow). Locks the frame's dimensions so switching tabs scrolls the body instead of jolting
 *  the dialog up/down — the user complained about chasing a moving Save button when tabs had
 *  different field counts. Uses a fixed height (not min-height) for that reason: tab A with
 *  3 fields and tab B with 15 fields render in the same envelope, ModalBody scrolls when needed.
 *
 *  ``$fullscreen`` swaps to a near-100vw/vh frame for operators editing a row with many fields
 *  on a small screen — the header gives them a maximize button, this toggles the prop. */
export const ScreenDialogModal = styled(Modal)<{ $fullscreen?: boolean }>`
  width: ${({ $fullscreen }) => ($fullscreen ? '100vw' : 'min(900px, 95vw)')};
  height: ${({ $fullscreen }) => ($fullscreen ? '100vh' : 'min(700px, 90vh)')};
  max-width: ${({ $fullscreen }) => ($fullscreen ? '100vw' : '95vw')};
  max-height: ${({ $fullscreen }) => ($fullscreen ? '100vh' : '90vh')};
  border-radius: ${({ $fullscreen }) => ($fullscreen ? '0' : radius.lg)};
  /* Override Modal's flexible min-* so the locked size wins on small content (an "Add row" with
     only required fields filled-in keeps the same footprint as a full Edit). */
  min-width: 0;
  min-height: 0;
  ${media.tablet} { ${FULL_SCREEN} }
`

/** A Modal preset for the screen visual designer — a nearly full-screen frame so the 3-column
 *  layout (palette + canvas + inspector) has room to breathe. Without the explicit width/height
 *  the body's grid would collapse to its natural content and overflow the viewport instead of
 *  letting each column scroll on its own. Used by ScreenEditor's Dialog tab in Visual mode.
 *
 *  ``$fullscreen`` swaps to a 100vw/vh frame for designing complex screens on a laptop where
 *  the default 1400×900 still leaves the palette / inspector cramped. */
export const VisualBuilderModal = styled(Modal)<{ $fullscreen?: boolean }>`
  width: ${({ $fullscreen }) => ($fullscreen ? '100vw' : 'min(1400px, 96vw)')};
  height: ${({ $fullscreen }) => ($fullscreen ? '100vh' : 'min(900px, 92vh)')};
  max-width: ${({ $fullscreen }) => ($fullscreen ? '100vw' : '96vw')};
  max-height: ${({ $fullscreen }) => ($fullscreen ? '100vh' : '92vh')};
  border-radius: ${({ $fullscreen }) => ($fullscreen ? '0' : radius.lg)};
  min-width: 0;
  min-height: 0;
  ${media.tablet} { ${FULL_SCREEN} }
`

/** A Modal preset for a *nested* ScreenDialog opened from inside another dialog — e.g. a row
 *  click on a NestedTableView ("Edit Activity Log rule" sitting on top of "Settings - Applications").
 *  Smaller than the top-level preset (so the parent's frame remains visible behind it) and
 *  *auto-height* with a `max-height` cap — the nested form is usually short (a few related-table
 *  columns) and the parent already paid for a fixed envelope, no point repeating it.
 *
 *  Accepts ``$fullscreen`` for shape-compatibility with ``ScreenDialogModal`` (the caller picks
 *  one of the two based on a ``nested`` flag) but ignores it — a nested dialog going fullscreen
 *  would hide the parent context that justified showing the nested one in the first place. */
export const NestedScreenDialogModal = styled(Modal)<{ $fullscreen?: boolean }>`
  width: min(720px, 90vw);
  /* never taller than what holds it: opened from a record form, its overlay is laid out inside that form's
     window (a backdrop-filter contains fixed children) — a tall nested form scrolls instead of being cut */
  max-height: min(85vh, 100%);
  /* Re-enable a small min-height so a single-field dialog still has a sensible footprint. */
  min-width: 0;
  min-height: 240px;
  /* on a phone the parent's frame cannot stay visible behind it anyway */
  ${media.tablet} { ${FULL_SCREEN} }
`

/** Overlay variant for a nested dialog — bumps z-index above the parent's Overlay so click-
 *  outside-to-close binds to the nested first, and the visual stack is unambiguous even when
 *  React's commit order isn't (e.g. when sub-mount races with a parent re-render). */
export const NestedOverlay = styled(Overlay)`
  z-index: 500;
`

export const ModalFooter = styled.div`
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid ${colors.border};
  flex-shrink: 0;
  ${media.tablet} { padding: 10px 12px; flex-wrap: wrap; }
`

export function ConfirmModal({
  title,
  message,
  confirmLabel,
  cancelLabel,
  variant = 'primary',
  onConfirm,
  onCancel,
}: {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'primary' | 'danger'
  onConfirm: () => void
  onCancel: () => void
}) {
  const { t } = useTranslation()
  // Portal to document.body at the top z-index (2000, same as the useModals confirm) — a confirm is
  // a top-level interrupt and must never be trapped beneath whatever opened it. Rendered inline at
  // the base Overlay (400), it got buried under a nested dialog (NestedOverlay 500, a backdrop-filter
  // stacking context) — e.g. deleting a row in a nested-table edit dialog.
  return createPortal(
    <Overlay onClick={onCancel} style={{ zIndex: 2000 }}>
      <Modal style={{ width: 440 }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{title}</ModalHeader>
        <ModalBody>{message}</ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel}>
            {cancelLabel ?? t('common.cancel')}
          </Button>
          <Button $size="sm" $variant={variant} onClick={onConfirm} autoFocus>
            {confirmLabel ?? t('common.confirm')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}
