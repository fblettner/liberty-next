// What a result cell of a dimension shows: its value through the column's enum / lookup rule, as the
// label, the code, or both — the choice made on the field's chip (`DimRow.show`). One reading for the
// table and the pivot, so both say the same thing.
import { useCallback, useMemo } from 'react'
import { ruleCell } from '../../services/cells'
import { shownText, type ShowModes } from '../../services/explorer'
import { lookupKey, useLookupBatch, type LookupSpec } from '../../services/lookups'
import type { Column } from '../../types/connectors'
import type { CodeShow, ResultColumn } from '../../types/explorer'

/** A column's display rule: a bucketed date (a month, a year) is shown as it comes. */
export const ruleOf = (c: ResultColumn) => (c.rule && !c.bucket ? c.rule : undefined)
const lookupSpec = (c: ResultColumn): LookupSpec | null => {
  const r = ruleOf(c)
  return r?.kind === 'lookup' ? { connector: r.connector, query: r.query, value: r.value, label: r.label, params: r.params, sources: r.sources } : null
}
/** A column whose values are codes with a label: an enum or a lookup. */
export const isCoded = (c: ResultColumn) => { const k = ruleOf(c)?.kind; return c.kind !== 'measure' && (k === 'enum' || k === 'lookup') }

export interface DimText { text: string; kind: ReturnType<typeof ruleCell>['kind'] }

/** The choice made for a column: by its key, else by its field (a detail column); the label when none. */
export const showOf = (c: ResultColumn, shows?: ShowModes): CodeShow => shows?.[c.key] ?? (c.field ? shows?.[c.field] : undefined) ?? 'label'

/** `(column, value) → what to show` for the non-null values of *columns*; the lookups are fetched once.
 *  *as* forces one reading (the table draws "both" as two columns: the code, then the label). */
export function useDimText(columns: ResultColumn[], shows?: ShowModes): (c: ResultColumn, v: unknown, as?: CodeShow) => DimText {
  const specs = useMemo<LookupSpec[]>(() => columns.flatMap((c) => lookupSpec(c) ?? []), [columns])
  const lookups = useLookupBatch(specs)
  return useCallback((c: ResultColumn, v: unknown, as?: CodeShow): DimText => {
    const rule = ruleOf(c)
    const col: Column = { name: c.key, type: null, rule }
    const spec = lookupSpec(c)
    const r = ruleCell(v, col, undefined, spec ? lookups.get(lookupKey(spec)) : undefined)
    if (!isCoded(c)) return { text: r.text, kind: r.kind }
    const mode = as ?? showOf(c, shows)
    const label = r.kind === 'enum' || r.kind === 'lookup' ? r.text : undefined
    return { text: shownText(String(v).trimEnd(), label, mode), kind: mode === 'code' ? 'plain' : r.kind }
  }, [lookups, shows])
}
