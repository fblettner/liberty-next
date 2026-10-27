// The fields pane: the model's entities (collapsible, with a 1..1 / 1..n badge), dimensions first
// then the Σ measures, a search box. A click adds the field to the analysis (rows or measures by
// kind), the funnel adds it as a filter, a drag drops it on a shelf. Fields already used are
// highlighted. The `calc` pseudo-entity holds the calculated fields (new / edit come in part 2).
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Filter, Hash, Calendar, Type, ToggleLeft, Sigma, Search, Plus, Pencil, Trash2, ChevronsDownUp, ChevronsUpDown } from 'lucide-react'
import type { ExplorerEntity, ExplorerField } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'

export const FIELD_DND = 'application/x-liberty-field'

const Pane = styled.div`display: flex; flex-direction: column; min-height: 0; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card}; overflow: hidden;`
const SearchBox = styled.div`
  display: flex; align-items: center; gap: 6px; padding: 6px 10px; border-bottom: 1px solid ${colors.border}; color: ${colors.text.muted};
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; &::placeholder { color: ${colors.text.muted}; } }
  & .all { border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; padding: 2px; &:hover { color: ${colors.text.primary}; } }
`
const List = styled.div`flex: 1; min-height: 0; overflow-y: auto; padding: 4px 0;`
const EntityHead = styled.button`
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 6px 10px; border: none; background: transparent; cursor: pointer; text-align: left;
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-weight: 600; font-family: ${fonts.sans};
  &:hover { background: var(--hover-subtle); }
`
const Badge = styled.span<{ $many?: boolean }>`
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; padding: 0 5px; border-radius: ${radius.sm};
  border: 1px solid ${({ $many }) => ($many ? colors.orange.border : colors.border)}; color: ${({ $many }) => ($many ? colors.orange.main : colors.text.muted)};
`
const Group = styled.div`padding: 2px 0 4px; font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em; padding-left: 26px; display: flex; align-items: center; gap: 4px;`
const Row = styled.div<{ $used?: boolean }>`
  display: flex; align-items: center; gap: 6px; padding: 3px 8px 3px 26px; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  color: ${({ $used }) => ($used ? colors.blue.main : colors.text.secondary)};
  & .icon { flex-shrink: 0; color: ${colors.text.muted}; }
  & .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .code { flex-shrink: 0; font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .funnel { opacity: 0; color: ${colors.text.muted}; border: none; background: transparent; cursor: pointer; padding: 0 2px; display: inline-flex; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; & .funnel { opacity: 1; } }
`
const Empty = styled.div`padding: 16px 10px; color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const NewCalc = styled.button`
  display: flex; align-items: center; gap: 6px; width: 100%; margin-top: 6px; padding: 6px 10px; border: none; border-top: 1px dashed ${colors.border};
  background: transparent; cursor: pointer; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
