// Resolve dashboard-level filter picks into per-widget URL query parameters. A filter declares
// a `dictionary_key` (the v1 `dd`); each *screen* carries a server-built `dd_map` (one entry per
// ColumnHint with a `dd`). For the widget's `(connector, query)` we look up the matching screen
// and ask `dd_map[filter.dictionary_key]` for the column name — then bind it as `?<col>=<value>`
// plus the `<col>_op=equals` companion the migrated SQL wrappers expect.
//
// Widgets whose query has no matching screen — or whose screen has no column with that `dd` —
// send the pick as a DICTIONARY BIND instead (`_dd_<ITEM>=<value>`, `_dd_<ITEM>_from` / `_to`
// for the period): the server binds it on the result column carrying the item (its name, the
// name minus the connector's dd_prefix, or a hint's dd — the way the data scope finds its
// columns) and ignores it when no column does. So a plain `SELECT … FROM sales` is filtered
// without a screen, like the scope filters it.
//
// Pre-Phase-3 this code walked `meta.columns` on the connector describe(); columns moved to the
// Screen layer in Phase 3, which left filtering as a no-op until this rewire landed.
import type { DashboardFilterWire, DashboardPeriodWire } from '../../types/dashboards'
import type { ScreenListItem } from '../../types/screens'
import { previousPeriod, type DateRange } from '../../services/period'

export interface PeriodSelection {
  range: DateRange
  /** The chosen basis value (one of `period.basis[].value`), or '' when the period has no basis list. */
  basis: string
}

/** The reserved prefix of a dictionary bind (`liberty/web/connectors.py` `split_dd_binds`). */
export const DD_PARAM_PREFIX = '_dd_'

/** The widest range: what "All dates" binds, so a query written with BETWEEN :PERIOD_FROM AND
 *  :PERIOD_TO keeps every row instead of comparing with NULL. */
export const ALL_DATES = { dateFrom: '0001-01-01', dateTo: '9999-12-31' }

/** The period's URL params for one widget: the four range binds every query may use, the basis,
 *  and — when the dashboard names the date column (directly or by dd, resolved through the
 *  widget's screen) — the `<COL>_from` / `<COL>_to` pair the server range-wraps on. "All dates"
 *  (an empty range) binds the widest range and no column bounds. */
export function buildWidgetPeriodParams(
  connector: string,
  query: string,
  period: DashboardPeriodWire | undefined,
  selection: PeriodSelection | undefined,
  findScreen: (connector: string, readQuery: string) => ScreenListItem | null,
): Record<string, string> {
  if (!period || !selection) return {}
  const all = !selection.range.dateFrom || !selection.range.dateTo
  const { dateFrom, dateTo } = all ? ALL_DATES : selection.range
  const prev = all ? ALL_DATES : previousPeriod(selection.range)
  const out: Record<string, string> = { PERIOD_FROM: dateFrom, PERIOD_TO: dateTo, PREV_FROM: prev.dateFrom, PREV_TO: prev.dateTo }
  const basis = (period.basis ?? []).find((b) => b.value === selection.basis) ?? (period.basis ?? [])[0]
  if (basis) out.PERIOD_BASIS = basis.value
  if (all) return out
  // The date column: the chosen basis' own column / dd when it names one, else the period's.
  const column = basis && (basis.column || basis.dd) ? basis.column : period.column
  const dd = basis && (basis.column || basis.dd) ? basis.dd : period.dd
  const col = column ?? (dd ? findScreen(connector, query)?.dd_map?.[dd] ?? null : null)
  if (col) {
    out[`${col}_from`] = dateFrom
    out[`${col}_to`] = dateTo
  } else if (dd) {
    out[`${DD_PARAM_PREFIX}${dd}_from`] = dateFrom
    out[`${DD_PARAM_PREFIX}${dd}_to`] = dateTo
  }
  return out
}

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
  const ddMap = findScreen(connector, query)?.dd_map
  const out: Record<string, string> = {}
  for (const f of filters) {
    const value = filterValues[f.id]
    if (!value) continue                                          // "All" / unset
    const col = ddMap?.[f.dictionary_key]
    if (col) {
      out[col] = value
      out[`${col}_op`] = 'equals'                                 // server-side wrapper expects an op
    } else {
      out[`${DD_PARAM_PREFIX}${f.dictionary_key}`] = value        // no screen column: the server resolves the item
    }
  }
  return out
}
