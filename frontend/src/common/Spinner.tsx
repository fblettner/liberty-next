import { type ReactNode } from 'react'
import styled from '@emotion/styled'
import { keyframes } from '@emotion/react'
import { useTranslation } from 'react-i18next'
import { colors, fontSize } from '../theme'

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

const CenteredWrap = styled.div<{ $error?: boolean }>`
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  min-height: 50vh;
  font-size: ${fontSize.base};
  color: ${({ $error }) => ($error ? colors.red.main : colors.text.muted)};
`

/** Full-area "Loading…" / message placeholder (also used for fatal errors). */
export function Centered({ children, error }: { children?: ReactNode; error?: boolean }) {
  const { t } = useTranslation()
  return (
    <CenteredWrap $error={error}>
      {!error && children == null && <SpinnerRing />}
      {children ?? t('common.loading')}
    </CenteredWrap>
  )
}
