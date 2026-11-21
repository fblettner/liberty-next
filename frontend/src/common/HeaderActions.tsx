// A page's own buttons (Save, Discard, its "unsaved" word) placed in the page header, beside what the header
// already holds (Reload config) — not in a row of their own inside the page. The host of the page offers the
// place (`HeaderSlotProvider`, given the header's element); a page shown without such a header (embedded in a
// window, in the Studio) keeps the same buttons in a row at its top.
import { createContext, useContext, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'

const HeaderSlotContext = createContext<HTMLElement | null>(null)
export const HeaderSlotProvider = HeaderSlotContext.Provider

/** The header's place for a page's buttons: sits before the header's own. */
export const HeaderSlot = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; min-width: 0;`
const Inline = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; flex-shrink: 0;`

export function HeaderActions({ children }: { children: ReactNode }) {
  const slot = useContext(HeaderSlotContext)
  return slot ? createPortal(children, slot) : <Inline>{children}</Inline>
}
