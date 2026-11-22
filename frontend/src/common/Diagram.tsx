// A diagram the server laid out (liberty/diagram.py), drawn: the tiers as columns, the hosts as boxes inside
// them, a card per node — its state as a coloured mark and a word, a dashed outline when it is not declared —
// and each link on the path the server gave it. Nothing is placed here: the document's painter (render_svg, in
// Python) draws the same coordinates, so the page and the PDF show one picture. A click on a card calls
// `onPick`. The few words it writes (the states, "not declared") are props: the caller owns its texts.
// NOT barrelled: used by the pages an application ships, as `sdk.Diagram` (pages/AppPage/sdk.ts).
import { useMemo } from 'react'
import styled from '@emotion/styled'
import { FlowCanvas } from './flowKit'
import { colors, fontSize, fonts, radius, shadow } from '../theme'
import { badgeLine, evidenceRank, legendOf, linksOf, readDiagram, type DiagramState, type PlacedDiagram, type PlacedNode } from '../services/diagram'

export interface DiagramWords { ok: string; warn: string; down: string; unknown: string; undeclared: string }
const WORDS: DiagramWords = { ok: 'OK', warn: 'To review', down: 'Down', unknown: 'Not known', undeclared: 'Not declared' }

export interface DiagramProps {
  /** the laid-out diagram, as the server answered it (`lay_out`): read tolerantly, drawn at its coordinates */
  layout: unknown
  /** the id of the card that is picked (highlighted with its links) */
  picked?: string | null
  /** a click on a card */
  onPick?: (node: PlacedNode) => void
  /** the words of the states and of what is not declared, in the user's language (English when left out) */
  words?: Partial<DiagramWords>
  /** the zoom is remembered per kind of diagram on this device, under this key */
  zoomKey?: string
  /** the drawing alone, at its own size: no canvas, no zoom, no legend */
  bare?: boolean
}

const STATE_COLOR: Record<DiagramState, string> = { ok: colors.green.main, warn: colors.orange.main, down: colors.red.main, unknown: colors.text.muted }
// a line's colour is its evidence's rank (the states keep green / orange / red for themselves)
const LINK_COLORS = [colors.blue.main, colors.purple.main, colors.yellow.main, colors.ai.main]
const linkColor = (rank: number): string => (rank < 0 ? colors.text.muted : LINK_COLORS[rank % LINK_COLORS.length])

const Root = styled.div`display: flex; flex-direction: column; gap: 8px; flex: 1 1 auto; min-height: 0; min-width: 0;`
const Board = styled.div`position: relative; font-family: ${fonts.sans};`
const Tier = styled.div<{ $dashed: boolean }>`
  position: absolute; box-sizing: border-box; border-radius: ${radius.md}; background: ${colors.bg.input};
  border: 1px ${(p) => (p.$dashed ? 'dashed' : 'solid')} ${(p) => (p.$dashed ? colors.text.muted : colors.border)};
  & > span { position: absolute; left: 8px; right: 8px; top: 5px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase;
    color: ${colors.text.muted}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
`
const Host = styled.div<{ $dashed: boolean }>`
  position: absolute; box-sizing: border-box; border-radius: ${radius.sm}; background: ${colors.bg.card};
  border: 1px ${(p) => (p.$dashed ? 'dashed' : 'solid')} ${(p) => (p.$dashed ? colors.text.muted : colors.border)};
  & > span { position: absolute; left: 6px; right: 6px; top: 1px; font-size: ${fontSize.micro}; color: ${colors.text.secondary};
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
`
const Lines = styled.svg`position: absolute; left: 0; top: 0; overflow: visible; pointer-events: none;`
const Card = styled.button<{ $state: DiagramState; $dashed: boolean; $on: boolean; $dim: boolean }>`
  position: absolute; box-sizing: border-box; display: flex; flex-direction: column; justify-content: center; gap: 1px; padding: 3px 8px 3px 11px; margin: 0;
  text-align: left; cursor: pointer; overflow: hidden; font-family: ${fonts.sans}; line-height: 1.25; border-radius: ${radius.sm};
  background: ${(p) => (p.$on ? colors.blue.bg : colors.bg.modal)};
  border: 1px ${(p) => (p.$dashed ? 'dashed' : 'solid')} ${(p) => (p.$on ? colors.blue.main : p.$dashed ? colors.text.secondary : colors.border)};
  box-shadow: ${(p) => (p.$on ? shadow.focus : 'none')}; opacity: ${(p) => (p.$dim ? 0.55 : 1)};
  &:hover { border-color: ${colors.blue.main}; opacity: 1; }
  &::before { content: ''; position: absolute; left: 3px; top: 5px; bottom: 5px; width: 3px; border-radius: 2px; background: ${(p) => STATE_COLOR[p.$state]}; }
  & span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 100%; }
  & .l { font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; }
  & .d { font-size: ${fontSize.micro}; color: ${colors.text.secondary}; }
  & .s { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .s b { font-weight: 600; color: ${(p) => (p.$state === 'unknown' ? colors.text.secondary : STATE_COLOR[p.$state])}; }
`
const Chip = styled.span<{ $on: boolean }>`
  position: absolute; transform: translate(-50%, -50%); padding: 0 5px; white-space: nowrap; max-width: 120px; overflow: hidden; text-overflow: ellipsis; pointer-events: none;
  font-size: ${fontSize.micro}; border-radius: ${radius.sm}; background: ${colors.bg.dropdown};
  color: ${(p) => (p.$on ? colors.text.primary : colors.text.secondary)}; border: 1px solid ${(p) => (p.$on ? colors.blue.border : colors.border)};
`
const Legend = styled.div`
  display: flex; flex-wrap: wrap; align-items: center; gap: 4px 16px; flex: 0 0 auto; font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & > span { display: inline-flex; align-items: center; gap: 6px; }
  & .note { color: ${colors.text.muted}; flex-basis: 100%; }
`
const Mark = styled.i<{ $color: string }>`display: inline-block; width: 3px; height: 11px; border-radius: 2px; background: ${(p) => p.$color};`
const Dashed = styled.i`display: inline-block; width: 16px; height: 10px; border-radius: 3px; border: 1px dashed ${colors.text.secondary};`
const Stroke = styled.i<{ $color: string }>`display: inline-block; width: 16px; height: 0; border-top: 2px solid ${(p) => p.$color};`

