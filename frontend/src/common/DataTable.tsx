// Generic data grid on TanStack Table v8 — ported from nomaubl's DataTable, trimmed to
// client-side mode (the data is already in memory). Features: a global search box + a
// per-column filter row, sortable + resizable columns, a column hide/reorder menu, CSV /
// Excel export, page-size + page navigation, and localStorage persistence of column
// visibility / order / sizes keyed by `tableId`. Theme-driven — every colour/size/radius
// comes from theme.ts; headers are the uppercase muted style nomaubl uses.
import { useEffect, useMemo, useRef, useState } from 'react'
import * as XLSX from 'xlsx'
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type ColumnFiltersState,
  type ColumnOrderState,
  type ColumnSizingState,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import {
  ArrowUp, ArrowDown, ChevronsUpDown, Search, Filter, Columns3, Download, FileText, Table as TableIcon,
  ChevronDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Check,
} from 'lucide-react'
import { colors, radius, fontSize, fonts, shadow } from '../theme'

const PAGE_SIZE_OPTIONS = [25, 50, 100, 200]

export interface DataTableProps<T extends object> {
  columns: ColumnDef<T, unknown>[]
  data: T[]
  /** A stable id → persists column visibility/order/sizes in localStorage. */
  tableId?: string
  /** Extra controls rendered at the left of the toolbar (e.g. an Edit toggle). */
  toolbar?: React.ReactNode
  exportFilename?: string
  initialPageSize?: number
  /** Columns hidden by default (e.g. from a `hidden` display hint) — a saved choice still wins. */
  initialColumnVisibility?: VisibilityState
}

interface SavedGrid {
  visibility: VisibilityState
  order: ColumnOrderState
  sizes: ColumnSizingState
}
function loadGrid(id: string): Partial<SavedGrid> {
  try { return JSON.parse(localStorage.getItem(`dt-${id}`) ?? '{}') } catch { return {} }
}
function saveGrid(id: string, s: SavedGrid) {
  try { localStorage.setItem(`dt-${id}`, JSON.stringify(s)) } catch { /* ignore */ }
}

// ── styled ──────────────────────────────────────────────────────────────────
const Wrap = styled.div`display: flex; flex-direction: column; min-height: 0;`
const ToolbarRow = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap;`
const Spacer = styled.div`flex: 1; min-width: 4px;`
const ActionGroup = styled.div`display: flex; gap: 4px; align-items: center; flex-shrink: 0;`

const SearchBox = styled.div`
  display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 8px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.muted}; max-width: 240px;
  & input {
    border: none; background: transparent; outline: none; min-width: 0; flex: 1;
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
    &::placeholder { color: ${colors.text.muted}; }
  }
  &:focus-within { border-color: ${colors.blue.border}; }
`
const CtrlBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 10px;
  border-radius: ${radius.md};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer; white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
  &:disabled { opacity: 0.4; cursor: default; }
`
const MenuWrap = styled.div`position: relative;`
const Dropdown = styled.div`
  position: absolute; top: calc(100% + 4px); right: 0; z-index: 100;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  padding: 6px; box-shadow: ${shadow.lg};
`
const ColMenu = styled(Dropdown)`min-width: 230px; max-height: 60vh; overflow-y: auto;`
const ExportMenu = styled(Dropdown)`min-width: 150px;`
const DropdownItem = styled.button`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 8px;
  border: none; background: transparent; border-radius: ${radius.md};
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  cursor: pointer; text-align: left;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const ColRow = styled.div`
  display: flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: ${radius.md};
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; user-select: none;
`
const ColLabel = styled.span`flex: 1; cursor: pointer; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; &:hover { color: ${colors.text.primary}; }`
const ArrowBtn = styled.button`
  display: flex; align-items: center; justify-content: center; width: 16px; height: 16px;
  border-radius: 3px; border: 1px solid ${colors.border}; background: transparent; color: ${colors.text.muted};
  cursor: pointer; padding: 0; flex-shrink: 0;
  &:hover:not(:disabled) { background: var(--hover-subtle); color: ${colors.text.primary}; }
  &:disabled { opacity: 0.25; cursor: default; }
`
const CheckBox = styled.span<{ $on: boolean }>`
  width: 14px; height: 14px; flex-shrink: 0; border-radius: 3px;
  border: 1.5px solid ${({ $on }) => ($on ? colors.blue.main : colors.border)};
  background: ${({ $on }) => ($on ? colors.blue.main : 'transparent')};
  display: flex; align-items: center; justify-content: center; cursor: pointer; color: #fff;
  transition: background 0.12s, border-color 0.12s;
`
const TableScroll = styled.div`
  overflow: auto; max-height: 60vh; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  scrollbar-width: thin;
