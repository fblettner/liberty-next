import styled from '@emotion/styled'
import {colors, radius, glass} from '../theme'

export const Card = styled.div`
  background: var(--ghost-bg, ${colors.bg.card});
  border: 1px solid ${colors.border};
  border-radius: ${radius.lg};
  padding: 16px;
  ${glass.surface}
`

