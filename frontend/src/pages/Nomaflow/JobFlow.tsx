// A job's steps drawn as a flow, read left to right: the start, a card per step (its kind, its name, what it
// does), the end. A click on a card opens that step below; a "+" on a link inserts a step at that place.
// The same picture as a service's flow in the Studio (pages/Settings/ActionFlow) — a job is a plain sequence.
import { useEffect, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Flag, Play, Plus } from 'lucide-react'
import { colors, fontSize, fonts, radius, shadow } from '../../theme'
import { FlowCanvas, FlowRows } from '../Settings/flowKit'

type Tone = 'blue' | 'green' | 'orange' | 'purple' | 'yellow'
export interface FlowStep { name: string; kind: string; tone: string; summary: string; off: boolean; continues: boolean }

const Line = styled.div`display: flex; align-items: center; width: max-content;`
const End = styled.button<{ $tone: Tone; $on?: boolean }>`
  flex: 0 0 auto; display: flex; cursor: pointer; align-items: center; gap: 8px; padding: 8px 13px; border-radius: 999px; font-family: ${fonts.sans};
  font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary};
  background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => (p.$on ? colors[p.$tone].main : colors[p.$tone].border)};
`
const Card = styled.button<{ $tone: Tone; $on: boolean; $off: boolean }>`
  flex: 0 0 auto; width: 190px; text-align: left; cursor: pointer; padding: 9px 11px; display: flex; flex-direction: column; gap: 3px;
  font-family: ${fonts.sans}; background: var(--bg-modal); border-radius: ${radius.lg}; opacity: ${(p) => (p.$off ? 0.45 : 1)};
  border: 1px solid ${(p) => (p.$on ? colors.blue.main : colors.border)}; box-shadow: ${(p) => (p.$on ? shadow.focus : 'none')};
  transition: border-color 0.15s, transform 0.15s;
  &:hover { border-color: ${(p) => (p.$on ? colors.blue.main : colors[p.$tone].border)}; transform: translateY(-1px); }
  & .kind { font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: ${(p) => colors[p.$tone].main}; }
  & .name { font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .what { font-size: ${fontSize.micro}; font-family: ${fonts.mono}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-height: 1.2em; }
`
const LinkWrap = styled.div`position: relative; flex: 0 0 auto; width: 40px; height: 26px; display: flex; align-items: center;`
const Wire = styled.div`
  width: 100%; height: 2px; background: ${colors.border}; position: relative;
  &::after { content: ''; position: absolute; right: 0; top: -3px; border-left: 6px solid ${colors.border}; border-top: 4px solid transparent; border-bottom: 4px solid transparent; }
`
const AddBtn = styled.button`
  position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%); width: 20px; height: 20px; padding: 0; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; border-radius: 999px;
  background: var(--bg-modal); color: ${colors.text.muted}; border: 1px solid ${colors.border};
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.main}; background: ${colors.blue.bg}; }
`
const Menu = styled.div`
  position: absolute; left: 50%; top: 26px; transform: translateX(-50%); z-index: 5; min-width: 150px; padding: 6px;
  background: var(--bg-modal); border: 1px solid ${colors.border}; border-radius: ${radius.md}; box-shadow: ${shadow.modal};
  display: flex; flex-direction: column; gap: 2px;
  & button { padding: 5px 8px; background: transparent; border: none; cursor: pointer; text-align: left; border-radius: ${radius.sm};
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono}; }
  & button:hover { background: var(--hover-subtle); }
`
const toneOf = (tone: string): Tone => (['blue', 'green', 'orange', 'purple', 'yellow'].includes(tone) ? (tone as Tone) : 'blue')

export default function JobFlow({ steps, kinds, selected, onSelect, onStart, onInsert }: {
  /** The start is picked: the job itself (its schedule, its parameters…) is what is edited. */
  onStart: () => void
  steps: FlowStep[]
  /** The kinds of step a "+" offers. */
  kinds: string[]
  selected: number | null
  onSelect: (index: number) => void
  onInsert: (index: number, kind: string) => void
}) {
  const { t } = useTranslation()
  const [adding, setAdding] = useState<number | null>(null)
  useEffect(() => {
    if (adding == null) return
    const close = () => setAdding(null)
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [adding])
  const link = (index: number): ReactNode => (
    <LinkWrap key={`l${index}`}>
      <Wire />
      <AddBtn type="button" title={t('settings.studio.addStep')} aria-label={t('settings.studio.addStep')}
        onMouseDown={(e) => e.stopPropagation()} onClick={() => setAdding(adding === index ? null : index)}><Plus size={12} /></AddBtn>
      {adding === index && (
        <Menu onMouseDown={(e) => e.stopPropagation()}>
          {kinds.map((kind) => <button type="button" key={kind} onClick={() => { setAdding(null); onInsert(index, kind) }}>{kind}</button>)}
        </Menu>
      )}
    </LinkWrap>
  )
  return (
    <FlowCanvas zoomKey="job">
      <FlowRows>
        <End type="button" $tone="blue" $on={selected == null} onClick={onStart}><Play size={15} /> {t('settings.studio.start')}</End>
        {steps.map((s, i) => (
          <Line key={i}>
            {link(i)}
            <Card type="button" $tone={toneOf(s.tone)} $on={selected === i} $off={s.off} onClick={() => onSelect(i)}>
              <span className="kind">{s.kind}{s.off ? ` · ${t('nomaflow.steps.turnedOffTag')}` : ''}</span>
              <span className="name">{s.name}</span>
              <span className="what">{s.summary}</span>
            </Card>
          </Line>
        ))}
        <Line>{link(steps.length)}<End as="div" $tone="green" style={{ cursor: 'default' }}><Flag size={15} /> {t('nomaflow.flow.end')}</End></Line>
      </FlowRows>
    </FlowCanvas>
  )
}