`
const Table = styled.table`width: 100%; border-collapse: collapse; font-size: ${fontSize.sm}; font-family: ${fonts.mono};`
const Th = styled.th<{ $sortable?: boolean }>`
  text-align: left; padding: 8px 18px 8px 12px; position: sticky; top: 0; z-index: 2;
  background: ${colors.bg.dropdown}; border-bottom: 1px solid ${colors.border};
  font-size: ${fontSize.micro}; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase;
  color: ${colors.text.muted}; white-space: nowrap; user-select: none; overflow: hidden;
  cursor: ${({ $sortable }) => ($sortable ? 'pointer' : 'default')};
  ${({ $sortable }) => $sortable && `&:hover { color: ${colors.text.secondary}; }`}
`
const ThInner = styled.div`display: flex; align-items: center; gap: 4px;`
const SortMark = styled.span<{ $active: boolean }>`display: inline-flex; opacity: ${({ $active }) => ($active ? 1 : 0.3)}; color: ${({ $active }) => ($active ? colors.blue.main : 'inherit')};`
const ResizeHandle = styled.div<{ $resizing: boolean }>`
  position: absolute; top: 0; right: 0; width: 5px; height: 100%; cursor: col-resize; touch-action: none;
  background: ${({ $resizing }) => ($resizing ? colors.blue.main : 'transparent')};
  &:hover { background: ${colors.blue.border}; }
`
const FilterTh = styled.th`padding: 4px 8px; background: ${colors.bg.input}; border-bottom: 1px solid ${colors.border};`
const FilterInput = styled.input`
  width: 100%; box-sizing: border-box; background: transparent; border: 1px solid ${colors.border};
  border-radius: ${radius.sm}; color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  padding: 2px 6px; outline: none;
  &:focus { border-color: ${colors.blue.border}; }
  &::placeholder { color: ${colors.text.muted}; }
`
const Tr = styled.tr`
  &:hover td { background: var(--hover-subtle); }
  &:not(:last-child) td { border-bottom: 1px solid ${colors.border}; }
`
const Td = styled.td`
  padding: 5px 12px; color: ${colors.text.secondary}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
`
const Empty = styled.div`padding: 36px; text-align: center; color: ${colors.text.muted}; font-size: ${fontSize.base}; font-family: ${fonts.sans};`
const PaginationRow = styled.div`display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 2px 0; flex-wrap: wrap;`
const PagLeft = styled.div`display: flex; align-items: center; gap: 10px; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`
const PageSizeSelect = styled.select`
  background: transparent; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; padding: 2px 6px; cursor: pointer;
  option { background: ${colors.bg.dropdown}; color: ${colors.text.secondary}; }
`
const PagRight = styled.div`display: flex; align-items: center; gap: 3px;`
const PagBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; justify-content: center; min-width: 28px; height: 28px; padding: 0 6px;
  border-radius: ${radius.md};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  font-size: ${fontSize.sm}; font-family: ${fonts.mono}; cursor: pointer;
  transition: background 0.12s, color 0.12s, border-color 0.12s;
  &:hover:not(:disabled) { border-color: ${colors.border}; color: ${colors.text.secondary}; }
  &:disabled { opacity: 0.3; cursor: default; }
`

function pageNumbers(cur: number, total: number): (number | '…')[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1)
  const out: (number | '…')[] = [1]
  if (cur > 3) out.push('…')
  for (let p = Math.max(2, cur - 1); p <= Math.min(total - 1, cur + 1); p++) out.push(p)
  if (cur < total - 2) out.push('…')
  out.push(total)
  return out
}
const colHeaderText = (col: { id: string; columnDef: { header?: unknown } }): string =>
  typeof col.columnDef.header === 'string' ? col.columnDef.header : col.id

