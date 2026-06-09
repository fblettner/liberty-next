import styled from '@emotion/styled'
import { colors, radius, fontSize } from '../theme'

/** Inline status strip — error / warning / ok / info, coloured from the theme. */
export const Banner = styled.div<{ $tone?: 'error' | 'warning' | 'ok' | 'info' }>`
  padding: 8px 12px;
  border-radius: ${radius.md};
  font-size: ${fontSize.base};
  ${({ $tone = 'info' }) => {
    const c = $tone === 'error' ? colors.red
      : $tone === 'warning' ? colors.orange
      : $tone === 'ok' ? colors.green
      : colors.blue
    return `background: ${c.bg}; border: 1px solid ${c.border}; color: ${c.main};`
  }}
`

/** Pre-formatted code/JSON block. */
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
