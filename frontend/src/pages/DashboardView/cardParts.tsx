// The synthesis widgets' card: the glass panel with its title and subtitle inside (nomaubl's
// cards), the list line, the figure column, the tone dot, the tag pill, the bar track.
import styled from '@emotion/styled'
import type { Tone } from '../../services/widgets'
import { colors, fontSize, fonts, glass, radius, shadow } from '../../theme'

export const Card = styled.div`
  flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; padding: 16px 18px;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: ${colors.bg.card};
  ${glass.surface}
  box-shadow: ${shadow.sm}, inset 0 1px 0 rgba(255, 255, 255, 0.08);
  overflow: hidden;
`
export const Title = styled.div`font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; font-family: ${fonts.sans};`
export const Sub = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans}; margin-top: 2px;`
export const Scroll = styled.div`flex: 1; min-height: 0; overflow-y: auto;`
export const Line = styled.div`
  display: flex; gap: 12px; align-items: center; padding: 9px 0; border-top: 1px solid ${colors.border};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; color: ${colors.text.primary};
  &:first-of-type { border-top: none; }
`
export const Main = styled.div`flex: 1; min-width: 0; & > .t { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }`
export const Num = styled.div`text-align: right; font-variant-numeric: tabular-nums; flex: none; & > .v { font-weight: 600; }`
export const Muted = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; margin-top: 2px;`
export const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 8px 0;`

export function toneColor(tone: Tone): string {
  return tone === 'ok' ? colors.green.main : tone === 'warn' ? colors.orange.main : tone === 'error' ? colors.red.main : tone === 'info' ? colors.blue.main : colors.text.muted
}
export function toneBg(tone: Tone): string {
  return tone === 'ok' ? colors.green.bg : tone === 'warn' ? colors.orange.bg : tone === 'error' ? colors.red.bg : tone === 'info' ? colors.blue.bg : colors.bg.input
}
export const Dot = styled.span<{ $tone: Tone }>`width: 8px; height: 8px; border-radius: 50%; flex: none; background: ${({ $tone }) => toneColor($tone)};`
export const Pill = styled.span<{ $tone: Tone }>`
  display: inline-block; margin-top: 4px; padding: 2px 8px; border-radius: ${radius.sm}; font-size: ${fontSize.micro};
  background: ${({ $tone }) => toneBg($tone)}; color: ${({ $tone }) => toneColor($tone)};
`
export const Track = styled.div`height: 8px; border-radius: 4px; background: ${colors.bg.input}; overflow: hidden;`
export const Fill = styled.div<{ $w: number }>`height: 8px; border-radius: 4px; width: ${({ $w }) => Math.max(0, Math.min(100, $w))}%; background: ${colors.blue.main};`
