// The frame the configuration builders share (Liberty Next → Build / Administer): the page column, its
// toolbar row, the "nothing here yet" line. Each builder used to declare its own identical copy.
import styled from '@emotion/styled'
import { colors, fontSize } from '../theme'

/** The builder's page: a column filling the available height, its list / editor scrolling inside. */
export const BuilderShell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0; height: 100%;`

/** The row above the builder's content: search, pickers, Add / Save / Reload buttons. */
export const BuilderToolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`

/** The centred "no item yet" / "pick one" line of a builder's panel. */
export const BuilderEmpty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`
