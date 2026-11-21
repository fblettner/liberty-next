// The look of the Explorer's shelves — the view, the period, the rows, the measures, the filters: one card
// each, with the coloured icon and title the stage cards of the editors have, and its chips in its colour.
// (Only the look: what a shelf holds and does is in Shelves.tsx / PeriodBar.tsx / index.tsx.)
import styled from '@emotion/styled'
import { colors, fontSize, fonts, radius } from '../../theme'

export type ShelfTone = 'blue' | 'green' | 'orange' | 'purple'

export const ShelfBox = styled.div<{ $tone: ShelfTone; $over?: boolean }>`
  display: flex; align-items: center; gap: 8px; min-height: 44px; padding: 6px 10px; flex-wrap: wrap; flex-shrink: 0;
  border: 1px solid ${(p) => (p.$over ? colors[p.$tone].main : colors.border)}; border-radius: ${radius.lg};
  background: ${(p) => (p.$over ? colors[p.$tone].bg : 'var(--bg-modal)')}; transition: border-color 0.12s, background 0.12s;
`
export const ShelfTitle = styled.span<{ $tone: ShelfTone }>`
  display: inline-flex; align-items: center; gap: 7px; min-width: 122px; flex-shrink: 0;
  font-family: ${fonts.sans}; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: ${(p) => colors[p.$tone].main};
  & .chip {
    display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: ${radius.md};
    background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border};
  }
`
