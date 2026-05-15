// Styled bits for TableView's page (index.tsx) and its result grid (ResultTable.tsx).
import styled from '@emotion/styled'
import { colors, fontSize, fonts } from '../../theme'

export const Meta = styled.div`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
`

// One result cell's content. The optional class is the display-rule "kind" from
// services/cells.ruleCell — a BOOLEAN status bullet (green = true, red = false), an
// ENUM/LOOKUP-resolved label (sans-serif, since it's prose not data), a still-loading
// lookup (italic-muted), or a SQL NULL.
export const CellSpan = styled.span`
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  &.null { color: ${colors.text.muted}; font-style: italic; }
  &.boolean-true, &.boolean-false {
    /* Filled bullet (●) sized up so it reads as a status indicator at a glance. The strict
       1em line-height keeps a boolean column's row height aligned with text rows around it. */
    font-size: 1.15em;
    line-height: 1;
  }
  &.boolean-true { color: ${colors.green.main}; }
  &.boolean-false { color: ${colors.red.main}; }
  &.enum, &.lookup { font-family: ${fonts.sans}; }
  &.lookup-pending { color: ${colors.text.muted}; font-style: italic; }
`
