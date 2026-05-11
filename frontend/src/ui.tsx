// Shared UI primitives for Liberty's admin shell — emotion-styled, themed via
// src/theme.ts. Adapted from nomaubl's `common/*` set, trimmed to what Liberty
// uses. Keep visual choices here so pages stay declarative.
import { useEffect, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { keyframes } from '@emotion/react'
import { useTranslation } from 'react-i18next'
import { colors, radius, fontSize, fonts, glass, shadow } from './theme'

// ── Theme awareness ───────────────────────────────────────────────────────────

/** True when the light theme is active (the `.theme-light` class on <html>).
 *  Re-renders the caller when the Layout's toggle flips it — used e.g. to pick
 *  Monaco's editor theme. */
export function useIsLight(): boolean {
  const [light, setLight] = useState(() => document.documentElement.classList.contains('theme-light'))
  useEffect(() => {
    const el = document.documentElement
    const obs = new MutationObserver(() => setLight(el.classList.contains('theme-light')))
    obs.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return light
}

// ── Layout helpers ────────────────────────────────────────────────────────────

export const Stack = styled.div<{ gap?: number }>`
  display: flex;
  flex-direction: column;
  gap: ${({ gap = 12 }) => gap}px;
`

export const Row = styled.div<{ gap?: number; wrap?: boolean; align?: string }>`
  display: flex;
  align-items: ${({ align = 'center' }) => align};
  gap: ${({ gap = 10 }) => gap}px;
  flex-wrap: ${({ wrap = true }) => (wrap ? 'wrap' : 'nowrap')};
`

// ── Card ──────────────────────────────────────────────────────────────────────

export const Card = styled.div`
  background: var(--ghost-bg, ${colors.bg.card});
  border: 1px solid ${colors.border};
  border-radius: ${radius.lg};
  padding: 16px;
  ${glass.surface}
`

export const CardTitle = styled.div`
  font-size: ${fontSize.base};
  font-weight: 600;
  color: ${colors.text.muted};
  text-transform: uppercase;
  letter-spacing: 0.07em;
  margin-bottom: 12px;
`

// ── Buttons ───────────────────────────────────────────────────────────────────

type ButtonVariant = 'primary' | 'danger' | 'ghost'
type ButtonSize = 'sm' | 'md' | 'lg'

export const Button = styled.button<{ $variant?: ButtonVariant; $size?: ButtonSize }>`
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: ${radius.md};
  font-family: ${fonts.sans};
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, opacity 0.15s, border-color 0.15s, color 0.15s;
  white-space: nowrap;

  ${({ $size = 'md' }) =>
    $size === 'sm'
      ? `height: 28px; padding: 0 10px; font-size: ${fontSize.sm};`
      : $size === 'lg'
        ? `height: 36px; padding: 0 18px; font-size: ${fontSize.md};`
        : `height: 32px; padding: 0 14px; font-size: ${fontSize.base};`}

  ${({ $variant = 'ghost' }) =>
    $variant === 'primary'
      ? `background: ${colors.blue.main}; border: 1px solid transparent; color: #fff;
         &:hover:not(:disabled) { opacity: 0.88; }`
      : $variant === 'danger'
        ? `background: transparent; border: 1px solid ${colors.red.border}; color: ${colors.red.main};
           &:hover:not(:disabled) { background: ${colors.red.bg}; }`
        : `background: transparent; border: 1px solid ${colors.border}; color: ${colors.text.secondary};
           &:hover:not(:disabled) { background: ${colors.bg.card}; color: ${colors.text.primary}; }`}

  &:disabled { opacity: 0.4; cursor: default; }
`

export const LinkButton = styled.button`
  background: none;
  border: none;
  padding: 0;
  cursor: pointer;
  color: ${colors.blue.main};
  font: inherit;
  &:hover { text-decoration: underline; }
`

// ── Inputs ────────────────────────────────────────────────────────────────────

const fieldBase = `
  height: 32px;
  padding: 0 10px;
  border-radius: ${radius.md};
  border: 1px solid ${colors.border};
  background: ${colors.bg.input};
  color: ${colors.text.primary};
  font-size: ${fontSize.base};
  font-family: ${fonts.sans};
  outline: none;
  transition: border-color 0.15s, box-shadow 0.15s;
  &:focus { border-color: ${colors.blue.main}; box-shadow: ${shadow.focus}; }
  &::placeholder { color: ${colors.text.muted}; }
`

export const Input = styled.input`
  ${fieldBase}
  width: 100%;
  &:read-only { background: var(--bg-readonly); color: ${colors.text.muted}; }
`

export const Select = styled.select`
  ${fieldBase}
  cursor: pointer;
`

export const Textarea = styled.textarea`
  ${fieldBase}
  height: auto;
  min-height: 80px;
  padding: 8px 10px;
  width: 100%;
  resize: vertical;
  line-height: 1.5;
`

export const FieldLabel = styled.label`
  display: block;
  font-size: ${fontSize.micro};
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: ${colors.text.muted};
  margin-bottom: 5px;
`

export function Field({ label, children, htmlFor }: { label: string; children: ReactNode; htmlFor?: string }) {
  return (
    <div>
      <FieldLabel htmlFor={htmlFor}>{label}</FieldLabel>
      {children}
    </div>
  )
}

// ── Tags / badges ─────────────────────────────────────────────────────────────

type Tone = 'neutral' | 'blue' | 'green' | 'red' | 'orange' | 'purple'

export const Tag = styled.span<{ $tone?: Tone }>`
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: ${radius.sm};
  font-size: ${fontSize.sm};
  font-weight: 600;
  font-family: ${fonts.mono};
  letter-spacing: 0.02em;
  ${({ $tone = 'neutral' }) => {
    const c =
      $tone === 'blue'
        ? colors.blue
        : $tone === 'green'
          ? colors.green
          : $tone === 'red'
            ? colors.red
            : $tone === 'orange'
              ? colors.orange
              : $tone === 'purple'
                ? colors.purple
                : null
    return c
      ? `background: ${c.bg}; border: 1px solid ${c.border}; color: ${c.main};`
      : `background: ${colors.bg.input}; border: 1px solid ${colors.border}; color: ${colors.text.muted};`
  }}
`

export const Mono = styled.span`
  font-family: ${fonts.mono};
  font-size: 0.95em;
  color: ${colors.text.muted};
`

// ── Banners ───────────────────────────────────────────────────────────────────

export const Banner = styled.div<{ $tone?: 'error' | 'ok' | 'info' }>`
  padding: 8px 12px;
  border-radius: ${radius.md};
  font-size: ${fontSize.base};
  ${({ $tone = 'info' }) => {
    const c = $tone === 'error' ? colors.red : $tone === 'ok' ? colors.green : colors.blue
    return `background: ${c.bg}; border: 1px solid ${c.border}; color: ${c.main};`
  }}
`

export const Pre = styled.pre`
  background: ${colors.bg.input};
  border: 1px solid ${colors.border};
  border-radius: ${radius.md};
  padding: 12px;
  overflow: auto;
  max-height: 50vh;
  font-size: ${fontSize.sm};
  color: ${colors.text.secondary};
  white-space: pre-wrap;
  word-break: break-word;
`

// ── Spinner ───────────────────────────────────────────────────────────────────

const spin = keyframes`from { transform: rotate(0deg) } to { transform: rotate(360deg) }`

export const SpinnerRing = styled.div<{ size?: number; thickness?: number }>`
  width: ${({ size = 18 }) => size}px;
  height: ${({ size = 18 }) => size}px;
  border-radius: 50%;
  border: ${({ thickness = 2 }) => thickness}px solid ${colors.border};
  border-top-color: ${colors.blue.main};
  animation: ${spin} 0.7s linear infinite;
  flex-shrink: 0;
`

const CenteredWrap = styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 50vh;
  color: ${colors.text.muted};
  font-size: ${fontSize.base};
`

/** Full-area "Loading…" / message placeholder (also used for fatal errors). */
export function Centered({ children, error }: { children?: ReactNode; error?: boolean }) {
  const { t } = useTranslation()
  return (
    <CenteredWrap style={error ? { color: colors.red.main } : undefined}>
      {!error && children == null && <SpinnerRing />}
      {children ?? t('common.loading')}
    </CenteredWrap>
  )
}

// ── Page header (the "context bar" at the top of each page) ───────────────────

const PageWrap = styled.div`
  display: flex;
  flex-direction: column;
  height: 100%;
  overflow: hidden;
  padding: 16px;
  gap: 0;
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

// ── Modal + confirm ───────────────────────────────────────────────────────────

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
