import styled from '@emotion/styled'
import { colors, radius, fontSize, fonts } from '../theme'

export type Tone = 'neutral' | 'blue' | 'green' | 'red' | 'orange' | 'purple' | 'yellow'

function tonePalette(tone: Tone) {
  switch (tone) {
    case 'blue': return colors.blue
    case 'green': return colors.green
    case 'red': return colors.red
    case 'orange': return colors.orange
    case 'purple': return colors.purple
    case 'yellow': return colors.yellow
    default: return null
  }
}

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
    const c = tonePalette($tone)
    return c
      ? `background: ${c.bg}; border: 1px solid ${c.border}; color: ${c.main};`
      : `background: ${colors.bg.input}; border: 1px solid ${colors.border}; color: ${colors.text.muted};`
  }}
`

/** Inline monospace label — paths, URLs, identifiers. */
export const Mono = styled.span`
  font-family: ${fonts.mono};
  font-size: 0.95em;
  color: ${colors.text.muted};
`
