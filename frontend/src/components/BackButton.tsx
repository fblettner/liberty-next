// Drill-back button — rendered above the content area when the operator got here via a
// row_click_route (the source page passes ``location.state = { from, fromTabId }`` when
// navigating). One click navigates back to the source AND closes the drill tab that the
// destination URL auto-opened — together that's the "same tab" feel the operator wants
// without re-architecting URL ↔ tab coupling (the URL drives rendering, the tab strip
// follows the URL; an open-then-close pair simulates an in-place drill).
//
// Renders nothing when there's no drill source (e.g. operator typed the URL directly or
// opened the tab from the menu), so the button is invisible whenever it would be useless.
import { useCallback } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ArrowLeft } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'
import { useTabs } from '../tabs/TabsContext'

const Bar = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 6px 16px 0; flex-shrink: 0;
`
const BackBtn = styled.button`
  display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 10px 0 6px;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: transparent; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  color: ${colors.text.secondary}; cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`

export default function BackButton() {
  const { t } = useTranslation()
  const location = useLocation()
  const navigate = useNavigate()
  const { close, activeId } = useTabs()
  // The source page sets these on navigate(); when absent, this is a direct hit (typed URL,
  // bookmark, menu click) and there's nothing to go "back" to from a UX standpoint.
  const state = location.state as { from?: string; fromTabId?: string } | null
  const from = state?.from
  const onClick = useCallback(() => {
    if (!from) return
    // Order matters — close the DRILL tab (the one currently active, opened auto-magically
    // by the destination URL's TabRoute) BEFORE navigating, otherwise activeId trails the
    // URL by one render and the tab strip flickers.
    if (activeId) close(activeId)
    navigate(from, { replace: true })
  }, [from, activeId, close, navigate])
  if (!from) return null
  return (
    <Bar>
      <BackBtn type="button" onClick={onClick} title={t('common.back', 'Back')}>
        <ArrowLeft size={13} />
        {t('common.back', 'Back')}
      </BackBtn>
    </Bar>
  )
}