function Drawing({ d, picked, onPick, w }: { d: PlacedDiagram; picked: string | null; onPick?: (node: PlacedNode) => void; w: DiagramWords }) {
  // what the picked card touches: its links, and the cards and boxes at their other ends
  const lit = useMemo(() => {
    if (!picked) return null
    const links = linksOf(d, picked)
    return { links: new Set(links), ends: new Set([picked, ...links.flatMap((k) => [k.from, k.to])]) }
  }, [d, picked])
  return (
    <Board style={{ width: d.width, height: d.height }} data-diagram={d.nodes.length}>
      {d.groups.filter((g) => !g.parent).map((g) => (
        <Tier key={g.id} $dashed={!g.declared} data-diagram-group={g.id} style={{ left: g.x, top: g.y, width: g.w, height: g.h }}><span title={g.label}>{g.label}</span></Tier>
      ))}
      {d.groups.filter((g) => g.parent).map((g) => (
        <Host key={g.id} $dashed={!g.declared} data-diagram-group={g.id} data-declared={g.declared} style={{ left: g.x, top: g.y, width: g.w, height: g.h }}><span title={g.label}>{g.label}</span></Host>
      ))}
      <Lines width={d.width} height={d.height}>
        {d.links.map((k, i) => {
          const color = linkColor(evidenceRank(d, k.evidence))
          const on = !!lit?.links.has(k)
          return (
            <g key={i} opacity={lit && !on ? 0.3 : 1}>
              <path d={k.path} fill="none" stroke={color} strokeWidth={on ? 2 : 1.3} />
              {k.arrow && <path d={k.arrow} fill={color} />}
            </g>
          )
        })}
      </Lines>
      {d.nodes.map((n) => {
        const rest = badgeLine(n)
        return (
          <Card key={n.id} type="button" $state={n.state} $dashed={!n.declared} $on={picked === n.id} $dim={!!lit && !lit.ends.has(n.id) && !lit.ends.has(n.group)}
            data-diagram-node={n.id} data-state={n.state} data-declared={n.declared} title={[n.label, n.detail].filter(Boolean).join(' — ')}
            style={{ left: n.x, top: n.y, width: n.w, height: n.h }} onClick={() => onPick?.(n)}>
            <span className="l">{n.label}</span>
            {n.detail && <span className="d">{n.detail}</span>}
            <span className="s"><b>{w[n.state]}</b>{rest ? ` · ${rest}` : ''}</span>
          </Card>
        )
      })}
      {d.links.map((k, i) => k.label && (
        <Chip key={i} $on={!!lit?.links.has(k)} data-diagram-link={`${k.from}>${k.to}`} style={{ left: k.x, top: k.y, opacity: lit && !lit.links.has(k) ? 0.4 : 1 }}>{k.label}</Chip>
      ))}
    </Board>
  )
}

export function Diagram({ layout, picked = null, onPick, words, zoomKey = 'diagram', bare }: DiagramProps) {
  const d = useMemo(() => readDiagram(layout), [layout])
  const w = useMemo(() => {
    const out = { ...WORDS }
    for (const k of Object.keys(WORDS) as (keyof DiagramWords)[]) if (words?.[k]) out[k] = words[k] as string
    return out
  }, [words])
  if (!d) return null
  const drawing = <Drawing d={d} picked={picked} onPick={onPick} w={w} />
  if (bare) return drawing
  const legend = legendOf(d)
  return (
    <Root>
      <FlowCanvas zoomKey={zoomKey}>{drawing}</FlowCanvas>
      <Legend data-diagram-legend>
        {legend.states.map((s) => <span key={s}><Mark $color={STATE_COLOR[s]} />{w[s]}</span>)}
        {legend.undeclared && <span><Dashed />{w.undeclared}</span>}
        {legend.evidences.map((e, i) => <span key={e}><Stroke $color={linkColor(i)} />{e}</span>)}
        {d.note && <span className="note">{d.note}</span>}
      </Legend>
    </Root>
  )
}
