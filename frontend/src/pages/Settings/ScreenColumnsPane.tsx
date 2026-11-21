// The Grid stage's columns: the list on the left — each line says what the column is (its name, its label, the
// marks worth seeing: key, filter, hidden, its rule) — and the picked column's settings beside it, the way a
// service shows its flow and the picked step. The settings are the same form as before (the column's schema,
// its nested rules drilled in the panel); the list replaces the drill-down that hid the others while one was open.
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { GripVertical, Plus, Search, X } from 'lucide-react'
import { SchemaNavigator, type JsonSchema, type NavRoot } from '../../common'
import { Tag } from '../../common/Tag'
import { colors, fontSize, fonts, radius } from '../../theme'
import { columnMarks, moved, selectionAfterMove, selectionAfterRemove, type ColumnMark } from '../../services/screenStages'

type Col = Record<string, unknown>
const TONE: Record<ColumnMark, 'blue' | 'green' | 'orange' | 'purple' | 'neutral'> = {
  key: 'blue', filter: 'green', hidden: 'neutral', required: 'orange', readonly: 'neutral', sensitive: 'purple',
}

const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0;`
const ListSide = styled.div`flex: 0 0 min(460px, 38%); min-width: 0; display: flex; flex-direction: column; min-height: 0;`
const Rows = styled.div`flex: 0 1 auto; min-height: 0; overflow-y: auto; padding-right: 4px;`
const Side = styled.div`
  flex: 1; min-width: 0; min-height: 0; overflow-y: auto; padding: 14px 16px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
`
const SearchRow = styled.div`
  display: flex; align-items: center; gap: 6px; height: 30px; padding: 0 9px; margin-bottom: 8px; flex-shrink: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.muted};
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; &::placeholder { color: ${colors.text.muted}; } }
  & .x { cursor: pointer; display: inline-flex; }
`
const RowBtn = styled.button<{ $on: boolean }>`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 7px 9px; margin-bottom: 4px; text-align: left; cursor: pointer;
  border: 1px solid ${(p) => (p.$on ? colors.blue.main : colors.border)}; border-radius: ${radius.md};
  background: ${(p) => (p.$on ? colors.blue.bg : colors.bg.input)}; font-family: ${fonts.sans};
  & .grip { flex-shrink: 0; color: ${colors.text.muted}; cursor: grab; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .label { font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .marks { display: flex; gap: 4px; flex-shrink: 0; align-items: center; }
  & .remove { flex-shrink: 0; display: inline-flex; padding: 3px; border-radius: ${radius.sm}; color: ${colors.text.muted}; }
  & .remove:hover { color: ${colors.red.main}; background: var(--hover-subtle); }
  &:hover { border-color: ${(p) => (p.$on ? colors.blue.main : colors.blue.border)}; }
`
const AddBtn = styled.button`
  display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; margin-top: 6px; padding: 5px 11px; cursor: pointer; flex-shrink: 0;
  border: 1px dashed ${colors.border}; border-radius: ${radius.md}; background: transparent; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.border}; }
`
const Nothing = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 28px 8px; text-align: center;`

export default function ScreenColumnsPane({ columns, onChange, itemSchema, describe, deriveContext }: {
  columns: Col[]
  onChange: (next: Col[]) => void
  /** one column's schema, carrying its own `$defs` */
  itemSchema: JsonSchema
  /** the label a column shows under its name, and its dictionary item when it differs from the name */
  describe: (col: Col) => { dd: string; label: string }
  deriveContext?: NavRoot['deriveContext']
}) {
  const { t } = useTranslation()
  const [selected, setSelected] = useState<number | null>(columns.length ? 0 : null)
  const [q, setQ] = useState('')
  const dragFrom = useRef<number | null>(null)
  const needle = q.trim().toLowerCase()
  const lines = columns.map((col, i) => {
    const name = typeof col.name === 'string' ? col.name : ''
    const { dd, label } = describe(col)
    return { i, col, name, dd, label, ...columnMarks(col) }
  })
  const shown = needle ? lines.filter((l) => `${l.name} ${l.dd} ${l.label} ${l.rule}`.toLowerCase().includes(needle)) : lines
  const canReorder = !needle && columns.length > 1   // a filtered list is not the whole order
  const current = selected != null && selected < columns.length ? selected : null

  const drop = (to: number) => {
    const from = dragFrom.current
    dragFrom.current = null
    if (from == null || from === to) return
    onChange(moved(columns, from, to))
    setSelected(selectionAfterMove(current, from, to))
  }
  const remove = (i: number) => {
    onChange(columns.filter((_, j) => j !== i))
    setSelected(selectionAfterRemove(current, i, columns.length - 1))
  }
  const add = () => { setQ(''); onChange([...columns, {}]); setSelected(columns.length) }

  return (
    <Split>
      <ListSide>
        {columns.length > 6 && (
          <SearchRow>
            <Search size={13} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('settings.screens.columnsPane.search', { n: columns.length })} />
            {q && <span className="x" role="button" tabIndex={0} title={t('common.clear')} onClick={() => setQ('')} onKeyDown={(e) => { if (e.key === 'Enter') setQ('') }}><X size={12} /></span>}
          </SearchRow>
        )}
        <Rows>
          {shown.map((l) => (
            <RowBtn
              key={l.i} type="button" $on={current === l.i} data-column={l.name} onClick={() => setSelected(l.i)}
              draggable={canReorder}
              onDragStart={() => { dragFrom.current = l.i }}
              onDragOver={canReorder ? (e) => e.preventDefault() : undefined}
              onDrop={canReorder ? (e) => { e.preventDefault(); drop(l.i) } : undefined}
            >
              {canReorder && <GripVertical size={13} className="grip" />}
              <span className="text">
                <span className="name">{l.name || t('settings.screens.columnsPane.unnamed')}{l.dd ? ` · ${l.dd}` : ''}</span>
                {l.label && <span className="label">{l.label}</span>}
              </span>
              <span className="marks">
                {l.rule && <Tag $tone="purple">{l.rule}</Tag>}
                {l.marks.map((m) => <Tag key={m} $tone={TONE[m]}>{t(`settings.screens.columnsPane.mark.${m}`)}</Tag>)}
              </span>
              <span className="remove" role="button" tabIndex={0} title={t('common.remove')} onClick={(e) => { e.stopPropagation(); remove(l.i) }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); remove(l.i) } }}><X size={13} /></span>
            </RowBtn>
          ))}
          {shown.length === 0 && <Nothing>{needle ? t('settings.screens.columnsPane.noMatch') : t('settings.screens.columnsPane.empty')}</Nothing>}
        </Rows>
        <AddBtn type="button" onClick={add}><Plus size={13} /> {t('settings.screens.columnsPane.add')}</AddBtn>
      </ListSide>
      <Side>
        {current == null
          ? <Nothing>{t('settings.screens.columnsPane.pick')}</Nothing>
          : (
            // keyed on the column: another one picked starts at the top of its own settings
            <SchemaNavigator
              key={current}
              root={{
                label: (typeof columns[current].name === 'string' && columns[current].name as string) || t('settings.screens.columnsPane.unnamed'),
                schema: itemSchema,
                value: columns[current],
                onChange: (v) => onChange(columns.map((c, j) => (j === current ? v : c))),
                deriveContext,
              }}
            />
          )}
      </Side>
    </Split>
  )
}