// ── component ───────────────────────────────────────────────────────────────
export function DataTable<T extends object>({
  columns, data, tableId, toolbar, exportFilename = 'export', initialPageSize = 50, initialColumnVisibility,
}: DataTableProps<T>) {
  const { t } = useTranslation()
  const saved = useMemo(() => (tableId ? loadGrid(tableId) : {}), [tableId])

  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(saved.visibility ?? initialColumnVisibility ?? {})
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(saved.order ?? [])
  const [columnSizing, setColumnSizing] = useState<ColumnSizingState>(saved.sizes ?? {})
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: initialPageSize })

  const [colOpen, setColOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [showFilters, setShowFilters] = useState(false)
  const colRef = useRef<HTMLDivElement>(null)
  const exportRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (tableId) saveGrid(tableId, { visibility: columnVisibility, order: columnOrder, sizes: columnSizing })
  }, [tableId, columnVisibility, columnOrder, columnSizing])

  useEffect(() => {
    if (!colOpen && !exportOpen) return
    const h = (e: MouseEvent) => {
      if (colOpen && colRef.current && !colRef.current.contains(e.target as Node)) setColOpen(false)
      if (exportOpen && exportRef.current && !exportRef.current.contains(e.target as Node)) setExportOpen(false)
    }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [colOpen, exportOpen])

  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnVisibility, columnOrder, columnSizing, columnFilters, globalFilter, pagination },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: setColumnOrder,
    onColumnSizingChange: setColumnSizing,
    onColumnFiltersChange: setColumnFilters,
    onGlobalFilterChange: setGlobalFilter,
    onPaginationChange: setPagination,
    enableColumnResizing: true,
    columnResizeMode: 'onChange',
    globalFilterFn: 'includesString',
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  })

  const allColIds = table.getAllLeafColumns().map((c) => c.id)
  const effectiveOrder = columnOrder.length
    ? [...columnOrder.filter((id) => allColIds.includes(id)), ...allColIds.filter((id) => !columnOrder.includes(id))]
    : allColIds
  const moveColumn = (idx: number, dir: 'up' | 'down') => {
    const next = [...effectiveOrder]
    const swap = dir === 'up' ? idx - 1 : idx + 1
    if (swap < 0 || swap >= next.length) return
    ;[next[idx], next[swap]] = [next[swap], next[idx]]
    setColumnOrder(next)
  }

  const exportRows = () => {
    const cols = table.getVisibleLeafColumns()
    const headers = cols.map((c) => colHeaderText(c))
    const rows = table.getFilteredRowModel().rows.map((row) =>
      cols.map((col) => {
        const v = row.getAllCells().find((c) => c.column.id === col.id)?.getValue()
        return v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)
      }),
    )
    return { headers, rows }
  }
  const exportCsv = () => {
    const { headers, rows } = exportRows()
    const esc = (s: string) => `"${s.replace(/"/g, '""')}"`
    const blob = new Blob([[headers.map(esc).join(','), ...rows.map((r) => r.map(esc).join(','))].join('\n')], {
      type: 'text/csv;charset=utf-8;',
    })
    const url = URL.createObjectURL(blob)
    Object.assign(document.createElement('a'), { href: url, download: `${exportFilename}.csv` }).click()
    URL.revokeObjectURL(url)
    setExportOpen(false)
  }
  const exportExcel = () => {
    const { headers, rows } = exportRows()
    const ws = XLSX.utils.aoa_to_sheet([headers, ...rows])
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Sheet1')
    XLSX.writeFile(wb, `${exportFilename}.xlsx`)
    setExportOpen(false)
  }

  const { pageIndex, pageSize } = table.getState().pagination
  const totalRows = table.getFilteredRowModel().rows.length
  const totalPages = Math.max(1, table.getPageCount())
  const from = totalRows === 0 ? 0 : pageIndex * pageSize + 1
  const to = Math.min((pageIndex + 1) * pageSize, totalRows)
  const visibleColCount = table.getVisibleLeafColumns().length

  return (
    <Wrap>
      <ToolbarRow>
        {toolbar}
        <SearchBox>
          <Search size={13} />
          <input
            value={globalFilter}
            onChange={(e) => table.setGlobalFilter(e.target.value)}
            placeholder={t('table.search')}
          />
        </SearchBox>
        <Spacer />
        <ActionGroup>
          <CtrlBtn $active={showFilters} onClick={() => setShowFilters((v) => !v)} title={t('table.filters')}>
            <Filter size={13} /> {t('table.filters')}
          </CtrlBtn>
          <MenuWrap ref={colRef}>
            <CtrlBtn onClick={() => setColOpen((v) => !v)} title={t('table.columns')}>
              <Columns3 size={13} /> {t('table.columns')}
            </CtrlBtn>
            {colOpen && (
              <ColMenu>
                {effectiveOrder.map((colId, idx) => {
                  const col = table.getColumn(colId)
                  if (!col) return null
                  return (
                    <ColRow key={colId}>
                      <ArrowBtn onClick={() => moveColumn(idx, 'up')} disabled={idx === 0} title={t('common.up', 'Up')}>
                        <ArrowUp size={10} />
                      </ArrowBtn>
                      <ArrowBtn onClick={() => moveColumn(idx, 'down')} disabled={idx === effectiveOrder.length - 1} title={t('common.down', 'Down')}>
                        <ArrowDown size={10} />
                      </ArrowBtn>
                      <CheckBox $on={col.getIsVisible()} onClick={() => col.toggleVisibility()}>
                        {col.getIsVisible() && <Check size={9} />}
                      </CheckBox>
                      <ColLabel onClick={() => col.toggleVisibility()}>{colHeaderText(col)}</ColLabel>
                    </ColRow>
                  )
                })}
              </ColMenu>
            )}
          </MenuWrap>
          <MenuWrap ref={exportRef}>
            <CtrlBtn onClick={() => setExportOpen((v) => !v)} title={t('table.export')}>
              <Download size={13} /> {t('table.export')} <ChevronDown size={11} />
            </CtrlBtn>
            {exportOpen && (
              <ExportMenu>
                <DropdownItem onClick={exportCsv}><FileText size={13} /> CSV (.csv)</DropdownItem>
                <DropdownItem onClick={exportExcel}><TableIcon size={13} /> Excel (.xlsx)</DropdownItem>
              </ExportMenu>
            )}
          </MenuWrap>
        </ActionGroup>
      </ToolbarRow>

      <TableScroll>
        <Table>
          <thead>
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const canSort = h.column.getCanSort()
                  const sorted = h.column.getIsSorted()
                  return (
                    <Th
                      key={h.id}
                      $sortable={canSort}
                      style={{ minWidth: h.column.columnDef.minSize ?? 56, width: h.getSize() }}
                      onClick={canSort ? h.column.getToggleSortingHandler() : undefined}
                    >
                      <ThInner>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {canSort && (
                          <SortMark $active={!!sorted}>
                            {sorted === 'asc' ? <ArrowUp size={11} /> : sorted === 'desc' ? <ArrowDown size={11} /> : <ChevronsUpDown size={11} />}
                          </SortMark>
                        )}
                      </ThInner>
                      <ResizeHandle
                        $resizing={h.column.getIsResizing()}
                        onMouseDown={h.getResizeHandler()}
                        onTouchStart={h.getResizeHandler()}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </Th>
                  )
                })}
              </tr>
            ))}
            {showFilters && (
              <tr>
                {table.getHeaderGroups()[0]?.headers.map((h) => (
                  <FilterTh key={h.id}>
                    {h.column.getCanFilter() && (
                      <FilterInput
                        value={String(h.column.getFilterValue() ?? '')}
                        onChange={(e) => h.column.setFilterValue(e.target.value || undefined)}
                        placeholder="…"
                        onClick={(e) => e.stopPropagation()}
                      />
                    )}
                  </FilterTh>
                ))}
              </tr>
            )}
          </thead>
          <tbody>
            {table.getRowModel().rows.length === 0 ? (
              <tr><td colSpan={visibleColCount}><Empty>{t('table.noResults')}</Empty></td></tr>
            ) : (
              table.getRowModel().rows.map((row) => (
                <Tr key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <Td key={cell.id} style={{ width: cell.column.getSize(), minWidth: cell.column.columnDef.minSize ?? 56 }}>
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </Td>
                  ))}
                </Tr>
              ))
            )}
          </tbody>
        </Table>
      </TableScroll>

      <PaginationRow>
        <PagLeft>
          <span>{t('table.rowsPerPage')}</span>
          <PageSizeSelect value={pageSize} onChange={(e) => { table.setPageSize(+e.target.value); table.setPageIndex(0) }}>
            {PAGE_SIZE_OPTIONS.map((n) => <option key={n} value={n}>{n}</option>)}
          </PageSizeSelect>
          <span>{t('table.showing', { from, to, total: totalRows })}</span>
        </PagLeft>
        <PagRight>
          <PagBtn onClick={() => table.setPageIndex(0)} disabled={!table.getCanPreviousPage()} title={t('common.first', 'First')}><ChevronsLeft size={13} /></PagBtn>
          <PagBtn onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()} title={t('common.prev')}><ChevronLeft size={13} /></PagBtn>
          {pageNumbers(pageIndex + 1, totalPages).map((p, i) =>
            p === '…'
              ? <span key={`e${i}`} style={{ color: colors.text.muted, padding: '0 2px', fontSize: fontSize.sm }}>…</span>
              : <PagBtn key={p} $active={p === pageIndex + 1} onClick={() => table.setPageIndex((p as number) - 1)}>{p}</PagBtn>,
          )}
          <PagBtn onClick={() => table.nextPage()} disabled={!table.getCanNextPage()} title={t('common.next')}><ChevronRight size={13} /></PagBtn>
          <PagBtn onClick={() => table.setPageIndex(totalPages - 1)} disabled={!table.getCanNextPage()} title={t('common.last', 'Last')}><ChevronsRight size={13} /></PagBtn>
        </PagRight>
      </PaginationRow>
    </Wrap>
  )
}

export default DataTable
