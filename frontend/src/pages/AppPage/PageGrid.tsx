// The shared grid (common/DataTable: search, filters, grouping, columns, export, saved views) as a
// page shipped by an application uses it. The page describes its columns and rows in a small shape
// of the contract's own — the grid's library and its types stay inside the framework — and gets
// what a data screen's table has. Part of the CONTRACT (sdk.ts): add props, never rename.
import { lazy, Suspense, useMemo, type ReactNode } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Centered } from '../../common'
import type { DataTableProps } from '../../common/DataTable'
import type { FilterMeta } from '../../common/DataTableFilter'
import { filterPropsFor } from '../TableView/resultColumns'
import { colors, fonts } from '../../theme'

// the grid is heavy (the table library, the export): loaded with the first page that draws one
const DataTable = lazy(() => import('../../common/DataTable')) as unknown as <T extends object>(props: DataTableProps<T>) => ReactNode

export type PageGridRow = Record<string, unknown>

export interface PageGridColumn<R extends PageGridRow = PageGridRow> {
  /** the row's key this column reads, and the column's id */
  key: string
  /** the heading */
  label: string
  /** how the column sorts and filters, and how a number is written (right-aligned, the user's locale) */
  type?: 'text' | 'number' | 'date'
  /** a code: drawn in the fixed-width font */
  mono?: boolean
  /** a secondary value: drawn in the muted colour */
  muted?: boolean
  /** what the column sorts, filters, searches and exports on, when it is not `row[key]` */
  value?: (row: R) => unknown
  /** what the cell draws (a link, a tag…) in place of the value's text */
  render?: (row: R) => ReactNode
}

export interface PageGridProps<R extends PageGridRow = PageGridRow> {
  columns: PageGridColumn<R>[]
  rows: R[]
  /** a stable id (`<app>:<page>:<what>`): the user's saved views of this grid are kept under it */
  id?: string
  /** a click on a row (links and buttons inside a cell keep their own click) */
  onRowClick?: (row: R) => void
  /** a CSS class on a row (`on` highlights it) */
  rowClassName?: (row: R) => string | undefined
  /** the name of the file the grid's export writes */
  exportName?: string
  /** rows per page (the grid's own choice when left out) */
  pageSize?: number
  /** controls drawn at the left of the grid's toolbar */
  toolbar?: ReactNode
  /** no toolbar: the rows and the pager only */
  bare?: boolean
  /** what a row shows when it is opened: every row gets a chevron, and this is drawn under the row
   *  (its dependencies, a nested list…). Since version 3 of the contract. */
  detail?: (row: R) => ReactNode
}

// the grid fills the box the page gives it (a height, or a flex child with min-height: 0)
const Box = styled.div`
  display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0;
  & tr.on td { background: ${colors.blue.bg}; }
`
const Cell = styled.span<{ $mono?: boolean; $muted?: boolean; $right?: boolean }>`
  display: block; overflow: hidden; text-overflow: ellipsis;
  font-family: ${({ $mono }) => ($mono ? fonts.mono : 'inherit')};
  color: ${({ $muted }) => ($muted ? colors.text.muted : 'inherit')};
  text-align: ${({ $right }) => ($right ? 'right' : 'inherit')};
`

export function PageGrid<R extends PageGridRow = PageGridRow>({ columns, rows, id, onRowClick, rowClassName, exportName, pageSize, toolbar, bare, detail }: PageGridProps<R>) {
  const { i18n } = useTranslation()
  const nf = useMemo(() => new Intl.NumberFormat(i18n.language || 'en', { maximumFractionDigits: 2 }), [i18n.language])
  const defs = useMemo<ColumnDef<R, unknown>[]>(() => columns.map((c) => {
    const num = c.type === 'number'
    const fp = filterPropsFor(c.type ?? 'text', undefined, num ? 'right' : undefined)
    const read = (row: R) => (c.value ? c.value(row) : row[c.key])
    return {
      id: c.key,
      header: c.label,
      accessorFn: read,
      filterFn: fp.filterFn,
      meta: fp.meta satisfies FilterMeta,
      cell: (info) => {
        // a group's heading cell carries the grouped value; a row draws what the page asked for
        if (!info.cell.getIsGrouped() && c.render) return c.render(info.row.original)
        const v = info.getValue()
        if (v === null || v === undefined || v === '') return <Cell $muted>—</Cell>
        if (num && typeof v === 'number') return <Cell $right $muted={c.muted}>{nf.format(v)}</Cell>
        return <Cell $mono={c.mono} $muted={c.muted} title={String(v)}>{String(v)}</Cell>
      },
    }
  }), [columns, nf])
  // another set of columns is another table: its sort, filters and grouping start again
  const shape = defs.map((d) => d.id).join('|')
  return (
    <Box>
      <Suspense fallback={<Centered />}>
        <DataTable<R> key={shape} columns={defs} data={rows} tableId={id} onRowClick={onRowClick} rowClassName={rowClassName}
          exportFilename={exportName ?? 'export'} initialPageSize={pageSize} toolbar={toolbar} chromeless={bare} renderDetail={detail} />
      </Suspense>
    </Box>
  )
}
