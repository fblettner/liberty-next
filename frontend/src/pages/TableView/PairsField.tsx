// A column of format `pairs` in the record form: its text (`name = value`, one per line) shown as fields —
// one labelled field per parameter of an endpoint when the column says whose parameters it holds
// (`params_of`), free name / value rows otherwise. The text itself stays one click away.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Plus, X } from 'lucide-react'
import { Input, SearchSelect, Textarea } from '../../common'
import type { ParamDef } from '../../types/connectors'
import { type Pair, formatPairs, pairValue, parsePairs, setPair } from '../../services/pairs'
import { colors, fontSize, fonts, radius, media } from '../../theme'

const Box = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; padding: 10px 12px;
  display: flex; flex-direction: column; gap: 8px;
`
const Grid = styled.div`
  display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px;
  ${media.tablet} { grid-template-columns: minmax(0, 1fr); }
`
const Cell = styled.label<{ $wide?: boolean }>`
  display: flex; flex-direction: column; gap: 3px; min-width: 0;
  ${(p) => (p.$wide ? 'grid-column: 1 / -1;' : '')}
`
const Name = styled.span`font-size: ${fontSize.micro}; font-weight: 600; color: ${colors.text.secondary};`
const Help = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; line-height: 1.35;`
const Line = styled.div`display: flex; gap: 8px; align-items: center;`
const Small = styled.button`
  display: inline-flex; align-items: center; gap: 4px; background: transparent; border: none; cursor: pointer; padding: 2px 0;
  color: ${colors.text.muted}; font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  &:hover { color: ${colors.text.primary}; }
  &:disabled { opacity: 0.5; cursor: default; }
`
const Foot = styled.div`display: flex; justify-content: space-between; align-items: center; gap: 12px;`
const Empty = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted};`

export function PairsField({
  value, onChange, defs, pickFirst, disabled, required,
}: {
  value: string
  onChange: (text: string | null) => void
  /** The parameters to fill (an endpoint's): one labelled field each. Absent → free name / value rows. */
  defs?: ParamDef[] | null
  /** The names come from another field (`params_of`) that is still blank: say so instead of free rows. */
  pickFirst?: boolean
  disabled?: boolean
  required?: boolean
}) {
  const { t } = useTranslation()
  const parsed = useMemo(() => parsePairs(value), [value])
  // the rows being typed (a row without a name yet is not in the text): kept here, re-read when the text
  // changes from outside (another record, an action that fills the field)
  const [rows, setRows] = useState<Pair[]>(parsed ?? [])
  const [asText, setAsText] = useState(false)
  useEffect(() => {
    if (parsed && formatPairs(rows) !== formatPairs(parsed)) setRows(parsed)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- only an outside change re-reads the rows
  }, [value])
  const emit = (next: Pair[]) => { setRows(next); const text = formatPairs(next); onChange(text === '' ? null : text) }
  const text = asText || parsed === null

  const body = text ? (
    <Textarea
      required={required} disabled={disabled} value={value} rows={Math.min(12, Math.max(3, value.split('\n').length + 1))}
      style={{ minHeight: 0, fontFamily: fonts.mono }}
      onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}
    />
  ) : pickFirst ? (
    <Empty>{t('pairs.pickFirst')}</Empty>
  ) : defs ? (
    <>
      {defs.length === 0 && rows.length === 0 && <Empty>{t('pairs.noParams')}</Empty>}
      <Grid>
        {defs.map((d) => {
          const v = pairValue(rows, d.name)
          const long = v.length > 60
          return (
            <Cell key={d.name} $wide={long}>
              <Name>{d.label || d.name}{d.required ? ' *' : ''}</Name>
              {d.choices?.length ? (
                <SearchSelect
                  value={v} disabled={disabled} options={d.choices.map((c) => ({ value: c, label: c }))}
                  onChange={(next: string | null) => emit(setPair(rows, d.name, next ?? ''))}
                  anyLabel={d.default ?? t('common.none')}
                />
              ) : (
                <Input
                  type="text" value={v} disabled={disabled}
                  placeholder={d.default != null && d.default !== '' ? t('table.defaultPrefix', { v: d.default }) : d.example ? t('table.examplePrefix', { v: d.example }) : ''}
                  onChange={(e) => emit(setPair(rows, d.name, e.target.value))}
                />
              )}
              {d.help && <Help>{d.help}</Help>}
            </Cell>
          )
        })}
        {/* a value the text holds for a name the endpoint does not declare: shown, so nothing is hidden */}
        {rows.filter((p) => !defs.some((d) => d.name.toLowerCase() === p.name.toLowerCase())).map((p) => (
          <Cell key={`x-${p.name}`}>
            <Name style={{ fontFamily: fonts.mono }}>{p.name}</Name>
            <Input type="text" value={p.value} disabled={disabled} onChange={(e) => emit(setPair(rows, p.name, e.target.value))} />
            <Help>{t('pairs.undeclared')}</Help>
          </Cell>
        ))}
      </Grid>
    </>
  ) : (
    <>
      {rows.map((p, i) => (
        <Line key={i}>
          <Input type="text" value={p.name} disabled={disabled} placeholder={t('pairs.name')} style={{ flex: '0 0 38%', fontFamily: fonts.mono }}
            onChange={(e) => emit(rows.map((r, k) => (k === i ? { ...r, name: e.target.value } : r)))} />
          <Input type="text" value={p.value} disabled={disabled} placeholder={t('pairs.value')}
            onChange={(e) => emit(rows.map((r, k) => (k === i ? { ...r, value: e.target.value } : r)))} />
          <Small type="button" disabled={disabled} title={t('pairs.remove')} aria-label={t('pairs.remove')} onClick={() => emit(rows.filter((_r, k) => k !== i))}><X size={14} /></Small>
        </Line>
      ))}
      {rows.length === 0 && <Empty>{t('pairs.none')}</Empty>}
    </>
  )

  return (
    <Box>
      {body}
      <Foot>
        {!text && !defs && !pickFirst
          ? <Small type="button" disabled={disabled} onClick={() => setRows([...rows, { name: '', value: '' }])}><Plus size={13} /> {t('pairs.add')}</Small>
          : <span />}
        {parsed !== null && <Small type="button" onClick={() => setAsText((x) => !x)}>{asText ? t('pairs.asFields') : t('pairs.asText')}</Small>}
      </Foot>
    </Box>
  )
}
