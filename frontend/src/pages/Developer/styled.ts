// Shared layout of the Developer reference views (C12): the sections / groups on the left, the
// head card and the grid on the right, the detail popup's facts and code references.
import styled from '@emotion/styled'
import { Card } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

export const Body = styled.div`display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 14px; flex: 1; min-height: 0;`
export const Side = styled(Card)`padding: 0; display: flex; flex-direction: column; min-height: 0; overflow: hidden;`
export const SideSearch = styled.div`
  position: relative; padding: 10px; border-bottom: 1px solid ${colors.border};
  & svg { position: absolute; left: 20px; top: 20px; color: ${colors.text.muted}; }
  & input { width: 100%; box-sizing: border-box; padding: 7px 10px 7px 30px; border-radius: ${radius.md}; border: 1px solid ${colors.border};
    background: ${colors.bg.input}; color: ${colors.text.primary}; font-family: ${fonts.sans}; font-size: ${fontSize.sm}; outline: none; }
  & input:focus { border-color: ${colors.blue.border}; }
`
export const SideList = styled.div`overflow-y: auto; flex: 1; min-height: 0;`
export const SideItem = styled.button<{ $on: boolean }>`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 9px 14px; border: none; border-bottom: 1px solid ${colors.border};
  background: ${({ $on }) => ($on ? colors.blue.bg : 'transparent')}; cursor: pointer; text-align: left;
  border-left: 3px solid ${({ $on }) => ($on ? colors.blue.main : 'transparent')};
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; flex: 1; }
  & .n { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .gaps { font-size: ${fontSize.micro}; color: ${colors.red.main}; }
  &:hover { background: var(--hover-subtle); }
`
export const Main = styled.div`display: flex; flex-direction: column; gap: 12px; min-height: 0;`
export const Head = styled(Card)`display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  & .title { font-family: ${fonts.mono}; font-size: ${fontSize.lg}; font-weight: 600; color: ${colors.text.primary}; }
  & .sub { color: ${colors.text.muted}; font-size: ${fontSize.sm}; }`
export const Mono = styled.span`font-family: ${fonts.mono};`
export const Tags = styled.span`display: inline-flex; gap: 4px; flex-wrap: nowrap; overflow: hidden; max-width: 100%;`
export const TAGS_SHOWN = 3
export const Facts = styled.div`
  display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 6px 14px; align-items: baseline; font-size: ${fontSize.sm};
  & .k { color: ${colors.text.muted}; }
  & .v { color: ${colors.text.primary}; overflow-wrap: anywhere; }
  & .mono { font-family: ${fonts.mono}; }
`
export const RefRow = styled.div`
  display: grid; grid-template-columns: 110px minmax(0, 1fr); gap: 14px; padding: 6px 0; border-bottom: 1px solid ${colors.border};
  &:last-child { border-bottom: none; }
  & .file { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.blue.main}; }
  & .code { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.secondary}; white-space: pre-wrap; overflow-wrap: anywhere; margin-top: 2px; }
`
export const Label = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-weight: 600; margin: 14px 0 4px; padding-bottom: 4px; border-bottom: 1px solid ${colors.border};`
export const SideCat = styled.div`
  padding: 12px 14px 4px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: ${colors.text.muted}; border-bottom: 1px solid ${colors.border};
`
