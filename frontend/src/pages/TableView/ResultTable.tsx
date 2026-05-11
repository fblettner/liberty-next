// The SELECT result grid — @tanstack/react-table: sortable columns (header
// click), client-side paging, sticky header. Columns come from `result.columns`,
// which may carry display hints (label / hidden / width / align — see ColumnHint
// on the backend) and an optional `rule` (BOOLEAN / ENUM / LOOKUP — the v2 form
// of v1's dd_rules). The rule is applied per cell via services/cells.ruleCell;
// LOOKUP rules need a network round-trip per (connector, query), batched and
// cached by services/lookups.useLookupBatch.
import { useMemo, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import {
  useReactTable,
  getCoreRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table'
import { ChevronLeft, ChevronRight, ArrowUp, ArrowDown } from 'lucide-react'
import type { Column, QueryResult } from '../../types/connectors'
import { Button, Row } from '../../common'
import { enumMap, ruleCell } from '../../services/cells'
import { lookupKey, useLookupBatch, type LookupSpec } from '../../services/lookups'
import { Meta, TableScroll, DataTable } from './styled'

const PAGE_SIZE = 50

type DataRow = Record<string, unknown>

function colTitle(c: Column): string {
  return c.label ?? c.name
}
function colTip(c: Column): string | undefined {
  // tooltip = the raw column name (when a label hides it) + the discovered/format type
  const bits = [c.label ? c.name : null, c.format ?? c.type].filter(Boolean)
  return bits.length ? bits.join(' · ') : undefined
}
function cellAlign(c: Column): CSSProperties['textAlign'] | undefined {
  return c.align === 'right' || c.align === 'center' || c.align === 'left' ? c.align : undefined
}

export function ResultTable({ result }: { result: QueryResult }) {
  const { t } = useTranslation()
  const [sorting, setSorting] = useState<SortingState>([])

  // Hidden columns (a `hidden` hint) are dropped entirely; the rest keep the
  // server-supplied order (the backend already applied the `columns` hint order).
  const visibleCols = useMemo(() => result.columns.filter((c) => !c.hidden), [result])
  const tipByName = useMemo(() => new Map(visibleCols.map((c) => [c.name, colTip(c)])), [visibleCols])

  // Precompute the value→label map for each ENUM column (cheap, ≤ a few values).
  const enumMaps = useMemo(() => {
    const m = new Map<string, Map<string, string>>()
    for (const c of visibleCols) if (c.rule?.kind === 'enum') m.set(c.name, enumMap(c.rule))
    return m
  }, [visibleCols])

  // Fetch the lookup query for each LOOKUP column once (cached across the session); a
  // cell falls back to its raw value while the fetch is in flight.
  const lookupSpecs = useMemo<LookupSpec[]>(
    () =>
      visibleCols
        .filter((c) => c.rule?.kind === 'lookup')
        .map((c) => {
          const r = c.rule as Extract<NonNullable<Column['rule']>, { kind: 'lookup' }>
          return { connector: r.connector, query: r.query, value: r.value, label: r.label }
        }),
    [visibleCols],
  )
  const lookupMaps = useLookupBatch(lookupSpecs)

  const data = useMemo<DataRow[]>(() => result.rows, [result])
  const columns = useMemo<ColumnDef<DataRow>[]>(
    () =>
      visibleCols.map((c) => {
        const align = cellAlign(c)
        const enumMapForCol = enumMaps.get(c.name)
        const lookupMapForCol = c.rule?.kind === 'lookup'
          ? lookupMaps.get(lookupKey({ connector: c.rule.connector, query: c.rule.query, value: c.rule.value, label: c.rule.label }))
          : undefined
        return {
          id: c.name,
          accessorFn: (r) => r[c.name],  // sort by the raw value — rule rendering is visual-only
          header: colTitle(c),
          size: c.width ?? undefined,
          cell: (info) => {
            const { text, isNull, kind } = ruleCell(info.getValue(), c, enumMapForCol, lookupMapForCol)
            return (
              <span
                className={isNull ? 'null' : kind === 'plain' ? undefined : kind}
                style={align ? { display: 'block', textAlign: align } : undefined}
                title={kind === 'lookup' || kind === 'enum' || kind === 'lookup-pending' ? String(info.getValue() ?? '') : undefined}
              >
                {text}
              </span>
            )
          },
        }
      }),
    [visibleCols, enumMaps, lookupMaps],
  )

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    initialState: { pagination: { pageSize: PAGE_SIZE } },
  })

  const { pageIndex } = table.getState().pagination
  const pageCount = table.getPageCount()

  return (
    <>
      <TableScroll>
        <DataTable>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => {
                  const sorted = header.column.getIsSorted()
                  return (
                    <th
                      key={header.id}
                      className="sortable"
                      title={tipByName.get(header.column.id)}
                      onClick={header.column.getToggleSortingHandler()}
                    >
                      {flexRender(header.column.columnDef.header, header.getContext())}
                      {sorted && (
                        <span className="ix">{sorted === 'asc' ? <ArrowUp size={12} /> : <ArrowDown size={12} />}</span>
                      )}
                    </th>
                  )
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id}>
                {row.getVisibleCells().map((cell) => {
                  const v = cell.getValue()
                  const isNull = v === null || v === undefined
                  return (
                    <td key={cell.id} className={isNull ? 'null' : undefined}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </DataTable>
      </TableScroll>
      {pageCount > 1 && (
        <Row>
          <Button $size="sm" disabled={!table.getCanPreviousPage()} onClick={() => table.previousPage()}>
            <ChevronLeft size={13} /> {t('common.prev')}
          </Button>
          <Meta>
            {t('common.page')} {pageIndex + 1} {t('common.of')} {pageCount}
          </Meta>
          <Button $size="sm" disabled={!table.getCanNextPage()} onClick={() => table.nextPage()}>
            {t('common.next')} <ChevronRight size={13} />
          </Button>
        </Row>
      )}
    </>
  )
}

export default ResultTable
