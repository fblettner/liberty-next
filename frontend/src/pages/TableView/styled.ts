// Styled bits shared by TableView's page (index.tsx) and its result grid
// (ResultTable.tsx) — keeps both files lean and avoids a circular import.
import styled from '@emotion/styled'
import { colors, radius, fontSize, fonts } from '../../theme'

export const Meta = styled.div`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
`

export const TableScroll = styled.div`
  overflow: auto;
  max-height: 58vh;
  border: 1px solid ${colors.border};
  border-radius: ${radius.md};
`

export const DataTable = styled.table`
  border-collapse: collapse;
  width: 100%;
  font-size: ${fontSize.base};
  font-family: ${fonts.mono};

  th {
    position: sticky;
    top: 0;
    background: ${colors.bg.dropdown};
    color: ${colors.text.secondary};
    text-align: left;
    padding: 6px 10px;
    border-bottom: 1px solid ${colors.border};
    border-right: 1px solid ${colors.border};
    user-select: none;
    white-space: nowrap;
    font-weight: 600;
  }
  th.sortable { cursor: pointer; }
  th.sortable:hover { color: ${colors.text.primary}; }
  th .ix { display: inline-flex; vertical-align: middle; margin-left: 4px; color: ${colors.blue.main}; }
  td {
    padding: 4px 10px;
    border-bottom: 1px solid ${colors.border};
    border-right: 1px solid ${colors.border};
    white-space: nowrap;
    color: ${colors.text.secondary};
  }
  td.null { color: ${colors.text.muted}; font-style: italic; }
  tr:hover td { background: var(--hover-subtle); }

  /* Display-rule rendering — BOOLEAN ticks, ENUM/LOOKUP-resolved labels, an unresolved (still-loading) lookup. */
  .bool-true { color: ${colors.green.main}; font-weight: 600; }
  .bool-false { color: ${colors.red.main}; opacity: 0.7; }
  .enum, .lookup { font-family: ${fonts.sans}; }
  .lookup-pending { color: ${colors.text.muted}; font-style: italic; }
`
