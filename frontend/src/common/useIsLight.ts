import { useEffect, useState } from 'react'

/** True when the light theme is active (the `.theme-light` class on <html>).
 *  Re-renders the caller when the Layout's toggle flips it — used e.g. to pick
 *  Monaco's editor theme. */
export function useIsLight(): boolean {
  const [light, setLight] = useState(() => document.documentElement.classList.contains('theme-light'))
  useEffect(() => {
    const el = document.documentElement
    const obs = new MutationObserver(() => setLight(el.classList.contains('theme-light')))
    obs.observe(el, { attributes: true, attributeFilter: ['class'] })
    return () => obs.disconnect()
  }, [])
  return light
}
