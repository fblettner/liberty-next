// Builds the column definitions for a SELECT result and hands them to the shared
// DataTable (sort / filter / search / hide-reorder / resize / export / paginate).
// Per-column display rules (BOOLEAN / ENUM / LOOKUP — the v2 form of v1's dd_rules)
// are applied in each cell via services/cells.ruleCell; a LOOKUP column is *split*
// into "<label> (ID)" (the raw code) + "<label>" (the resolved label) the way v1 did,
// and the lookup queries are fetched once via services/lookups.useLookupBatch.
import { useMemo, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import type { ColumnDef, VisibilityState } from '@tanstack/react-table'
import type { Column, QueryResult } from '../../types/connectors'
import { DataTable } from '../../common/DataTable'
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

  // One ColumnDef per result column — except LOOKUP columns, which become two
  // (the raw "(ID)" column then the resolved-label column). `accessorFn` returns the
  // displayed value (enum/lookup-resolved) so sort / filter / search / export work on it;
  // the boolean column sorts on its raw code.
  const columns = useMemo<ColumnDef<DataRow, unknown>[]>(() => {
    const idSuffix = ` ${t('table.idColumnSuffix')}`
    const span = (text: string, kind: ReturnType<typeof ruleCell>['kind'], align: Align, titleVal?: string) => (
      <CellSpan className={kind === 'plain' ? undefined : kind} style={align ? { textAlign: align } : undefined} title={titleVal}>
        {text}
      </CellSpan>
    )
    const out: ColumnDef<DataRow, unknown>[] = []
    for (const c of result.columns) {
      const align = cellAlign(c)
      if (c.rule?.kind === 'lookup') {
        const r = c.rule
        const map = lookupMaps.get(lookupKey({ connector: r.connector, query: r.query, value: r.value, label: r.label }))
        // raw code column
        out.push({
          id: c.name,
          header: colHeader(c) + idSuffix,
          accessorFn: (row) => row[c.name],
          size: c.width ?? undefined,
          cell: (info) => {
            const { text, isNull } = ruleCell(info.getValue(), { ...c, rule: undefined }, undefined, undefined)
            return span(isNull ? 'null' : text, isNull ? 'null' : 'plain', align)
          },
        })
        // resolved label column
        out.push({
          id: `${c.name}__lookup`,
          header: colHeader(c),
          accessorFn: (row) => {
            const v = row[c.name]
            if (v === null || v === undefined) return ''
            return map?.get(String(v)) ?? String(v)
          },
          cell: (info) => {
            const v = (info.row.original as DataRow)[c.name]
            const { text, kind, isNull } = ruleCell(v, c, undefined, map)
            return span(text, isNull ? 'null' : kind, align, isNull ? undefined : String(v ?? ''))
          },
        })
        continue
      }
      const enumMapForCol = enumMaps.get(c.name)
      out.push({
        id: c.name,
        header: colHeader(c),
        // sort/filter/search on the displayed value (enum → label); booleans stay on the raw code
        accessorFn: (row) => {
          const v = row[c.name]
          if (c.rule?.kind === 'enum' && v != null) return enumMapForCol?.get(String(v)) ?? v
          return v
        },
        size: c.width ?? undefined,
        cell: (info) => {
          const v = (info.row.original as DataRow)[c.name]
          const { text, kind, isNull } = ruleCell(v, c, enumMapForCol, undefined)
          return span(text, isNull ? 'null' : kind, align, kind === 'enum' && !isNull ? String(v ?? '') : undefined)
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
