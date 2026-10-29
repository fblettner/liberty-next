// The API connector editor's styled atoms, shared by its tabs (ApiConnectorEditor,
// ApiEnvironmentsTab).
import styled from '@emotion/styled'
import { colors, fontSize, fonts, radius } from '../../theme'

export const SectionHead = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.micro}; letter-spacing: 0.5px;
  text-transform: uppercase; padding: 14px 0 8px; border-bottom: 1px solid ${colors.border};
  margin-bottom: 12px;
`
export const Hint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro}; padding: 4px 2px;`
// One field row: label on the left (fixed width), control on the right.
export const FieldRow = styled.div`
  display: grid; grid-template-columns: 160px 1fr; align-items: center; gap: 12px;
  padding: 6px 0;
  & > label { color: ${colors.text.primary}; font-size: ${fontSize.sm}; }
`
// A collapsible card (an endpoint, an environment): the header toggles the body.
export const EndpointCard = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  margin-bottom: 10px;
`
export const EndpointHead = styled.button<{ $open?: boolean }>`
  display: flex; align-items: center; gap: 12px; width: 100%; padding: 10px 14px;
  text-align: left; cursor: pointer; background: transparent; border: none;
  color: ${colors.text.primary}; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  border-bottom: 1px solid ${({ $open }) => ($open ? colors.border : 'transparent')};
  & .method {
    display: inline-block; padding: 2px 8px; border-radius: ${radius.sm};
    background: ${colors.blue.bg}; color: ${colors.blue.main};
    font-size: ${fontSize.micro}; font-weight: 600; min-width: 44px; text-align: center;
  }
  & .name { font-family: ${fonts.mono}; }
  & .grow { flex: 1; }
`
export const EndpointBody = styled.div`padding: 14px;`
