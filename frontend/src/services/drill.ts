// The in-place drill (a `navigate` action with `inline = true`): the target a grid opens as a
// panel inside its own tab — the destination query or screen, the binds resolved from the clicked
// row — and the breadcrumb that leads back. No React here; the TableView owns the stack by
// nesting itself (each level keeps its own child), so the crumbs accumulate level by level.
export interface DrillTarget {
  connector: string
  query: string
  /** A pinned screen id (its columns / filters / dialog apply); null = the query, screen resolved by read query. */
  screen: string | null
  /** The binds the target seeds its params and filters from (`?USR_ID=42` in the tab mode). */
  params: Record<string, string>
}

export interface Crumb { label: string; onClick: () => void }

/** What a nested TableView receives: its binds, the crumbs above it, how to close it. */
export interface EmbeddedDrill { binds: Record<string, string>; crumbs: Crumb[]; onBack: () => void
  /** Whether the tab the panel lives in is the active route (the parent's own flag). */
  active: boolean }

/** The binds as the URL would carry them (empty values dropped, order stable) — the key the
 *  TableView's drill seeding reacts to, identical in both modes. */
export function bindsToSearchParams(binds: Record<string, string>): URLSearchParams {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(binds)) if (v != null && v !== '') qs.set(k, String(v))
  return qs
}
