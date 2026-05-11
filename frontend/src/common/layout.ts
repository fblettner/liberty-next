// Tiny flex helpers — vertical Stack / horizontal Row. The numeric props are px
// gaps; everything visual is in the theme. (See nomaubl's `styled/*`.)
import styled from '@emotion/styled'

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
