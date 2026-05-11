import styled from '@emotion/styled'
import { colors, radius, fontSize, glass } from '../theme'

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
