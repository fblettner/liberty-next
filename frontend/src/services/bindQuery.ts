// The query string of a read that follows a KEY from one screen to another: a nested tab reading
// its parent's children, the row-click form reading the clicked record. No React here.
//
// Such a bind is a key relationship, so it must match EXACTLY: each value goes with a
// ``<name>_op=equals`` companion. Without it a text column that is a filter of the target screen
// is matched with the server's default operator, "contains" — ``DEMO`` then also returns
// ``DEMO2``, and a form that takes the first row opens (and saves) the wrong record.

/** The screen whose settings the read must use: several screens may share one read query, and
 *  without the pin the server applies the first one it finds. */
export interface ScreenPin {
  app: string
  screen: string
}

/** ``name=value&name_op=equals`` for each bind, then the screen pin. Starts with ``?``; empty
 *  when there is nothing to send. */
export function bindsToQuery(bound: Record<string, string>, pin?: ScreenPin | null): string {
  const parts = Object.entries(bound).flatMap(([k, v]) => [
    `${encodeURIComponent(k)}=${encodeURIComponent(v)}`,
    `${encodeURIComponent(`${k}_op`)}=equals`,
  ])
  if (pin && pin.app && pin.screen) {
    parts.push(`_screen=${encodeURIComponent(pin.screen)}`, `_app=${encodeURIComponent(pin.app)}`)
  }
  return parts.length ? `?${parts.join('&')}` : ''
}
