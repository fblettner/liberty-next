// The list of the "Open as a table" dialog (LookupDialog), drawn with the shared grid: search, a filter per column
// (a pick list of values for a column the dropdown offers chips for), sorting, grouping, the columns shown, paging.
// NOT barrelled: loaded with the dialog (the grid is heavy). The rows' shape is services/options.lookupTable.
import { lazy, Suspense, useMemo, type ReactNode } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Check } from 'lucide-react'
import { Centered } from './Spinner'
import type { DataTableProps } from './DataTable'
import { filterPropsFor } from './DataTableFilter'
import { lookupTable, type Searchable } from '../services/options'
import { colors, fonts } from '../theme'

const DataTable = lazy(() => import('./DataTable')) as unknown as <T extends object>(props: DataTableProps<T>) => ReactNode

type Row = Record<string, string>

const Box = styled.div`display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; & tr.on td { background: ${colors.blue.bg}; }`
const Cell = styled.span<{ $mono?: boolean }>`display: inline-flex; align-items: center; gap: 6px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: ${({ $mono }) => ($mono ? fonts.mono : 'inherit')};`

export default function LookupTable({ options, value, cellColumns, onPick }: {
  options: Searchable[]
  value: string
  cellColumns?: { label: string; filterable?: boolean }[]
  onPick: (value: string) => void
}) {
  const { t } = useTranslation()
  const { columns, rows } = useMemo(() => lookupTable(options, cellColumns, { code: t('lookupDialog.code'), label: t('lookupDialog.label') }), [options, cellColumns, t])
  const defs = useMemo<ColumnDef<Row, unknown>[]>(() => columns.map((c, i) => {
    const fp = c.choices ? filterPropsFor('enum', c.choices.map((v) => ({ value: v, label: v }))) : filterPropsFor('text')
    return {
      id: c.key, header: c.label, accessorFn: (r: Row) => r[c.key] ?? '', filterFn: fp.filterFn, meta: fp.meta,
      cell: (info) => {
        const v = String(info.getValue() ?? '')
        // the line picked now carries its mark in the first column
        return <Cell $mono={c.mono} title={v}>{i === 0 && info.row.original.value === value && <Check size={12} />}{v}</Cell>
      },
    }
  }), [columns, value])
  return (
    <Box data-lookup-table>
      <Suspense fallback={<Centered />}>
        <DataTable<Row> columns={defs} data={rows} onRowClick={(r) => onPick(r.value)} rowClassName={(r) => (r.value === value ? 'on' : undefined)} noExport />
      </Suspense>
    </Box>
  )
}