`
const Mini = styled.button`border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; padding: 0 2px; display: inline-flex; opacity: 0; &:hover { color: ${colors.text.primary}; }`

function CalcBtns({ name, onEdit, onDelete }: { name: string; onEdit: (n: string) => void; onDelete: (n: string) => void }) {
  return (
    <>
      <Mini type="button" className="funnel" onClick={(ev) => { ev.stopPropagation(); onEdit(name) }}><Pencil size={11} /></Mini>
      <Mini type="button" className="funnel" onClick={(ev) => { ev.stopPropagation(); onDelete(name) }}><Trash2 size={11} /></Mini>
    </>
  )
}

function TypeIcon({ f }: { f: ExplorerField }) {
  if (f.kind === 'measure') return <Sigma className="icon" size={12} />
  if (f.type === 'number') return <Hash className="icon" size={12} />
  if (f.type === 'date' || f.type === 'datetime') return <Calendar className="icon" size={12} />
  if (f.type === 'boolean') return <ToggleLeft className="icon" size={12} />
  return <Type className="icon" size={12} />
}

export function FieldsPane({ entities, primary, used, onAdd, onFilter, onPrimary, onCalcNew, onCalcEdit, onCalcDelete }: {
  entities: ExplorerEntity[]
  primary: string
  used: Set<string>
  onAdd: (f: ExplorerField) => void
  onFilter: (f: ExplorerField) => void
  onPrimary: (entity: string) => void
  onCalcNew: () => void
  onCalcEdit: (name: string) => void
  onCalcDelete: (name: string) => void
}) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({})
  // A JDE table has a hundred columns: the pane opens with the analysed entity expanded, the
  // others collapsed; reset when the model (its entity ids) changes.
  const idsKey = entities.map((e) => e.id).join('|')
  useEffect(() => {
    setCollapsed(Object.fromEntries(entities.filter((e) => e.id !== primary && e.id !== 'calc').map((e) => [e.id, true])))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- reset on a new model only, not on a primary change
  }, [idsKey])
  const setAll = (open: boolean) => setCollapsed(open ? {} : Object.fromEntries(entities.map((e) => [e.id, true])))
  const needle = search.trim().toLowerCase()
  const visible = useMemo(() => entities.map((e) => ({
    e, fields: e.fields.filter((f) => !needle || f.label.toLowerCase().includes(needle) || f.name.toLowerCase().includes(needle)),
  })).filter((x) => x.fields.length > 0 || !needle), [entities, needle])

  const start = (f: ExplorerField) => (ev: React.DragEvent) => {
    ev.dataTransfer.setData(FIELD_DND, f.key)
    ev.dataTransfer.effectAllowed = 'copy'
  }

  return (
    <Pane>
      <SearchBox>
        <Search size={13} /><input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('explorer.searchFields')} />
        <button type="button" className="all" title={t('explorer.expandAll')} onClick={() => setAll(true)}><ChevronsUpDown size={13} /></button>
        <button type="button" className="all" title={t('explorer.collapseAll')} onClick={() => setAll(false)}><ChevronsDownUp size={13} /></button>
      </SearchBox>
      <List>
        {visible.length === 0 && <Empty>{t('explorer.noFields')}</Empty>}
        {visible.map(({ e, fields }) => {
          const open = !collapsed[e.id] || !!needle
          const dims = fields.filter((f) => f.kind === 'dimension')
          const meas = fields.filter((f) => f.kind === 'measure')
          return (
            <div key={e.id}>
              <EntityHead type="button" onClick={() => setCollapsed((c) => ({ ...c, [e.id]: !c[e.id] }))} onDoubleClick={() => onPrimary(e.id)}
                title={e.id === primary ? t('explorer.primaryEntity') : t('explorer.makePrimary')}>
                {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                <span style={{ flex: 1, color: e.id === primary ? colors.blue.main : undefined }}>{e.label}</span>
                {e.id !== 'calc' && <Badge $many={e.cardinality === 'many'}>{e.cardinality === 'many' ? '1..n' : '1..1'}</Badge>}
                {e.error && <Badge title={e.error} style={{ color: colors.red.main, borderColor: colors.red.border }}>!</Badge>}
              </EntityHead>
              {open && dims.map((f) => (
                <Row key={f.key} $used={used.has(f.key)} draggable onDragStart={start(f)} onClick={() => onAdd(f)} title={f.key}>
                  <TypeIcon f={f} /><span className="label">{f.label}</span>
                  {f.label !== f.name && e.id !== 'calc' && <span className="code">{f.name}</span>}
                  {e.id === 'calc' && <CalcBtns name={f.name} onEdit={onCalcEdit} onDelete={onCalcDelete} />}
                  <button type="button" className="funnel" title={t('explorer.addFilter')} onClick={(ev) => { ev.stopPropagation(); onFilter(f) }}><Filter size={11} /></button>
                </Row>
              ))}
              {open && meas.length > 0 && <Group><Sigma size={10} /> {t('explorer.measures')}</Group>}
              {open && meas.map((f) => (
                <Row key={f.key} $used={used.has(f.key)} draggable onDragStart={start(f)} onClick={() => onAdd(f)} title={f.key}>
                  <TypeIcon f={f} /><span className="label">{f.label}</span>
                  {f.label !== f.name && e.id !== 'calc' && <span className="code">{f.name}</span>}
                  {e.id === 'calc' && <CalcBtns name={f.name} onEdit={onCalcEdit} onDelete={onCalcDelete} />}
                  <button type="button" className="funnel" title={t('explorer.addFilter')} onClick={(ev) => { ev.stopPropagation(); onFilter(f) }}><Filter size={11} /></button>
                </Row>
              ))}
            </div>
          )
        })}
        {!needle && (
          <NewCalc type="button" onClick={onCalcNew}><Plus size={12} /> {t('explorer.newCalcField')}</NewCalc>
        )}
      </List>
    </Pane>
  )
}
