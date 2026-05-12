// The server-filter panel for TableView: one field per result column flagged `filter` in the
// query config (v1's col_filter). Each field has an operator picker (contains / equals / … —
// like the grid's per-column filter) and an input; the value + operator are sent to the query
// as `:<col>` + `:<col>_op` binds on Run, so the SQL pre-filters server-side *before* the grid
// loads (the in-grid TanStack filters then refine the loaded page). Collapsible — open by default
// when the query doesn't auto-load (you set filters first), collapsed when it does.
import { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Check, Filter } from 'lucide-react'
import type { Column } from '../../types/connectors'
import { Input, Select, Field, Row } from '../../common'
import { colors, radius, fontSize, fonts, shadow } from '../../theme'

export interface ServerFilter { op: string; val: string }

// the operators the migrated `SELECT * FROM (…) _flt WHERE …` wrapper understands (the `:<col>_op`
// values it switches on) — a subset of the grid's, minus the numeric-range ones (those run client-side).
const OPS = ['contains', 'equals', 'notEquals', 'startsWith', 'endsWith'] as const
const OP_GLYPH: Record<string, string> = { contains: '~', equals: '=', notEquals: '≠', startsWith: '^', endsWith: '$' }

const NUM_FORMATS = new Set(['number', 'integer', 'int', 'decimal', 'numeric', 'float', 'double', 'real', 'money', 'currency'])
const DATE_FORMATS = new Set(['date', 'datetime', 'timestamp'])
function inputTypeFor(c: Column): 'text' | 'number' | 'date' {
  const f = (c.format ?? '').toLowerCase()
  const t = (c.type ?? '').toLowerCase()
  if (DATE_FORMATS.has(f) || /\bdate\b|timestamp/.test(t)) return 'date'
  if (NUM_FORMATS.has(f) || /int|numeric|decimal|float|double|real/.test(t)) return 'number'
  return 'text'
}

const Wrap = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
`
const Head = styled.button<{ $open?: boolean }>`
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 7px 10px;
  border: none; background: transparent; cursor: pointer; text-align: left;
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600;
  border-bottom: 1px solid ${({ $open }) => ($open ? colors.border : 'transparent')};
  & svg { color: ${colors.text.muted}; }
  &:hover { color: ${colors.text.primary}; }
  & .count { color: ${colors.blue.main}; }
`
const Body = styled.div`padding: 10px;`
const OpWrap = styled.div`position: relative; flex-shrink: 0;`
const OpBtn = styled.button<{ $open?: boolean }>`
  display: inline-flex; align-items: center; justify-content: center; width: 30px; height: 32px;
  border: 1px solid ${({ $open }) => ($open ? colors.blue.border : colors.border)}; border-radius: ${radius.sm};
  background: ${({ $open }) => ($open ? colors.blue.bg : colors.bg.card)};
  color: ${({ $open }) => ($open ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.base}; font-family: ${fonts.sans}; cursor: pointer; line-height: 1; padding: 0;
  &:hover { border-color: ${colors.blue.border}; color: ${colors.text.primary}; }
`
const OpDrop = styled.div`
  position: absolute; top: calc(100% + 4px); left: 0; z-index: 200; min-width: 168px;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  padding: 4px; box-shadow: ${shadow.lg};
`
const OpItem = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 5px 8px;
  border: none; border-radius: ${radius.md}; cursor: pointer; text-align: left;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
  & .g { width: 16px; text-align: center; flex-shrink: 0; color: ${colors.text.muted}; }
  & .n { flex: 1; }
  & svg { flex-shrink: 0; }
`
const FieldRow = styled.div`display: flex; gap: 4px; align-items: stretch;`

function OpPicker({ value, onChange }: { value: string; onChange: (op: string) => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  return (
    <OpWrap ref={ref}>
      <OpBtn type="button" $open={open} onClick={() => setOpen((v) => !v)} title={`${t('table.filterOp', 'Operator')}: ${t(`table.op_${value}`, value)}`}>
        {OP_GLYPH[value] ?? '~'}
      </OpBtn>
      {open && (
        <OpDrop>
          {OPS.map((op) => (
            <OpItem key={op} type="button" $active={op === value} onClick={() => { onChange(op); setOpen(false) }}>
              <span className="g">{OP_GLYPH[op]}</span>
              <span className="n">{t(`table.op_${op}`, op)}</span>
              {op === value && <Check size={12} />}
            </OpItem>
          ))}
        </OpDrop>
      )}
    </OpWrap>
  )
}

export function FilterPanel({ cols, values, onChange, autoLoad }: {
  cols: Column[]
  values: Record<string, ServerFilter>
  onChange: (name: string, next: ServerFilter) => void
  autoLoad: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(!autoLoad)
  if (cols.length === 0) return null
  const activeCount = cols.filter((c) => (values[c.name]?.val ?? '') !== '').length
  return (
    <Wrap>
      <Head type="button" $open={open} onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Filter size={13} />
        {t('table.filters')}{activeCount ? <span className="count"> ({activeCount})</span> : null}
      </Head>
      {open && (
        <Body>
          <Row align="flex-end">
            {cols.map((c) => {
              const isEnum = c.rule?.kind === 'enum'
              const cur = values[c.name] ?? { op: isEnum ? 'equals' : 'contains', val: '' }
              return (
                <div key={c.name} style={{ minWidth: 210 }}>
                  <Field label={c.label ?? c.name}>
                    <FieldRow>
                      {!isEnum && <OpPicker value={cur.op} onChange={(op) => onChange(c.name, { ...cur, op })} />}
                      {isEnum ? (
                        <Select value={cur.val} onChange={(e) => onChange(c.name, { op: 'equals', val: e.target.value })} style={{ flex: 1 }}>
                          <option value="">{t('table.filterAny')}</option>
                          {c.rule!.kind === 'enum' && c.rule!.values.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
                        </Select>
                      ) : (
                        <Input
                          type={inputTypeFor(c)}
                          placeholder={t('table.serverFilterHint')}
                          value={cur.val}
                          onChange={(e) => onChange(c.name, { ...cur, val: e.target.value })}
                          style={{ flex: 1, minWidth: 0 }}
                        />
                      )}
                    </FieldRow>
                  </Field>
                </div>
              )
            })}
          </Row>
        </Body>
      )}
    </Wrap>
  )
}
