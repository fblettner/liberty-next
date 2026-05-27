// Resolve dashboard-level filter picks into per-widget URL query parameters. A filter declares
// a `dictionary_key` (the v1 `dd`); each *screen* carries a server-built `dd_map` (one entry per
// ColumnHint with a `dd`). For the widget's `(connector, query)` we look up the matching screen
// and ask `dd_map[filter.dictionary_key]` for the column name — then bind it as `?<col>=<value>`
// plus the `<col>_op=equals` companion the migrated SQL wrappers expect.
//
// Widgets whose query has no matching screen — or whose screen has no column with that `dd` —
// simply skip the filter (no bind emitted) — same behaviour as v1's framework, no SQL surprises.
//
// Pre-Phase-3 this code walked `meta.columns` on the connector describe(); columns moved to the
// Screen layer in Phase 3, which left filtering as a no-op until this rewire landed.
import type { DashboardFilterWire } from '../../types/dashboards'
import type { ScreenListItem } from '../../types/screens'

/** Translate the current filter selections into URL query params for one widget's `(connector,
 *  query)` fetch. Returns an empty object when no filter applies (the widget fetches unfiltered).
 *
 *  `findScreen` is `WorkspaceContext.findScreen` — the existing connector+read_query → screen
 *  lookup. Passed in (rather than imported) to keep this module free of React imports so it can
 *  stay a pure helper unit-testable in isolation.
 */
export function buildWidgetFilterParams(
  connector: string,
  query: string,
  filters: DashboardFilterWire[],
  filterValues: Record<string, string>,
  findScreen: (connector: string, readQuery: string) => ScreenListItem | null,
): Record<string, string> {
  if (!filters.length) return {}
  const screen = findScreen(connector, query)
  const ddMap = screen?.dd_map
  if (!ddMap) return {}                                          // no screen / no dd hints → nothing to bind
  const out: Record<string, string> = {}
  for (const f of filters) {
    const value = filterValues[f.id]
    if (!value) continue                                          // "All" / unset
    const col = ddMap[f.dictionary_key]
    if (!col) continue                                            // screen has no column carrying this dd → ignore
    out[col] = value
    out[`${col}_op`] = 'equals'                                   // server-side wrapper expects an op
  }
  return out
}
