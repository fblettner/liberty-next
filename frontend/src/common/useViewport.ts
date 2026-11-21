import { useSyncExternalStore } from 'react'

/** Window widths the layout adapts at (px). Same values as `media` in the theme. */
export const BREAKPOINTS = { compact: 768, narrow: 1100 }

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query)
      m.addEventListener('change', cb)
      return () => m.removeEventListener('change', cb)
    },
    () => window.matchMedia(query).matches,
    () => false,
  )
}

/**
 * Width class of the window:
 *  - `narrow`  (≤ 1100 px, small laptop or tablet);
 *  - `compact` (≤ 768 px, phone or split window): the navigation becomes a drawer,
 *    windows take the whole screen and a grid reads as a list.
 */
export function useViewport(): { narrow: boolean; compact: boolean } {
  const narrow = useMedia(`(max-width: ${BREAKPOINTS.narrow}px)`)
  const compact = useMedia(`(max-width: ${BREAKPOINTS.compact}px)`)
  return { narrow, compact }
}
