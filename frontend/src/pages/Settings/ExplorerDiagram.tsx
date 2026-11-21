// An Explorer model drawn: the hub in the middle, the details (several rows per hub row) on its left, what a hub
// row has one of on its right, each relation a line saying the columns it joins on — and, set apart in orange,
// the entities that do not reach the hub (an analysis cannot use them). A click on a card or on a line picks it;
// the panel beside edits it. The places are computed from the model (services/explorerDiagram.ts, tested).
import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Database, Star } from 'lucide-react'
import { Tag } from '../../common/Tag'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { ExplorerModelBody } from '../../types/config'
import { edgeLabel, link, modelDiagram, place, type NodeRole } from '../../services/explorerDiagram'

export type DiagramPick = { kind: 'entity'; id: string } | { kind: 'relation'; index: number } | null

// (the gap between two columns is where a relation's label sits: it must hold it)
const CARD = { w: 232, h: 78, gapX: 150, gapY: 18 }
const BOW = 110        // room on the right for a relation between two cards of the same column

const Board = styled.div`position: relative;`
const Lines = styled.svg`position: absolute; left: 0; top: 0; overflow: visible; pointer-events: none;`
const LooseTitle = styled.div`
  position: absolute; left: 0; font-family: ${fonts.sans}; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.orange.main};
`
const Node = styled.button<{ $role: NodeRole; $on: boolean }>`
  position: absolute; display: flex; flex-direction: column; gap: 3px; padding: 8px 11px; text-align: left; cursor: pointer; overflow: hidden;
  font-family: ${fonts.sans}; border-radius: ${radius.lg};
  background: ${(p) => (p.$on ? colors.blue.bg : p.$role === 'hub' ? colors.blue.bg : 'var(--bg-modal)')};
  border: ${(p) => (p.$role === 'hub' ? 2 : 1)}px solid ${(p) => (p.$on ? colors.blue.main : p.$role === 'hub' ? colors.blue.border : p.$role === 'loose' ? colors.orange.border : colors.border)};
  box-shadow: ${(p) => (p.$on ? 'var(--shadow-focus)' : 'none')};
  &:hover { border-color: ${colors.blue.main}; }
  & .top { display: flex; align-items: center; gap: 6px; min-width: 0; }
  & .top svg { flex-shrink: 0; color: ${(p) => (p.$role === 'loose' ? colors.orange.main : colors.blue.main)}; }
  & .id { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; }
  & .label { font-size: ${fontSize.sm}; color: ${colors.text.secondary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .meta { font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`
const EdgeChip = styled.button<{ $on: boolean; $bad: boolean }>`
  position: absolute; transform: translate(-50%, -50%); padding: 1px 8px; cursor: pointer; white-space: nowrap; max-width: ${CARD.gapX - 22}px; overflow: hidden; text-overflow: ellipsis;
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; border-radius: ${radius.sm};
  color: ${(p) => (p.$bad ? colors.orange.main : p.$on ? colors.blue.main : colors.text.secondary)};
  background: ${colors.bg.dropdown};
  border: 1px solid ${(p) => (p.$bad ? colors.orange.border : p.$on ? colors.blue.main : colors.border)};
  &:hover { border-color: ${colors.blue.main}; color: ${colors.blue.main}; }
`

export default function ExplorerDiagram({ model, app, picked, onPick }: {
  model: ExplorerModelBody; app: string; picked: DiagramPick; onPick: (pick: DiagramPick) => void
}) {
  const { t } = useTranslation()
  const diagram = useMemo(() => modelDiagram(model), [model])
  const placed = useMemo(() => place(diagram, CARD), [diagram])
  const pickedId = picked?.kind === 'entity' ? picked.id : null
  const pickedEdge = picked?.kind === 'relation' ? picked.index : null
  return (
    <Board style={{ width: placed.width + BOW, height: placed.height + 8 }} data-diagram>
      <Lines width={placed.width + BOW} height={placed.height + 8}>
        {diagram.edges.map((e) => {
          const a = placed.boxes[e.from], b = placed.boxes[e.to]
          if (!a || !b) return null
          const on = pickedEdge === e.index || pickedId === e.from || pickedId === e.to
          return (
            <path key={e.index} d={link(a, b).path} fill="none" strokeWidth={on ? 2 : 1.5}
              stroke={!e.complete ? colors.orange.main : on ? colors.blue.main : colors.border} strokeDasharray={e.complete ? undefined : '5 4'} />
          )
        })}
      </Lines>
      {placed.looseTop != null && <LooseTitle style={{ top: placed.looseTop - 20 }}>{t('settings.explorer.diagram.loose')}</LooseTitle>}
      {diagram.nodes.map((n) => {
        const e = model.entities[n.id]
        const box = placed.boxes[n.id]
        const measures = Object.keys(e.measures ?? {}).length
        const source = e.query ? `${e.connector || app}.${e.query}` : `${e.schema ? `${e.schema}.` : ''}${e.table ?? ''}`
        return (
          <Node key={n.id} type="button" $role={n.role} $on={pickedId === n.id} data-entity={n.id} data-role={n.role}
            style={{ left: box.x, top: box.y, width: box.w, height: box.h }} onClick={() => onPick({ kind: 'entity', id: n.id })}>
            <span className="top">
              {n.role === 'hub' ? <Star size={13} /> : <Database size={13} />}
              <span className="id">{n.id}</span>
              {n.role === 'hub'
                ? <Tag $tone="blue">{t('settings.explorer.hub')}</Tag>
                : <Tag $tone={n.role === 'many' ? 'orange' : 'neutral'}>{n.role === 'many' ? '1..n' : '1..1'}</Tag>}
            </span>
            <span className="label">{e.label || source}</span>
            <span className="meta">
              {[e.label ? source : '', e.key?.length ? `${t('settings.explorer.key')}: ${e.key.join(', ')}` : '', measures ? `Σ ${measures}` : ''].filter(Boolean).join(' · ')}
            </span>
          </Node>
        )
      })}
      {diagram.edges.map((e) => {
        const a = placed.boxes[e.from], b = placed.boxes[e.to]
        if (!a || !b) return null
        const { mid } = link(a, b)
        return (
          <EdgeChip key={e.index} type="button" $on={pickedEdge === e.index} $bad={!e.complete} data-relation={e.index}
            style={{ left: mid.x, top: mid.y }} title={e.pairs.map((p) => `${e.from}.${p[0]} = ${e.to}.${p[1]}`).join('\n') || t('settings.explorer.diagram.noPair')}
            onClick={() => onPick({ kind: 'relation', index: e.index })}>
            {edgeLabel(e)}
          </EdgeChip>
        )
      })}
    </Board>
  )
}
