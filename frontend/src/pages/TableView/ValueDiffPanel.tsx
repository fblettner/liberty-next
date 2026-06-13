// Field-level BEFORE / AFTER for one audit statement, parsed in flight from the row's SQL column
// (Screen.value_diff). No values table needed — the detail is reconstructed from the stored
// statement, so it still works after that table is purged.
import { useMemo } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { parseDmlValues } from '../../common/dmlValues'
import type { ScreenValueDiff } from '../../types/screens'
import { colors, fontSize, fonts, radius } from '../../theme'

const Wrap = styled.div`padding: 8px 8px 10px 28px;`
const Table = styled.table`
  border-collapse: collapse; width: 100%; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  background: ${colors.bg.input}; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;
`
const Th = styled.th`
  text-align: left; padding: 5px 10px; font-size: ${fontSize.micro}; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: ${colors.text.muted}; border-bottom: 1px solid ${colors.border}; white-space: nowrap;
`
const Td = styled.td<{ $mono?: boolean; $changed?: boolean }>`
  padding: 4px 10px; vertical-align: top; border-bottom: 1px solid ${colors.border};
  color: ${({ $changed }) => ($changed ? colors.text.primary : colors.text.secondary)};
  font-family: ${({ $mono }) => ($mono ? fonts.mono : fonts.sans)};
  white-space: pre-wrap; word-break: break-word;
`
const Field = styled(Td)`font-family: ${fonts.mono}; color: ${colors.text.primary}; white-space: nowrap;`
const Empty = styled.div`padding: 8px 10px; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`

/** Case-insensitive cell read (aggregate/result columns may be lower-cased). */
function cell(row: Record<string, unknown>, name?: string | null): string {
  if (!name) return ''
  const v = name in row ? row[name] : row[Object.keys(row).find((k) => k.toLowerCase() === name.toLowerCase()) ?? '']
  return v == null ? '' : String(v)
}

const dash = (v: string | null) => (v == null ? '—' : v === '' ? '∅' : v)

export function ValueDiffPanel({ row, cfg }: { row: Record<string, unknown>; cfg: ScreenValueDiff }) {
  const { t } = useTranslation()
  const values = useMemo(
    () => parseDmlValues(cell(row, cfg.sql_column), cell(row, cfg.operation_column) || undefined),
    [row, cfg.sql_column, cfg.operation_column],
  )
  if (values.length === 0) return <Wrap><Empty>{t('table.valueDiff.none', 'No field values could be parsed from this statement.')}</Empty></Wrap>
  // Show BEFORE/AFTER columns only when the operation actually has that side.
  const hasBefore = values.some((v) => v.before != null)
  const hasAfter = values.some((v) => v.after != null)
  return (
    <Wrap>
      <Table>
        <thead>
          <tr>
            <Th>{t('table.valueDiff.field', 'Field')}</Th>
            {hasBefore && <Th>{t('table.valueDiff.before', 'Before')}</Th>}
            {hasAfter && <Th>{t('table.valueDiff.after', 'After')}</Th>}
          </tr>
        </thead>
        <tbody>
          {values.map((v) => {
            const changed = hasBefore && hasAfter && v.before !== v.after
            return (
              <tr key={v.name}>
                <Field>{v.name}</Field>
                {hasBefore && <Td $mono $changed={changed}>{dash(v.before)}</Td>}
                {hasAfter && <Td $mono $changed={changed}>{dash(v.after)}</Td>}
              </tr>
            )
          })}
        </tbody>
      </Table>
    </Wrap>
  )
}
