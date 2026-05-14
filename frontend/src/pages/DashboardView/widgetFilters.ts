// Resolve dashboard-level filter picks into per-widget URL query parameters. A filter declares
// a `dictionary_key` (the v1 `dd`); each widget's column hints carry `dd` on the wire — we look
// up the widget's `(connector, query)` in the workspace's cached connector metadata, find which
// of its columns matches the filter's `dd`, and bind that column name as `?<col>=<value>` plus
// the `<col>_op=equals` companion the migrated SQL wrappers expect.
//
// Widgets whose query has no matching column simply skip the filter (no bind emitted) — same
// behaviour as v1's framework, no SQL surprises.
import type { ConnectorMeta } from '../../types/connectors'
import type { DashboardFilterWire } from '../../types/dashboards'

/** Translate the current filter selections into URL query params for one widget's `(connector,
 *  query)` fetch. Returns an empty object when no filter applies (the widget fetches unfiltered).
 */
export function buildWidgetFilterParams(
  connector: string,
  query: string,
  filters: DashboardFilterWire[],
  filterValues: Record<string, string>,
  connectors: ConnectorMeta[] | null,
): Record<string, string> {
  if (!filters.length || !connectors) return {}
  const conn = connectors.find((c) => c.name === connector)
  if (!conn || conn.type !== 'sql') return {}
  const meta = conn.queries.find((q) => q.name === query)
  if (!meta) return {}
  const out: Record<string, string> = {}
  for (const f of filters) {
    const value = filterValues[f.id]
    if (!value) continue                                          // "All" / unset
    const col = meta.columns.find((c) => c.dd === f.dictionary_key)
    if (!col) continue                                            // widget has no matching column → ignore
    out[col.name] = value
    out[`${col.name}_op`] = 'equals'                              // server-side wrapper expects an op
  }
  return out
}
