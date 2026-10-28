// The views of a page as a row of cards — an icon, the name, one line on what the view is for —
// the picked one bordered and tinted in its colour (Monitoring, Package, Access). ``compact``
// keeps the row short (one line, the description cut) where the view below needs the height.
// (See nomaubl's common/ViewCards.tsx.)
import { type ReactNode } from 'react'
import styled from '@emotion/styled'
import { colors, fontSize, fonts, radius } from '../theme'

export interface ViewCard<T extends string> {
  id: T
  label: string
  desc: string
  icon: ReactNode
  /** a theme colour (``colors.blue.main``…); blue when unset */
  color?: string
}

const Row = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; flex-shrink: 0;`
const Card = styled.button<{ $on: boolean; $c: string; $compact: boolean }>`
  display: flex; gap: ${({ $compact }) => ($compact ? 10 : 12)}px; align-items: ${({ $compact }) => ($compact ? 'center' : 'flex-start')};
  padding: ${({ $compact }) => ($compact ? '9px 12px' : '14px 16px')}; text-align: left; cursor: pointer; min-width: 0;
  border-radius: ${radius.lg}; font-family: ${fonts.sans};
  border: 1px solid ${({ $on, $c }) => ($on ? `color-mix(in srgb, ${$c} 60%, transparent)` : colors.border)};
  background: ${({ $on, $c }) => ($on ? `color-mix(in srgb, ${$c} 8%, ${colors.bg.card})` : colors.bg.card)};
  transition: background 0.12s, border-color 0.12s;
  &:hover { border-color: ${({ $on, $c }) => ($on ? `color-mix(in srgb, ${$c} 60%, transparent)` : colors.text.muted)}; }
`
const Icon = styled.span<{ $c: string; $compact: boolean }>`
  width: ${({ $compact }) => ($compact ? 30 : 34)}px; height: ${({ $compact }) => ($compact ? 30 : 34)}px; border-radius: ${radius.md}; flex: none;
  display: inline-flex; align-items: center; justify-content: center;
  background: color-mix(in srgb, ${({ $c }) => $c} 16%, transparent); color: ${({ $c }) => $c};
`
const Name = styled.div`font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary};`
const Desc = styled.div<{ $compact: boolean }>`
  font-size: ${({ $compact }) => ($compact ? fontSize.micro : fontSize.sm)}; color: ${colors.text.muted}; margin-top: 2px; line-height: 1.4;
  ${({ $compact }) => ($compact ? 'white-space: nowrap; overflow: hidden; text-overflow: ellipsis;' : '')}
`

export function ViewCards<T extends string>({ items, value, onChange, compact = false }: {
  items: ViewCard<T>[]
  value: T
  onChange: (id: T) => void
  compact?: boolean
}) {
  return (
    <Row role="tablist">
      {items.map((v) => {
        const c = v.color ?? colors.blue.main
        return (
          <Card key={v.id} type="button" role="tab" aria-selected={value === v.id} $on={value === v.id} $c={c} $compact={compact}
            onClick={() => onChange(v.id)} title={compact ? v.desc : undefined}>
            <Icon $c={c} $compact={compact}>{v.icon}</Icon>
            <div style={{ minWidth: 0 }}><Name>{v.label}</Name><Desc $compact={compact}>{v.desc}</Desc></div>
          </Card>
        )
      })}
    </Row>
  )
}
