// The SELECT result grid — @tanstack/react-table: sortable columns (header
// click), client-side paging, sticky header. Columns are derived from
// `result.columns`; cells go through services/cells.cellText.
import { useMemo, useState } from 'react'
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
import type { QueryResult } from '../../types/connectors'
import { Button, Row } from '../../common'
import { cellText } from '../../services/cells'
import { Meta, TableScroll, DataTable } from './styled'

const PAGE_SIZE = 50

type DataRow = Record<string, unknown>

export function ResultTable({ result }: { result: QueryResult }) {
  const { t } = useTranslation()
  const [sorting, setSorting] = useState<SortingState>([])

  const data = useMemo<DataRow[]>(() => result.rows, [result])
  const colType = useMemo(() => Object.fromEntries(result.columns.map((c) => [c.name, c.type] as const)), [result])
  const columns = useMemo<ColumnDef<DataRow>[]>(
    () =>
      result.columns.map((c) => ({
        id: c.name,
        accessorFn: (r) => r[c.name],
        header: c.name,
        cell: (info) => {
          const { text, isNull } = cellText(info.getValue())
          return <span className={isNull ? 'null' : undefined}>{text}</span>
        },
      })),
    [result],
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
                      title={colType[header.column.id] ?? undefined}
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
                  const { isNull } = cellText(cell.getValue())
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
