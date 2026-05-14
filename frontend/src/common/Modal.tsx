// Centered overlay modal + a ready-made ConfirmModal. (See nomaubl's styled/modal.ts.)
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { colors, radius, fontSize, shadow } from '../theme'
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
`

export const ModalHeader = styled.div`
  padding: 14px 20px;
  border-bottom: 1px solid ${colors.border};
  font-size: ${fontSize.md};
  font-weight: 700;
  color: ${colors.text.primary};
  flex-shrink: 0;
`

export const ModalBody = styled.div`
  padding: 20px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
  font-size: ${fontSize.md};
  line-height: 1.6;
  color: ${colors.text.secondary};
`

export const ModalFooter = styled.div`
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding: 12px 20px;
  border-top: 1px solid ${colors.border};
  flex-shrink: 0;
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
  return (
    <Overlay onClick={onCancel}>
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
    </Overlay>
  )
}
