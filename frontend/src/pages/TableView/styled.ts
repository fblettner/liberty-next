// Styled bits for TableView's page (index.tsx) and its result grid (ResultTable.tsx).
import styled from '@emotion/styled'
import { colors, fontSize, fonts } from '../../theme'

export const Meta = styled.div`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
`

// One result cell's content. The optional class is the display-rule "kind" from
// services/cells.ruleCell — a BOOLEAN tick, an ENUM/LOOKUP-resolved label (sans-serif,
// since it's prose not data), a still-loading lookup (italic-muted), or a SQL NULL.
export const CellSpan = styled.span`
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  &.null { color: ${colors.text.muted}; font-style: italic; }
  &.bool-true { color: ${colors.green.main}; font-weight: 600; }
  &.bool-false { color: ${colors.red.main}; opacity: 0.65; }
  &.enum, &.lookup { font-family: ${fonts.sans}; }
  &.lookup-pending { color: ${colors.text.muted}; font-style: italic; }
`
