import styled from '@emotion/styled'
import {colors, radius} from '../theme'

// No backdrop blur here, on purpose: a card sits on the page's own smooth background, where a blur
// changes nothing to the eye, yet makes each card a graphics layer of its own — on a long scrolling
// list a browser short on graphics memory then leaves cards unpainted (empty bands that come and go).
export const Card = styled.div`
  background: var(--ghost-bg, ${colors.bg.card});
  border: 1px solid ${colors.border};
  border-radius: ${radius.lg};
  padding: 16px;
`
