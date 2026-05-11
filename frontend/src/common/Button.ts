import styled from '@emotion/styled'
import { colors, radius, fontSize, fonts } from '../theme'

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
