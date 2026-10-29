// The shelves: Rows (dimensions, a date one with its bucket), Measures (an aggregate each, "+"
// for a row count) and Filters (an operator by type and a value — an enum or lookup field picks
// from its values, a date gets from → to). Chips reorder by drag within a shelf; a field dragged
// from the pane drops on a shelf. In detail view the Rows shelf is the column list.
import { useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { X, Plus, Rows3, Sigma, Filter } from 'lucide-react'
import { DatePicker, Input, SearchSelect, type SearchSelectOption } from '../../common'
import { useLookupBatch, type LookupSpec } from '../../services/lookups'
import { BUCKETS, NUMERIC_AGGREGATES, moveItem, opsFor } from '../../services/explorer'
import type { Aggregate, AnalysisDefinition, Bucket, ExplorerField, FilterOp, FilterRow } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'
import { FIELD_DND } from './FieldsPane'

const Shelf = styled.div<{ $over?: boolean }>`
  display: flex; align-items: center; gap: 6px; min-height: 40px; padding: 4px 8px; flex-wrap: wrap;
  border: 1px dashed ${({ $over }) => ($over ? colors.blue.main : colors.border)}; border-radius: ${radius.md};
  background: ${({ $over }) => ($over ? colors.blue.bg : 'transparent')};
`
const ShelfLabel = styled.span`display: inline-flex; align-items: center; gap: 5px; min-width: 84px; font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em; font-family: ${fonts.sans};`
const Chip = styled.div<{ $dragging?: boolean }>`
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 4px 2px 8px; border-radius: ${radius.md}; cursor: grab;
  border: 1px solid ${colors.blue.border}; background: ${colors.blue.bg}; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  opacity: ${({ $dragging }) => ($dragging ? 0.5 : 1)};
  & .x { border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; padding: 2px; &:hover { color: ${colors.red.main}; } }
  & .sel { min-width: 90px; }
`
const Hint = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`
const AddBtn = styled.button`border: 1px dashed ${colors.border}; background: transparent; color: ${colors.text.muted}; border-radius: ${radius.md}; cursor: pointer; display: inline-flex; align-items: center; padding: 4px 6px; &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }`
const Narrow = styled.div`width: 150px;`

export function Shelves({ def, fields, onChange }: {
  def: AnalysisDefinition
  fields: Map<string, ExplorerField>
  onChange: (next: AnalysisDefinition) => void
}) {
  const { t } = useTranslation()
  const [over, setOver] = useState<'rows' | 'measures' | 'filters' | null>(null)
  const [drag, setDrag] = useState<{ shelf: string; index: number } | null>(null)
  const label = (key: string | null) => (key ? fields.get(key)?.label ?? key : t('explorer.rowCount'))

  const dropField = (shelf: 'rows' | 'measures' | 'filters') => (ev: React.DragEvent) => {
    ev.preventDefault()
    setOver(null)
    const key = ev.dataTransfer.getData(FIELD_DND)
    const internal = ev.dataTransfer.getData('text/plain')
    if (key && fields.has(key)) {
      const f = fields.get(key)!
      if (shelf === 'rows' && !def.rows.some((r) => r.field === key)) {
        const bucket: Bucket | null = def.view !== 'detail' && (f.type === 'date' || f.type === 'datetime') ? 'month' : null
        onChange({ ...def, rows: [...def.rows, bucket ? { field: key, bucket } : { field: key }] })
      } else if (shelf === 'measures') {
        // a dimension dropped on Measures counts its distinct values
        const agg: Aggregate = f.kind === 'measure' ? (f.aggregate ?? 'sum') : 'count_distinct'
        onChange({ ...def, measures: [...def.measures, { field: key, agg }] })
      } else if (shelf === 'filters' && !def.filterRows.some((r) => r.field === key)) {
        onChange({ ...def, filterRows: [...def.filterRows, { field: key, op: opsFor(f.type)[0], value: '' }] })
      }
      return
    }
    if (internal && drag && drag.shelf === shelf) {
      // reorder: the drop target is the chip under the cursor, found by data-index up the tree
      const target = (ev.target as HTMLElement).closest('[data-index]') as HTMLElement | null
      const to = target ? Number(target.dataset.index) : (shelf === 'rows' ? def.rows : shelf === 'measures' ? def.measures : def.filterRows).length - 1
      if (shelf === 'rows') onChange({ ...def, rows: moveItem(def.rows, drag.index, to) })
      else if (shelf === 'measures') onChange({ ...def, measures: moveItem(def.measures, drag.index, to) })
      else onChange({ ...def, filterRows: moveItem(def.filterRows, drag.index, to) })
    }
    setDrag(null)
  }
  const allowDrop = (shelf: 'rows' | 'measures' | 'filters') => (ev: React.DragEvent) => { ev.preventDefault(); setOver(shelf) }
  const chipDrag = (shelf: string, index: number) => (ev: React.DragEvent) => {
    setDrag({ shelf, index }); ev.dataTransfer.setData('text/plain', String(index)); ev.dataTransfer.effectAllowed = 'move'
  }

  const bucketOpts: SearchSelectOption[] = BUCKETS.map((b) => ({ value: b, label: t(`explorer.bucket.${b}`) }))
  const aggOpts = (f: ExplorerField | undefined): SearchSelectOption[] =>
    (f && f.type !== 'number' ? (['count', 'count_distinct'] as Aggregate[]) : NUMERIC_AGGREGATES).map((a) => ({ value: a, label: t(`explorer.agg.${a}`) }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Shelf $over={over === 'rows'} onDragOver={allowDrop('rows')} onDragLeave={() => setOver(null)} onDrop={dropField('rows')}>
        <ShelfLabel><Rows3 size={12} /> {def.view === 'detail' ? t('explorer.columns') : t('explorer.rows')}</ShelfLabel>
        {def.rows.length === 0 && <Hint>{t('explorer.rowsHint')}</Hint>}
        {def.rows.map((r, i) => {
          const f = fields.get(r.field)
          const isDate = f?.type === 'date' || f?.type === 'datetime'
          return (
            <Chip key={r.field} data-index={i} draggable onDragStart={chipDrag('rows', i)} onDragEnd={() => setDrag(null)} $dragging={drag?.shelf === 'rows' && drag.index === i}>
              <span>{label(r.field)}</span>
              {isDate && def.view !== 'detail' && (
                <span className="sel"><SearchSelect value={r.bucket ?? ''} anyLabel={t('explorer.bucket.none')} options={bucketOpts}
                  onChange={(v) => onChange({ ...def, rows: def.rows.map((x, k) => (k === i ? { field: x.field, ...(v ? { bucket: v as Bucket } : {}) } : x)) })} /></span>
              )}
              <button type="button" className="x" onClick={() => onChange({ ...def, rows: def.rows.filter((_, k) => k !== i) })}><X size={12} /></button>
            </Chip>
          )
        })}
      </Shelf>
      {def.view !== 'detail' && (
        <Shelf $over={over === 'measures'} onDragOver={allowDrop('measures')} onDragLeave={() => setOver(null)} onDrop={dropField('measures')}>
          <ShelfLabel><Sigma size={12} /> {t('explorer.measures')}</ShelfLabel>
          {def.measures.length === 0 && <Hint>{t('explorer.measuresHint')}</Hint>}
          {def.measures.map((m, i) => {
            const f = m.field ? fields.get(m.field) : undefined
            return (
              <Chip key={`${m.field ?? '*'}:${i}`} data-index={i} draggable onDragStart={chipDrag('measures', i)} onDragEnd={() => setDrag(null)} $dragging={drag?.shelf === 'measures' && drag.index === i}>
                {m.field ? (
                  <span className="sel"><SearchSelect value={m.agg} options={aggOpts(f)} onChange={(v) => onChange({ ...def, measures: def.measures.map((x, k) => (k === i ? { ...x, agg: v as Aggregate } : x)) })} /></span>
                ) : null}
                <span>{label(m.field)}</span>
                <button type="button" className="x" onClick={() => onChange({ ...def, measures: def.measures.filter((_, k) => k !== i) })}><X size={12} /></button>
              </Chip>
            )
          })}
          <AddBtn type="button" title={t('explorer.addCount')} onClick={() => onChange({ ...def, measures: [...def.measures, { field: null, agg: 'count' }] })}><Plus size={12} /></AddBtn>
        </Shelf>
      )}
      <Shelf $over={over === 'filters'} onDragOver={allowDrop('filters')} onDragLeave={() => setOver(null)} onDrop={dropField('filters')}>
        <ShelfLabel><Filter size={12} /> {t('explorer.filters')}</ShelfLabel>
        {def.filterRows.length === 0 && <Hint>{t('explorer.filtersHint')}</Hint>}
        {def.filterRows.map((r, i) => (
          <FilterChip key={r.field} index={i} row={r} field={fields.get(r.field)} dragging={drag?.shelf === 'filters' && drag.index === i}
            onDragStart={chipDrag('filters', i)} onDragEnd={() => setDrag(null)}
            onChange={(next) => onChange({ ...def, filterRows: def.filterRows.map((x, k) => (k === i ? next : x)) })}
            onRemove={() => onChange({ ...def, filterRows: def.filterRows.filter((_, k) => k !== i) })} />
        ))}
      </Shelf>
    </div>
  )
}

function FilterChip({ index, row, field, dragging, onDragStart, onDragEnd, onChange, onRemove }: {
  index: number; row: FilterRow; field: ExplorerField | undefined; dragging: boolean
  onDragStart: (ev: React.DragEvent) => void; onDragEnd: () => void
  onChange: (next: FilterRow) => void; onRemove: () => void
}) {
  const { t } = useTranslation()
  const type = field?.type ?? 'string'
  const rule = field?.rule
  const specs = useMemo<LookupSpec[]>(() => (rule?.kind === 'lookup' ? [{ connector: rule.connector, query: rule.query, value: rule.value, label: rule.label, params: rule.params, sources: rule.sources }] : []), [rule])
  const lookups = useLookupBatch(specs)
  const valueOpts = useMemo<SearchSelectOption[]>(() => {
    if (rule?.kind === 'enum') return rule.values.map((v) => ({ value: v.value, label: v.label, mono: v.value }))
    if (rule?.kind === 'lookup') {
      const m = lookups.values().next().value as Map<string, string> | undefined
      return m ? [...m.entries()].map(([v, l]) => ({ value: v, label: l, mono: v })) : []
    }
    return []
  }, [rule, lookups])
  const ops: SearchSelectOption[] = opsFor(type).map((o) => ({ value: o, label: t(`explorer.op.${o}`) }))
  // a local draft so typing does not re-run the analysis on every key; Enter / blur commits
  const [draft, setDraft] = useState(row.value)
  const [draftTo, setDraftTo] = useState(row.valueTo ?? '')
  const commit = () => { if (draft !== row.value || draftTo !== (row.valueTo ?? '')) onChange({ ...row, value: draft, valueTo: draftTo || undefined }) }
  const inputType = type === 'date' ? 'date' : type === 'datetime' ? 'datetime-local' : type === 'number' ? 'number' : 'text'
  const noValue = row.op === 'empty' || row.op === 'notEmpty'
  const inOp = row.op === 'in'
  // an `in` on a coded field: a picker that appends codes to the comma list
  const pickerIn = inOp && valueOpts.length > 0

  return (
    <Chip data-index={index} draggable onDragStart={onDragStart} onDragEnd={onDragEnd} $dragging={dragging}>
      <span>{field?.label ?? row.field}</span>
      <span className="sel"><SearchSelect value={row.op} options={ops} onChange={(v) => onChange({ ...row, op: v as FilterOp, value: '', valueTo: undefined })} /></span>
      {!noValue && row.op === 'between' && (
        <>
          <Narrow>{type === 'date'
            ? <DatePicker value={draft} onChange={(v) => { setDraft(v); onChange({ ...row, value: v, valueTo: draftTo || undefined }) }} />
            : <Input type={inputType} value={draft} onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit() }} />}</Narrow>
          <span style={{ color: colors.text.muted }}>→</span>
          <Narrow>{type === 'date'
            ? <DatePicker value={draftTo} onChange={(v) => { setDraftTo(v); onChange({ ...row, value: draft, valueTo: v || undefined }) }} />
            : <Input type={inputType} value={draftTo} onChange={(e) => setDraftTo(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit() }} />}</Narrow>
        </>
      )}
      {!noValue && row.op !== 'between' && valueOpts.length > 0 && !pickerIn && (
        <Narrow><SearchSelect value={row.value} options={valueOpts} anyLabel={t('explorer.anyValue')} onChange={(v) => onChange({ ...row, value: v })} /></Narrow>
      )}
      {!noValue && row.op !== 'between' && (valueOpts.length === 0 || pickerIn) && (
        <>
          <Narrow>{type === 'date' && !inOp
            ? <DatePicker value={draft} onChange={(v) => { setDraft(v); onChange({ ...row, value: v, valueTo: draftTo || undefined }) }} />
            : <Input type={inOp ? 'text' : inputType} value={draft} placeholder={inOp ? t('explorer.inPlaceholder') : ''} onChange={(e) => setDraft(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === 'Enter') commit() }} />}</Narrow>
          {pickerIn && (
            <Narrow><SearchSelect value="" options={valueOpts} placeholder={t('explorer.pickValue')} onChange={(v) => { if (!v) return; const next = [...draft.split(',').map((s) => s.trim()).filter(Boolean), v].join(', '); setDraft(next); onChange({ ...row, value: next }) }} /></Narrow>
          )}
        </>
      )}
      <button type="button" className="x" onClick={onRemove}><X size={12} /></button>
    </Chip>
  )
}
