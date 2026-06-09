// Shared chrome for editor modals — the consistent frame every "edit one thing in a modal" surface
// sits in, so header/footer placement, the close affordance, and scroll behaviour stop drifting
// per editor (see the editor UX audit). Layout, top to bottom:
//
//   ┌ header  · title (left, with optional dirty badge) · maximize? · ✕ close (right) ┐
//   ├ tabs    · optional fixed tab bar                                                ┤
//   ├ body    · the ONLY scrolling region (children)                                  ┤
//   └ footer  · optional left content · Cancel · Save (right)                         ┘
//
// Rules encoded here: close ``✕`` is always top-RIGHT (icon — quick dismiss); Cancel/Save are always
// the footer, right; header + tabs + footer are fixed, only the body scrolls. Escape calls onClose
// (the caller's onClose may prompt for unsaved changes). No backdrop-click-to-close by default — an
// editor must not discard in-progress edits on a stray click.
import { useEffect, type CSSProperties, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { X, Maximize2, Minimize2, Save } from 'lucide-react'
import { Overlay, Modal, ScreenDialogModal, VisualBuilderModal, ModalHeader, ModalFooter } from './Modal'
import { Button } from './Button'
import { colors, fontSize } from '../theme'

const HeaderRow = styled.div`
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  & > .title { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
  & > .right { display: inline-flex; align-items: center; gap: 8px; flex-shrink: 0; }
  & .dirty { color: ${colors.text.muted}; font-size: ${fontSize.sm}; }
`
const IconBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px;
  border: 1px solid ${colors.border}; border-radius: 6px; background: transparent; color: ${colors.text.secondary};
  cursor: pointer; flex-shrink: 0;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`
// The consumer's tab bar controls its own padding/border (it's rendered full-width here, like the
// header/footer), so this slot only pins it.
const TabsSlot = styled.div`flex-shrink: 0;`
// Fills the space between the fixed header/tabs and footer. ``$scroll`` true → this IS the scroll
// region (simple forms). False → ``overflow: hidden`` so a child that manages its own scrolling
// (e.g. ScreenEditor's per-tab scroll panes) isn't double-scrolled.
const Body = styled.div<{ $scroll: boolean }>`
  flex: 1 1 auto; min-height: 0; padding: 20px;
  overflow-y: ${({ $scroll }) => ($scroll ? 'auto' : 'hidden')};
  display: flex; flex-direction: column; gap: 14px;
  color: ${colors.text.secondary}; font-size: ${fontSize.md};
`

const FRAMES = { default: Modal, screen: ScreenDialogModal, visual: VisualBuilderModal } as const

export interface EditorModalShellProps {
  /** Header title (left). May include a mono ``[scope.id]`` subtitle as a child node. */
  title: ReactNode
  /** Close / cancel. The caller may prompt for unsaved changes here; the shell just invokes it. */
  onClose: () => void
  /** Which frame preset to use — visual builder, screen dialog, or the plain modal. */
  variant?: keyof typeof FRAMES
  /** Maximize/restore toggle — rendered in the header only when both are provided. */
  fullscreen?: boolean
  onToggleFullscreen?: () => void
  /** Marks the title with an "unsaved" badge. */
  dirty?: boolean
  /** When set, a Save button is rendered in the footer (calls this). */
  onSave?: () => void
  saveDisabled?: boolean
  busy?: boolean
  /** Optional fixed tab bar under the header. */
  tabs?: ReactNode
  /** Replaces the default Cancel/Save footer. When omitted, the default footer renders. */
  footer?: ReactNode
  /** Left-aligned content in the default footer (e.g. a Delete button). */
  footerLeft?: ReactNode
  /** Allow closing by clicking the backdrop (default false — editors shouldn't discard on a stray click). */
  dismissOnBackdrop?: boolean
  /** Body owns the scroll (default true). Set false when a child manages its own scrolling. */
  scrollBody?: boolean
  /** Overlay z-index override — for a modal opened from inside another modal (default = Overlay's). */
  overlayZIndex?: number
  /** Frame size/style override (e.g. a specific width/height for a non-preset modal). */
  frameStyle?: CSSProperties
  /** The body content. */
  children: ReactNode
}

export function EditorModalShell({
  title, onClose, variant = 'default', fullscreen, onToggleFullscreen, dirty, onSave,
  saveDisabled, busy, tabs, footer, footerLeft, dismissOnBackdrop = false, scrollBody = true,
  overlayZIndex, frameStyle, children,
}: EditorModalShellProps) {
  const { t } = useTranslation()
  const Frame = FRAMES[variant]

  // Escape closes (routes through the caller's onClose, which may prompt).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  const defaultFooter = (
    <>
      {footerLeft}
      <div style={{ marginLeft: 'auto', display: 'inline-flex', gap: 8 }}>
        <Button $variant="ghost" $size="sm" disabled={busy} onClick={onClose}>{t('common.cancel')}</Button>
        {onSave && (
          <Button $variant="primary" $size="sm" disabled={busy || saveDisabled} onClick={onSave}>
            <Save size={13} /> {t('common.save')}
          </Button>
        )}
      </div>
    </>
  )

  return createPortal(
    <Overlay onClick={dismissOnBackdrop ? onClose : undefined} style={overlayZIndex != null ? { zIndex: overlayZIndex } : undefined}>
      <Frame $fullscreen={fullscreen} style={frameStyle} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          <HeaderRow>
            <span className="title">{title}</span>
            <span className="right">
              {dirty && <span className="dirty">{t('settings.unsaved')}</span>}
              {onToggleFullscreen && (
                <IconBtn type="button" onClick={onToggleFullscreen}
                  title={fullscreen ? t('common.restore') : t('common.maximize')} aria-pressed={fullscreen}>
                  {fullscreen ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
                </IconBtn>
              )}
              <IconBtn type="button" onClick={onClose} title={t('common.close', 'Close')} aria-label={t('common.close', 'Close')}>
                <X size={15} />
              </IconBtn>
            </span>
          </HeaderRow>
        </ModalHeader>
        {tabs && <TabsSlot>{tabs}</TabsSlot>}
        <Body $scroll={scrollBody}>{children}</Body>
        {(footer || onSave || footerLeft) && <ModalFooter>{footer ?? defaultFooter}</ModalFooter>}
      </Frame>
    </Overlay>,
    document.body,
  )
}
