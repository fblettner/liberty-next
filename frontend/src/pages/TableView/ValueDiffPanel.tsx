// Field-level BEFORE / AFTER for one audit statement, parsed in flight from the row's SQL column
// (Screen.value_diff). No values table needed — the detail is reconstructed from the stored
// statement, so it still works after that table is purged. Changed fields are highlighted; a
// "changed only" filter hides the (often many) untouched fields of an UPDATE.
import { useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ArrowRight } from 'lucide-react'
import { parseDmlValues } from '../../common/dmlValues'
import type { ScreenValueDiff } from '../../types/screens'
import { colors, fontSize, fonts, radius } from '../../theme'

const Wrap = styled.div`padding: 8px 8px 12px 30px; max-width: 920px;`
const Bar = styled.div`
  display: flex; align-items: center; gap: 12px; margin-bottom: 6px;
  font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.sans};
`
const Toggle = styled.label`
  display: inline-flex; align-items: center; gap: 5px; cursor: pointer; user-select: none;
  & input { accent-color: ${colors.blue.main}; }
`
const Table = styled.table`
  border-collapse: collapse; width: 100%; font-size: ${fontSize.sm};
  background: ${colors.bg.input}; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;
`
const Th = styled.th`
  text-align: left; padding: 5px 12px; font-size: ${fontSize.micro}; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.06em; color: ${colors.text.muted}; border-bottom: 1px solid ${colors.border}; white-space: nowrap;
`
const Row = styled.tr<{ $changed?: boolean }>`
  ${({ $changed }) => ($changed ? `background: ${colors.blue.bg};` : '')}
  &:not(:last-child) td { border-bottom: 1px solid ${colors.border}; }
`
const FieldTd = styled.td<{ $changed?: boolean }>`
  padding: 4px 12px; vertical-align: top; white-space: nowrap;
  font-family: ${fonts.mono}; font-size: ${fontSize.micro};
  color: ${({ $changed }) => ($changed ? colors.blue.main : colors.text.secondary)};
  box-shadow: ${({ $changed }) => ($changed ? `inset 2px 0 0 ${colors.blue.main}` : 'none')};
`
const ValTd = styled.td<{ $strong?: boolean }>`
  padding: 4px 12px; vertical-align: top; white-space: pre-wrap; word-break: break-word; width: 42%;
  font-family: ${fonts.mono}; font-size: ${fontSize.micro};
  color: ${({ $strong }) => ($strong ? colors.text.primary : colors.text.muted)};
`
const Empty = styled.div`padding: 8px 12px; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`
const Blank = styled.span`color: ${colors.text.muted}; opacity: 0.5;`

/** Case-insensitive cell read (aggregate/result columns may be lower-cased). */
function cell(row: Record<string, unknown>, name?: string | null): string {
  if (!name) return ''
  const v = name in row ? row[name] : row[Object.keys(row).find((k) => k.toLowerCase() === name.toLowerCase()) ?? '']
  return v == null ? '' : String(v)
}

function val(v: string | null): React.ReactNode {
  if (v == null) return <Blank>—</Blank>
  if (v === '') return <Blank>(empty)</Blank>
  return v
}

export function ValueDiffPanel({ row, cfg }: { row: Record<string, unknown>; cfg: ScreenValueDiff }) {
  const { t } = useTranslation()
  const values = useMemo(
    () => parseDmlValues(cell(row, cfg.sql_column), cell(row, cfg.operation_column) || undefined),
    [row, cfg.sql_column, cfg.operation_column],
  )
  const hasBefore = values.some((v) => v.before != null)
  const hasAfter = values.some((v) => v.after != null)
  const isDiff = hasBefore && hasAfter
  const changedCount = isDiff ? values.filter((v) => v.before !== v.after).length : 0
  // Default to "changed only" for an UPDATE that has untouched fields (an F0101 update touches 1
  // of ~90 columns) — the untouched bulk is noise.
  const [changedOnly, setChangedOnly] = useState(isDiff && changedCount > 0 && changedCount < values.length)

  if (values.length === 0) {
    return <Wrap><Empty>{t('table.valueDiff.none', 'No field values could be parsed from this statement.')}</Empty></Wrap>
  }
  const shown = changedOnly ? values.filter((v) => v.before !== v.after) : values

  return (
    <Wrap>
      <Bar>
        <span>{t('table.valueDiff.summary', '{{n}} fields', { n: values.length })}{isDiff ? ` · ${t('table.valueDiff.changed', '{{n}} changed', { n: changedCount })}` : ''}</span>
        {isDiff && changedCount > 0 && changedCount < values.length && (
          <Toggle>
            <input type="checkbox" checked={changedOnly} onChange={(e) => setChangedOnly(e.target.checked)} />
            {t('table.valueDiff.changedOnly', 'Changed only')}
          </Toggle>
        )}
      </Bar>
      <Table>
        <thead>
          <tr>
            <Th>{t('table.valueDiff.field', 'Field')}</Th>
            {hasBefore && <Th>{t('table.valueDiff.before', 'Before')}</Th>}
            {hasAfter && <Th style={{ width: 28 }} />}
            {hasAfter && <Th>{t('table.valueDiff.after', 'After')}</Th>}
          </tr>
        </thead>
        <tbody>
          {shown.map((v) => {
            const changed = isDiff && v.before !== v.after
            return (
              <Row key={v.name} $changed={changed}>
                <FieldTd $changed={changed}>{v.name}</FieldTd>
                {hasBefore && <ValTd $strong={changed}>{val(v.before)}</ValTd>}
                {hasAfter && (
                  <td style={{ padding: '4px 0', textAlign: 'center', color: changed ? colors.blue.main : 'transparent' }}>
                    <ArrowRight size={12} />
                  </td>
                )}
                {hasAfter && <ValTd $strong={changed}>{val(v.after)}</ValTd>}
              </Row>
            )
          })}
        </tbody>
      </Table>
    </Wrap>
  )
}
