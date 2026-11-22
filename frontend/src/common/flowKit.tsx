// What the flow views share (a service's steps, a job's steps): the dotted canvas with its zoom, and the row
// that wraps — a long sequence continues on the next line instead of running off to the right.
import { useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Minus, Plus } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../theme'

const Frame = styled.div`
  position: relative; flex: 1 1 auto; min-height: 0; display: flex; border: 1px solid ${colors.border}; border-radius: ${radius.lg}; overflow: hidden;
  background-image: radial-gradient(${colors.border} 1px, transparent 1px); background-size: 18px 18px;
`
const Scroll = styled.div`flex: 1 1 auto; min-width: 0; overflow: auto; padding: 28px 24px 24px;`
const Bar = styled.div`
  position: absolute; top: 8px; right: 8px; z-index: 4; display: inline-flex; align-items: center; border-radius: ${radius.md}; overflow: hidden;
  background: var(--bg-modal); border: 1px solid ${colors.border};
  & button { display: inline-flex; align-items: center; justify-content: center; min-width: 28px; height: 26px; padding: 0 6px; border: none; background: transparent;
    cursor: pointer; color: ${colors.text.secondary}; font-size: ${fontSize.micro}; font-family: ${fonts.mono}; }
  & button:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
  & button:disabled { opacity: 0.4; cursor: default; }
`
/** A sequence that wraps: each item is a link and its step, the rows spaced so the links stay readable. */
export const FlowRows = styled.div`display: flex; flex-wrap: wrap; align-items: center; row-gap: 26px;`

const LEVELS = [0.5, 0.6, 0.75, 0.9, 1, 1.15, 1.3]

function stored(key: string): number {
  try { const v = Number(localStorage.getItem(`liberty.flow.zoom.${key}`)); return LEVELS.includes(v) ? v : 1 } catch { return 1 }
}

/** The canvas of a flow, with its zoom (kept per kind of flow on this device). */
export function FlowCanvas({ zoomKey, children }: { zoomKey: string; children: ReactNode }) {
  const { t } = useTranslation()
  const [zoom, setZoomState] = useState(() => stored(zoomKey))
  const setZoom = (v: number) => { setZoomState(v); try { localStorage.setItem(`liberty.flow.zoom.${zoomKey}`, String(v)) } catch { /* private window */ } }
  const at = LEVELS.indexOf(zoom)
  return (
    <Frame>
      <Bar>
        <button type="button" disabled={at <= 0} title={t('settings.studio.zoomOut')} aria-label={t('settings.studio.zoomOut')} onClick={() => setZoom(LEVELS[at - 1])}><Minus size={13} /></button>
        <button type="button" title={t('settings.studio.zoomReset')} onClick={() => setZoom(1)}>{Math.round(zoom * 100)}%</button>
        <button type="button" disabled={at >= LEVELS.length - 1} title={t('settings.studio.zoomIn')} aria-label={t('settings.studio.zoomIn')} onClick={() => setZoom(LEVELS[at + 1])}><Plus size={13} /></button>
      </Bar>
      <Scroll><div style={{ zoom }}>{children}</div></Scroll>
    </Frame>
  )
}
