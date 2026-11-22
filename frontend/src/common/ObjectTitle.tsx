// How an editor names what it edits: its label, then — smaller — the application it belongs to
// and its technical id. In place of the file's own notation (``[screens.app.id]``), which says
// where the object is stored, not what it is.
import styled from '@emotion/styled'
import { colors, fontSize, fonts } from '../theme'

const Wrap = styled.span`display: inline-flex; align-items: baseline; gap: 8px; min-width: 0;`
const Name = styled.span`font-weight: 600; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;`
const Where = styled.span`font-family: ${fonts.mono}; font-weight: 400; font-size: ${fontSize.sm}; color: ${colors.text.muted}; white-space: nowrap;`

export function ObjectTitle({ label, id, scope }: {
  /** The object's label; without one its id is the name. */
  label?: string | null
  id: string
  /** The application (or connector) it belongs to, when it has one. */
  scope?: string | null
}) {
  const name = (label ?? '').trim()
  const where = [scope, name && name !== id ? id : null].filter(Boolean).join(' · ')
  return (
    <Wrap data-object-title>
      <Name>{name || id}</Name>
      {where && <Where>{where}</Where>}
    </Wrap>
  )
}
