// Builds the column definitions for a SELECT result and hands them to the shared
// DataTable (sort / filter / search / group / hide-reorder / resize / export / paginate).
// Per-column display rules (BOOLEAN / ENUM / LOOKUP — the v2 form of v1's dd_rules)
// are applied in each cell via services/cells.ruleCell; a LOOKUP column is *split*
// into "<label> (ID)" (the raw code) + "<label>" (the resolved label) the way v1 did,
// and the lookup queries are fetched once via services/lookups.useLookupBatch. Each
// column also declares its filter kind (text/number/date/boolean/enum) so DataTable's
// per-column filter row shows the right control + operator.
import { useMemo, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import type { ColumnDef, VisibilityState } from '@tanstack/react-table'
import type { Column, QueryResult } from '../../types/connectors'
import { DataTable } from '../../common/DataTable'
import { genericFilterFn, type FilterKind, type FilterMeta } from '../../common/DataTableFilter'
import { enumMap, ruleCell } from '../../services/cells'
import { lookupKey, useLookupBatch, type LookupSpec } from '../../services/lookups'
import { CellSpan } from './styled'

type DataRow = Record<string, unknown>
type Align = CSSProperties['textAlign']

function colHeader(c: Column): string {
  return c.label ?? c.name
}
function cellAlign(c: Column): Align {
  return c.align === 'left' || c.align === 'right' || c.align === 'center' ? c.align : undefined
}
function filterKindOf(c: Column): FilterKind {
  if (c.rule?.kind === 'boolean') return 'boolean'
  if (c.rule?.kind === 'enum') return 'enum'
  const fmt = (c.format ?? '').toLowerCase()
  const typ = (c.type ?? '').toLowerCase()
  if (fmt === 'date' || /date|timestamp/.test(typ)) return 'date'
  if (fmt === 'number' || fmt === 'integer' || /int|numeric|decimal|float|double|real/.test(typ)) return 'number'
  return 'text'
}
// boolean/enum columns filter by exact match on the accessor value; the rest use the operator filter.
const filterPropsFor = (kind: FilterKind, options?: { value: string; label: string }[]) =>
  kind === 'boolean' || kind === 'enum'
    ? { filterFn: 'equals' as const, meta: { filter: { kind, options } } as FilterMeta }
    : { filterFn: genericFilterFn, meta: { filter: { kind } } as FilterMeta }

export function ResultTable({ result, connector, query }: { result: QueryResult; connector: string; query: string }) {
  const { t } = useTranslation()

  const enumMaps = useMemo(() => {
    const m = new Map<string, Map<string, string>>()
    for (const c of result.columns) if (c.rule?.kind === 'enum') m.set(c.name, enumMap(c.rule))
    return m
  }, [result.columns])

  const lookupSpecs = useMemo<LookupSpec[]>(
    () =>
      result.columns
        .filter((c) => c.rule?.kind === 'lookup')
        .map((c) => {
          const r = c.rule as Extract<NonNullable<Column['rule']>, { kind: 'lookup' }>
          return { connector: r.connector, query: r.query, value: r.value, label: r.label }
        }),
    [result.columns],
  )
  const lookupMaps = useLookupBatch(lookupSpecs)

  // One ColumnDef per result column — except LOOKUP columns, which become two (the raw
  // "(ID)" column then the resolved-label column). `accessorFn` returns the *displayed*
  // value (enum → its label, lookup → resolved) so sort / filter / search / group / export
  // work on it; the boolean column's accessor is "true"/"false" (its filter is a select).
  const columns = useMemo<ColumnDef<DataRow, unknown>[]>(() => {
    const idSuffix = ` ${t('table.idColumnSuffix')}`
    const span = (text: string, kind: ReturnType<typeof ruleCell>['kind'], align: Align, titleVal?: string) => (
      <CellSpan className={kind === 'plain' ? undefined : kind} style={align ? { textAlign: align } : undefined} title={titleVal}>
        {text}
      </CellSpan>
    )
    // a grouped row has no `.original`; render its grouping cell as the plain group key
    const grouped = (info: { cell: { getIsGrouped: () => boolean }; getValue: () => unknown }, align: Align) =>
      info.cell.getIsGrouped() ? span(String(info.getValue() ?? ''), 'plain', align) : null

    const out: ColumnDef<DataRow, unknown>[] = []
    for (const c of result.columns) {
      const align = cellAlign(c)

      if (c.rule?.kind === 'lookup') {
        const r = c.rule
        const map = lookupMaps.get(lookupKey({ connector: r.connector, query: r.query, value: r.value, label: r.label }))
        out.push({
          id: c.name,
          header: colHeader(c) + idSuffix,
          accessorFn: (row) => row[c.name],
          size: c.width ?? undefined,
          ...filterPropsFor('text'),
          cell: (info) => {
            const g = grouped(info, align)
            if (g) return g
            const { text, isNull } = ruleCell(info.getValue(), { ...c, rule: undefined }, undefined, undefined)
            return span(isNull ? 'null' : text, isNull ? 'null' : 'plain', align)
          },
        })
        out.push({
          id: `${c.name}__lookup`,
          header: colHeader(c),
          accessorFn: (row) => {
            const v = row[c.name]
            return v === null || v === undefined ? '' : (map?.get(String(v)) ?? String(v))
          },
          ...filterPropsFor('text'),
          cell: (info) => {
            const g = grouped(info, align)
            if (g) return g
            const v = (info.row.original as DataRow)[c.name]
            const { text, kind, isNull } = ruleCell(v, c, undefined, map)
            return span(text, isNull ? 'null' : kind, align, isNull ? undefined : String(v ?? ''))
          },
        })
        continue
      }

      const kind = filterKindOf(c)
      const enumMapForCol = enumMaps.get(c.name)
      const enumOptions = c.rule?.kind === 'enum'
        ? c.rule.values.map((v) => ({ value: v.label, label: v.label }))
        : undefined
      out.push({
        id: c.name,
        header: colHeader(c),
        accessorFn: (row) => {
          const v = row[c.name]
          if (c.rule?.kind === 'boolean') return v === null || v === undefined ? null : v === c.rule.true_value ? 'true' : 'false'
          if (c.rule?.kind === 'enum' && v != null) return enumMapForCol?.get(String(v)) ?? v
          return v
        },
        size: c.width ?? undefined,
        ...filterPropsFor(kind, enumOptions),
        cell: (info) => {
          const g = grouped(info, align)
          if (g) return g
          const v = (info.row.original as DataRow)[c.name]
          const { text, kind: rk, isNull } = ruleCell(v, c, enumMapForCol, undefined)
          return span(text, isNull ? 'null' : rk, align, rk === 'enum' && !isNull ? String(v ?? '') : undefined)
        },
      })
    }
    return out
  }, [result.columns, enumMaps, lookupMaps, t])

  // `hidden` hints → those columns start hidden (a saved choice in localStorage still wins).
  const initialVisibility = useMemo<VisibilityState>(() => {
    const v: VisibilityState = {}
    for (const c of result.columns) {
      if (c.hidden) {
        v[c.name] = false
        if (c.rule?.kind === 'lookup') v[`${c.name}__lookup`] = false
      }
    }
    return v
  }, [result.columns])

  return (
    <DataTable<DataRow>
      columns={columns}
      data={result.rows}
      tableId={`sql:${connector}:${query}`}
      exportFilename={query}
      initialColumnVisibility={initialVisibility}
    />
  )
}

export default ResultTable
