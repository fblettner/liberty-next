// A thing drawn as its stages: cards in a row joined by arrows, each saying what the stage holds (a main line,
// a second one — in orange when it tells something is missing); a click opens the stage. The same picture for
// a screen (ScreenStages), a pool (PoolsBuilder) and whatever is edited stage by stage.
import { Fragment, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { colors, fontSize, fonts, radius } from '../../theme'

export type StageTone = 'blue' | 'green' | 'purple' | 'orange'
export interface StageView { id: string; tone: StageTone; icon: ReactNode; title: string; what: string; more?: string; warn?: boolean }

const Strip = styled.div`display: flex; align-items: stretch; overflow-x: auto; padding-bottom: 2px; margin-bottom: 12px; flex-shrink: 0;`
const Wire = styled.div`
  flex: 0 0 22px; align-self: center; height: 2px; background: ${colors.border}; position: relative;
  &::after { content: ''; position: absolute; right: 0; top: -3px; border-left: 6px solid ${colors.border}; border-top: 4px solid transparent; border-bottom: 4px solid transparent; }
`
const Card = styled.button<{ $tone: StageTone; $on: boolean }>`
  flex: 1 1 0; min-width: 170px; text-align: left; cursor: pointer; padding: 9px 11px; display: flex; flex-direction: column; gap: 3px;
  font-family: ${fonts.sans}; background: ${(p) => (p.$on ? colors[p.$tone].bg : 'var(--bg-modal)')}; border-radius: ${radius.lg};
  border: 1px solid ${(p) => (p.$on ? colors[p.$tone].main : colors.border)}; transition: border-color 0.15s;
  &:hover { border-color: ${(p) => colors[p.$tone].border}; }
  & .kind { display: flex; align-items: center; gap: 6px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: ${(p) => colors[p.$tone].main}; }
  & .chip { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: ${radius.md}; background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border}; }
  & .what { font-size: ${fontSize.sm}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .more { font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-height: 1.3em; }
  & .warn { color: ${colors.orange.main}; }
`

export default function StageStrip({ stages, current, onPick }: { stages: StageView[]; current: string; onPick: (id: string) => void }) {
  return (
    <Strip>
      {stages.map((s, i) => (
        <Fragment key={s.id}>
          {i > 0 && <Wire />}
          <Card type="button" data-stage={s.id} $tone={s.tone} $on={current === s.id} onClick={() => onPick(s.id)}>
            <span className="kind"><span className="chip">{s.icon}</span>{s.title}</span>
            <span className="what">{s.what}</span>
            <span className={`more${s.warn ? ' warn' : ''}`}>{s.more ?? ''}</span>
          </Card>
        </Fragment>
      ))}
    </Strip>
  )
}
