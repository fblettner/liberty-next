// Generic data grid on TanStack Table v8 — adapted from nomaubl's DataTable, trimmed to
// client-side mode (the data is already in memory). Features: a global search box (searches
// every column — see `getColumnCanGlobalFilter` below); a per-column filter row whose control
// adapts to the column's type (text/number/date with an operator, boolean/enum as a select —
// see DataTableFilter); multi-column sort; row grouping (expand/collapse); a column hide/reorder
// menu (columns aren't user-resizable — `table-layout: auto` lets the browser content-size them);
// CSV / Excel export; page-size + page navigation; and localStorage persistence of column
// visibility + order keyed by `tableId`. Theme-driven throughout.
//
// **Row virtualization (always-on)** — via `@tanstack/react-virtual` against the scroll
// container (TableScroll). Only the visible rows + a small overscan window are mounted to the
// DOM; off-screen rows are replaced by top + bottom spacer ``<tr>``s that hold the scroll
// extent. Row heights are auto-measured (`measureElement`) so an edit-mode row that grows
// taller or a group header doesn't break the layout. Columns stay browser-sized
// (``table-layout: auto``) — there's some practical column jitter on extreme scrolls when the
// visible window's content widths shift, accepted as the price of always-on virtualization
// (the alternative is locking widths via colgroup which makes drag-reorder + content-fit
// worse). Threshold = always — there's no behavioural cliff between small and large grids;
// virtualization has no cost when there are only 25 rows on screen.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import * as XLSX from 'xlsx'
import {
  useReactTable,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  getGroupedRowModel,
  getExpandedRowModel,
  getPaginationRowModel,
  flexRender,
  type ColumnDef,
  type ColumnFiltersState,
  type ColumnOrderState,
  type ExpandedState,
  type GroupingState,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import {
  ArrowUp, ArrowDown, ChevronsUpDown, Search, Filter, FilterX, Columns3, Group, Download, FileText,
  Table as TableIcon, ChevronDown, ChevronRight, ChevronLeft, ChevronsLeft, ChevronsRight, Check,
} from 'lucide-react'
import { colors, radius, fontSize, fonts, shadow } from '../theme'
import { ColumnFilterControl, type FilterMeta } from './DataTableFilter'

// "All" (Number.MAX_SAFE_INTEGER) renders every loaded row in one virtualised page — the
// pagination chrome collapses into a single page. Useful for screens with high ``max_rows``
// where the operator wants to scroll through the whole result without paging.
const PAGE_SIZE_ALL = Number.MAX_SAFE_INTEGER
const PAGE_SIZE_OPTIONS: number[] = [25, 50, 100, 200, 500, 1000, PAGE_SIZE_ALL]

export interface DataTableProps<T extends object> {
  columns: ColumnDef<T, unknown>[]
  data: T[]
  /** A stable id → persists column visibility/order/sizes in localStorage. */
  tableId?: string
  /** Extra controls rendered at the left of the toolbar (e.g. an Edit toggle). */
  toolbar?: React.ReactNode
  /** Controls rendered immediately to the right of the search box (e.g. a Run button). */
  toolbarAfterSearch?: React.ReactNode
  /** Controls rendered at the far right, just before the Filters/Columns/Export group (e.g. a Max-rows input). */
  toolbarRight?: React.ReactNode
  exportFilename?: string
  initialPageSize?: number
  /** Columns hidden by default (e.g. from a `hidden` display hint) — a saved choice still wins. */
  initialColumnVisibility?: VisibilityState
  /** Default tanstack-table grouping — column id(s) the grid groups by on first mount. The
   *  operator can still ungroup / regroup from the Group control; this only seeds initial
   *  state. Empty / undefined = no default grouping (flat rows). */
  initialGrouping?: GroupingState
  /** Per-row CSS class on the `<tr>` — e.g. `dt-row-new` / `dt-row-deleted` to tint edited rows. */
  rowClassName?: (row: T) => string | undefined
  /** When provided, clicking a (non-grouped) row's body fires this. Clicks on interactive cell
   *  children (links, buttons, inputs) still take precedence — the handler bails when the click
   *  target lives inside such a control, so the user can copy text and use cell widgets normally.
   *  The TableView wires this to "open the screen dialog on the clicked row" when a dialog exists. */
  onRowClick?: (row: T) => void
  /** When provided, right-clicking a (non-grouped) row fires this — the consumer typically opens
   *  a positioned overlay menu at the click coords (the TableView wires this to ``Screen.row_menu``,
   *  the slice-4 ``Action`` chain bound to each row). ``event.preventDefault()`` is called for you
   *  so the browser's native context menu doesn't appear; we don't suppress it on the table chrome
   *  itself, so headers / pagination still get their native menu. */
  onRowContextMenu?: (row: T, event: React.MouseEvent<HTMLTableRowElement>) => void
  /** When provided, a paste (Ctrl+V) over the grid — while NOT focused in a cell input — parses the
   *  clipboard's TSV (an Excel copy) into rows keyed by VISIBLE column id and fires this. Mapping uses
   *  the table's own visible leaf columns (the exact set + order the export emits), by HEADER when the
   *  first pasted row matches column headers, else by POSITION — so it can't drift from what's shown.
   *  The consumer (TableView) turns the rows into new bulk-edit rows. */
  onPasteRows?: (rows: Record<string, unknown>[]) => void
}

interface SavedGrid {
  visibility: VisibilityState
  order: ColumnOrderState
}
function loadGrid(id: string): Partial<SavedGrid> {
  try { return JSON.parse(localStorage.getItem(`dt-${id}`) ?? '{}') } catch { return {} }
}
function saveGrid(id: string, s: SavedGrid) {
  try { localStorage.setItem(`dt-${id}`, JSON.stringify(s)) } catch { /* ignore */ }
}

// ── styled ──────────────────────────────────────────────────────────────────
// The DataTable fills its parent vertically — toolbar stays put at the top, the data rows scroll
// inside `TableScroll` below it. The parent (TableView's Stack) must be a flex column with
// `flex: 1; min-height: 0` for the chain to work.
const Wrap = styled.div`display: flex; flex-direction: column; min-height: 0; flex: 1;`
const ToolbarRow = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap;`
const Spacer = styled.div`flex: 1; min-width: 4px;`
const ActionGroup = styled.div`display: flex; gap: 4px; align-items: center; flex-shrink: 0;`
const SearchBox = styled.div`
  display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 8px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.muted}; min-width: 240px; max-width: 360px;
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
// Portaled to ``document.body`` with ``position: fixed`` (coords from the trigger) so the menu
// escapes any ancestor ``overflow`` — without this a Columns / Group / Export menu opened from a
// table inside a *non-maximised* dialog is clipped by the dialog body (visible only when the dialog
// is full-screen). Same pattern as ``SearchSelect``'s dropdown. ``max-height`` is set inline from
// the live space below/above the trigger so the inner list scrolls instead of running off-screen.
const FloatingMenu = styled.div`
  position: fixed; z-index: 1000;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  padding: 6px; box-shadow: ${shadow.lg}; overflow-y: auto; scrollbar-width: thin;
`

/** A dropdown that renders into ``document.body`` anchored to ``anchorRef``'s trigger, right-aligned
 *  to it, flipping above when there isn't room below, and capping its own height to the available
 *  viewport space. Closes on outside-click (ignoring the anchor, whose own onClick toggles), Escape,
 *  and follows the trigger on scroll/resize. */
function MenuPortal({
  open, anchorRef, onClose, minWidth, children,
}: {
  open: boolean
  anchorRef: React.RefObject<HTMLElement | null>
  onClose: () => void
  minWidth: number
  children: React.ReactNode
}) {
  const panelRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top?: number; bottom?: number; right: number; maxHeight: number } | null>(null)
  useEffect(() => {
    if (!open) { setPos(null); return }
    const compute = () => {
      const el = anchorRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      const vw = window.innerWidth
      const vh = window.innerHeight
      const GAP = 4
      const MIN = 160
      const MAX = Math.round(vh * 0.6)
      const spaceBelow = vh - r.bottom - GAP
      const spaceAbove = r.top - GAP
      const flipUp = spaceBelow < MIN && spaceAbove > spaceBelow
      const right = Math.max(GAP, vw - r.right)
      // Anchor the edge that stays put (top for below, bottom for above) so we don't need the
      // panel's own height to position it; ``maxHeight`` caps the scroll either way.
      setPos(flipUp
        ? { bottom: vh - r.top + GAP, right, maxHeight: Math.min(MAX, Math.max(MIN, spaceAbove)) }
        : { top: r.bottom + GAP, right, maxHeight: Math.min(MAX, Math.max(MIN, spaceBelow)) })
    }
    compute()
    const onDoc = (e: MouseEvent) => {
      const inAnchor = anchorRef.current?.contains(e.target as Node)
      const inPanel = panelRef.current?.contains(e.target as Node)
      if (!inAnchor && !inPanel) onClose()
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', compute)
    window.addEventListener('scroll', compute, true)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', compute)
      window.removeEventListener('scroll', compute, true)
    }
  }, [open, anchorRef, onClose])
  if (!open || !pos) return null
  return createPortal(
    <FloatingMenu ref={panelRef} style={{ ...pos, minWidth }}>{children}</FloatingMenu>,
    document.body,
  )
}
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
const MenuTitle = styled.div`padding: 4px 8px 6px; font-size: ${fontSize.micro}; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em; color: ${colors.text.muted};`
const MenuHead = styled.div`display: flex; align-items: center; gap: 6px; padding: 0 2px 2px; border-bottom: 1px solid ${colors.border}; margin-bottom: 4px;`
const MiniLink = styled.button`
  border: none; background: transparent; color: ${colors.blue.main}; font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  cursor: pointer; padding: 2px 4px; border-radius: 3px; text-transform: uppercase; letter-spacing: 0.05em;
  &:hover { background: var(--hover-subtle); }
`
// Cap at the space the table actually has below the page chrome (header + the run/toolbar rows +
// the result-meta line + the pager) — `100dvh - ~14rem` — so a result that fits doesn't get a
// (clipped) inner scrollbar, and a bigger one scrolls inside the box with its last row fully visible.
// The data rows scroll *inside* the TanStack table — `flex: 1; min-height: 0` fills the
// vertical space Wrap leaves after the ToolbarRow, and `overflow: auto` gives the rows their
// own scrollbar so the filter panel, search row, etc. above the table stay fixed regardless
// of how many rows-per-page the user picks. `<thead>` rows are `position: sticky; top: 0`
// inside this box, so the column headers stay visible while the body scrolls.
const TableScroll = styled.div`
  flex: 1; min-height: 0; overflow: auto;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg}; scrollbar-width: thin;
`
// `table-layout: auto` (the default): the browser sizes columns to their content — the best fit in
// practice. (Column *resizing* was tried with `table-layout: fixed`, but that forced widths the
// content didn't suit and interfered with header drag-reorder, so it was dropped — columns aren't
// user-resizable; hide the column or let the content breathe.)
const Table = styled.table`border-collapse: collapse; width: 100%; font-size: ${fontSize.sm}; font-family: ${fonts.mono};`
// ``STICKY_BG`` composites the (light-theme-translucent) dropdown tint over the opaque base, so a
// sticky header / filter cell is FULLY opaque — a translucent sticky cell lets the scrolling data
// rows bleed through (most visible in light mode, where ``--bg-dropdown`` is only 95% opaque).
const STICKY_BG = `linear-gradient(${colors.bg.dropdown}, ${colors.bg.dropdown}), ${colors.bg.base}`
const Th = styled.th<{ $sortable?: boolean; $dropTarget?: boolean }>`
  text-align: left; padding: 8px 18px 8px 12px; position: sticky; top: 0; z-index: 2;
  background: ${STICKY_BG}; border-bottom: 1px solid ${colors.border};
  border-left: 2px solid ${({ $dropTarget }) => ($dropTarget ? colors.blue.main : 'transparent')};
  font-size: ${fontSize.micro}; font-weight: 700; letter-spacing: 0.07em; text-transform: uppercase;
  color: ${colors.text.muted}; white-space: nowrap; user-select: none;
  cursor: ${({ $sortable }) => ($sortable ? 'pointer' : 'default')};
  ${({ $sortable }) => $sortable && `&:hover { color: ${colors.text.secondary}; }`}
`
const ThInner = styled.div<{ $align?: FilterMeta['align'] }>`
  display: flex; align-items: center; gap: 4px;
  justify-content: ${({ $align }) => ($align === 'right' ? 'flex-end' : $align === 'center' ? 'center' : 'flex-start')};
`
const SortMark = styled.span<{ $active: boolean }>`display: inline-flex; align-items: center; opacity: ${({ $active }) => ($active ? 1 : 0.3)}; color: ${({ $active }) => ($active ? colors.blue.main : 'inherit')};`
const SortIx = styled.sup`font-size: 8px; color: ${colors.blue.main}; margin-left: 1px;`
const GroupBadge = styled.span`display: inline-flex; align-items: center; color: ${colors.blue.main};`
// Filter row sits ABOVE the header row, both ``position: sticky`` so the filters stay on screen
// while the body scrolls (before, the filter row scrolled out of view under the sticky header).
// ``z-index: 3`` keeps it above the header (z-index 2) at the corners.
// Background MUST be opaque (see ``STICKY_BG``) — a sticky row with a see-through background lets
// the scrolling data rows bleed through it.
const FilterTh = styled.th`padding: 4px 8px; background: ${STICKY_BG}; border-bottom: 1px solid ${colors.border}; position: sticky; top: 0; z-index: 3;`
const Tr = styled.tr`
  &:hover td { background: var(--hover-subtle); }
  &:not(:last-child) td { border-bottom: 1px solid ${colors.border}; }
  &.dt-row-new td { background: ${colors.green.bg}; box-shadow: inset 3px 0 0 ${colors.green.main}; }
  &.dt-row-dirty td { background: ${colors.orange.bg}; box-shadow: inset 3px 0 0 ${colors.orange.main}; }
  &.dt-row-deleted td { background: ${colors.red.bg}; box-shadow: inset 3px 0 0 ${colors.red.main}; text-decoration: line-through; }
`
const GroupTr = styled.tr` td { background: ${colors.bg.card}; font-family: ${fonts.sans}; }`
const Td = styled.td`padding: 5px 12px; color: ${colors.text.secondary}; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;`
const GroupCellBtn = styled.button`
  display: inline-flex; align-items: center; gap: 6px; border: none; background: none; cursor: pointer;
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600;
  & svg { color: ${colors.text.muted}; }
`
const GroupCount = styled.span`color: ${colors.text.muted}; font-weight: 400;`
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
  columns, data, tableId, toolbar, toolbarAfterSearch, toolbarRight, exportFilename = 'export', initialPageSize = 50, initialColumnVisibility, initialGrouping, rowClassName, onRowClick, onRowContextMenu, onPasteRows,
}: DataTableProps<T>) {
  const { t } = useTranslation()
  const saved = useMemo(() => (tableId ? loadGrid(tableId) : {}), [tableId])

  // Start from the column hints' hidden flags, then layer the user's saved choices on top — so a
  // `hidden` hint takes effect on first load *and* survives a stale saved `{}` from before the hint
  // existed, while still letting the user un-hide a column and have that stick.
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(() => ({ ...(initialColumnVisibility ?? {}), ...(saved.visibility ?? {}) }))
  const [columnOrder, setColumnOrder] = useState<ColumnOrderState>(saved.order ?? [])
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([])
  const [globalFilter, setGlobalFilter] = useState('')
  // Seed grouping from the screen-level default (initialGrouping). Subsequent user changes
  // (via the Group control) replace this list; we don't re-seed on prop change so an operator
  // who ungrouped a screen and reloaded isn't yanked back to the default.
  //
  // Case-insensitive resolution against the actual tanstack column IDs (which come from the
  // raw query result — lowercase under Postgres, which folds unquoted identifiers). Operators
  // routinely declare screen column hints in UPPERCASE (``CLA_MODULE``) by convention; the
  // ``cur()`` cell reader already falls back case-insensitively for that reason. Without this
  // resolution, ``initial_group_by = ["CLA_MODULE"]`` would silently no-op (tanstack would
  // look for a column literally named ``CLA_MODULE``, find none, drop the grouping). Unknown
  // names fall through unchanged — useful if the operator types a column that only appears
  // after a query change (won't crash; just won't group until the column lands).
  const resolvedInitialGrouping = useMemo<GroupingState>(() => {
    if (!initialGrouping?.length) return []
    const colIds = new Map<string, string>()
    for (const c of columns) {
      const id = String((c as { id?: string }).id ?? (c as { accessorKey?: string }).accessorKey ?? '')
      if (id) colIds.set(id.toLowerCase(), id)
    }
    return initialGrouping.map((n) => colIds.get(n.toLowerCase()) ?? n)
  }, [initialGrouping, columns])
  const [grouping, setGrouping] = useState<GroupingState>(resolvedInitialGrouping)
  // Auto-expand all groups when a default grouping is present — operators landed on this
  // screen *because* they want the grouped view; landing on N collapsed group rows with no
  // detail is a worse first impression than the rows. true = "expand every group".
  const [expanded, setExpanded] = useState<ExpandedState>(
    resolvedInitialGrouping.length > 0 ? true : {},
  )
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: initialPageSize })

  const [colOpen, setColOpen] = useState(false)
  const [groupOpen, setGroupOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [showFilters, setShowFilters] = useState(false)
  const colRef = useRef<HTMLDivElement>(null)
  const groupRef = useRef<HTMLDivElement>(null)
  const exportRef = useRef<HTMLDivElement>(null)
  const dragColRef = useRef<string | null>(null) // header drag-reorder: the column id being dragged
  const [dragOverId, setDragOverId] = useState<string | null>(null)
  // Live height of the (sticky) filter row — drives the header row's sticky ``top`` offset so the
  // header pins directly below the filter row instead of overlapping it. 0 when filters are hidden.
  const [filterRowH, setFilterRowH] = useState(0)
  const filterRowRef = useRef<HTMLTableRowElement>(null)
  useEffect(() => {
    if (!showFilters) { setFilterRowH(0); return }
    const el = filterRowRef.current
    if (!el) return
    const update = () => setFilterRowH(el.offsetHeight)
    update()
    const ro = new ResizeObserver(update)
    ro.observe(el)
    return () => ro.disconnect()
  }, [showFilters])

  useEffect(() => {
    if (tableId) saveGrid(tableId, { visibility: columnVisibility, order: columnOrder })
  }, [tableId, columnVisibility, columnOrder])

  // Reset the grid's persisted visibility + order back to the screen's defaults — clears the
  // localStorage entry *and* re-applies ``initialColumnVisibility`` (which encodes the screen's
  // ``hidden`` flags). Needed when a screen has ``visible_when`` rules: an old saved-visibility
  // entry can override a column the rule wants shown (or hidden) given the current filter,
  // leaving the operator unable to see / hide a column without manually toggling it in the
  // Columns menu. "Reset" gives them a single-click way back to the configured defaults.
  const resetGrid = useCallback(() => {
    if (tableId) {
      try { localStorage.removeItem(`dt-${tableId}`) } catch { /* ignore */ }
    }
    setColumnVisibility({ ...(initialColumnVisibility ?? {}) })
    setColumnOrder([])
  }, [tableId, initialColumnVisibility])

  // Internal columns (select / status) are always pinned to the front, regardless of the user's
  // saved (data-only) column order; `columnOrder` persists only the data columns.
  const internalIds = useMemo(
    () => columns.filter((c) => (c.meta as { internal?: boolean } | undefined)?.internal).map((c) => String((c as { id?: string }).id ?? '')),
    [columns],
  )
  const dataIds = useMemo(
    () => columns.filter((c) => !(c.meta as { internal?: boolean } | undefined)?.internal).map((c) => String((c as { id?: string }).id ?? '')),
    [columns],
  )
  const dataOrder = useMemo(
    () => (columnOrder.length ? [...columnOrder.filter((id) => dataIds.includes(id)), ...dataIds.filter((id) => !columnOrder.includes(id))] : dataIds),
    [columnOrder, dataIds],
  )
  const tableColumnOrder = useMemo(() => [...internalIds, ...dataOrder], [internalIds, dataOrder])

  // Stable per-row identity. Without it TanStack falls back to index-based row ids, which SHIFT
  // when a row is prepended/removed (bulk-edit "Add row" / paste insert at the top) — that
  // invalidates the row virtualizer's measurement cache (keyed by index via ``data-index``), so
  // the new top row renders stale/offset until a scroll forces a re-measure. A WeakMap keyed by the
  // row object hands out a stable id that follows the object across reorders (data objects keep
  // their references across renders; only the surrounding array is rebuilt). Non-object rows fall
  // back to the index.
  const rowIdMap = useRef(new WeakMap<object, string>())
  const rowIdSeq = useRef(0)
  const stableRowId = useCallback((row: T, index: number): string => {
    if (row == null || typeof row !== 'object') return String(index)
    let id = rowIdMap.current.get(row)
    if (id === undefined) { id = `row-${rowIdSeq.current++}`; rowIdMap.current.set(row, id) }
    return id
  }, [])
  const table = useReactTable({
    data,
    columns,
    getRowId: stableRowId,
    state: { sorting, columnVisibility, columnOrder: tableColumnOrder, columnFilters, globalFilter, grouping, expanded, pagination },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onColumnOrderChange: (updater) => {
      const next = typeof updater === 'function' ? updater(tableColumnOrder) : updater
      setColumnOrder(next.filter((id) => !internalIds.includes(id)))
    },
    onColumnFiltersChange: setColumnFilters,
    onGlobalFilterChange: setGlobalFilter,
    onGroupingChange: setGrouping,
    onExpandedChange: setExpanded,
    onPaginationChange: setPagination,
    enableColumnResizing: false,
    enableMultiSort: true, // shift-click a header adds it to the sort
    isMultiSortEvent: (e) => (e as MouseEvent).shiftKey,
    globalFilterFn: 'includesString',
    // search every column, even ones whose first row happens to be null (TanStack's default would
    // exclude such a column from the global filter — that's why the search "missed" some columns)
    getColumnCanGlobalFilter: (col) => !((col.columnDef.meta as { internal?: boolean } | undefined)?.internal),
    autoResetExpanded: false,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getGroupedRowModel: getGroupedRowModel(),
    getExpandedRowModel: getExpandedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
  })

  // the data columns in user order — used by the Columns menu (↑/↓) and the header drag-reorder
  const effectiveOrder = dataOrder
  const moveColumn = (idx: number, dir: 'up' | 'down') => {
    const next = [...effectiveOrder]
    const swap = dir === 'up' ? idx - 1 : idx + 1
    if (swap < 0 || swap >= next.length) return
    ;[next[idx], next[swap]] = [next[swap], next[idx]]
    setColumnOrder(next)
  }
  // header drag-reorder: move `draggedId` to where `targetId` sits
  const dropColumn = (draggedId: string, targetId: string) => {
    if (draggedId === targetId || internalIds.includes(draggedId) || internalIds.includes(targetId)) return
    const ids = effectiveOrder.filter((id) => id !== draggedId)
    const at = ids.indexOf(targetId)
    if (at < 0) return
    ids.splice(at, 0, draggedId)
    setColumnOrder(ids)
  }

  const isInternal = (c: { columnDef: { meta?: unknown } }) => !!(c.columnDef.meta as FilterMeta | undefined)?.internal
  const colAlign = (c: { columnDef: { meta?: unknown } }) => (c.columnDef.meta as FilterMeta | undefined)?.align
  const exportRows = () => {
    const cols = table.getVisibleLeafColumns().filter((c) => !isInternal(c))
    const headers = cols.map((c) => colHeaderText(c))
    const rows = table.getFilteredRowModel().rows.map((row) =>
      cols.map((col) => {
        // A column may override its export value (e.g. BOOLEAN emits the raw "1"/"0" code, not the
        // "true"/"false" display token its accessor uses for sort/filter) so a re-import round-trips.
        const ev = (col.columnDef.meta as FilterMeta | undefined)?.exportValue
        const v = ev ? ev(row.original) : row.getAllCells().find((c) => c.column.id === col.id)?.getValue()
        return v === null || v === undefined ? '' : typeof v === 'object' ? JSON.stringify(v) : String(v)
      }),
    )
    return { headers, rows }
  }

  // Paste from Excel → new rows. Maps the clipboard's TSV using the table's OWN visible leaf columns
  // (the exact set + order the export emits — so it can't drift from what's displayed): HEADER mode
  // when the first pasted row matches ≥2 column headers (order/label/hidden-independent), else by
  // POSITION, with a LOOKUP/ENUM's derived label column as a skipped placeholder. Bails when a cell
  // input is focused so a normal single-cell paste still works.
  useEffect(() => {
    if (!onPasteRows) return
    const isLabelId = (id: string) => /(?:__lookup|__enum)$/.test(id)
    const onPaste = (e: ClipboardEvent) => {
      const ae = document.activeElement as HTMLElement | null
      const tag = ae?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || ae?.isContentEditable) return
      const text = e.clipboardData?.getData('text/plain') ?? ''
      if (!text || (!text.includes('\t') && !text.includes('\n'))) return
      const lines = text.replace(/\r\n?/g, '\n').split('\n')
      while (lines.length && lines[lines.length - 1].trim() === '') lines.pop()
      if (lines.length === 0) return
      const grid = lines.map((ln) => ln.split('\t'))
      const cols = table.getVisibleLeafColumns().filter((c) => !isInternal(c))
      const headerById = new Map<string, string>()  // header text → data column id (skip label cols)
      for (const c of cols) if (!isLabelId(c.id)) headerById.set(colHeaderText(c).trim().toLowerCase(), c.id)
      const first = grid[0]
      const matched = first.filter((h) => headerById.get(h.trim().toLowerCase())).length
      let targets: (string | null)[]
      let body: string[][]
      if (matched >= 2) {
        targets = first.map((h) => headerById.get(h.trim().toLowerCase()) ?? null)
        body = grid.slice(1)
      } else {
        targets = cols.map((c) => (isLabelId(c.id) ? null : c.id))
        body = grid
      }
      const rows = body.map((cells) => {
        const out: Record<string, unknown> = {}
        cells.forEach((v, i) => { const tgt = targets[i]; if (tgt && v !== '') out[tgt] = v })
        return out
      }).filter((r) => Object.keys(r).length > 0)
      if (rows.length === 0) return
      e.preventDefault()
      onPasteRows(rows)
    }
    document.addEventListener('paste', onPaste)
    return () => document.removeEventListener('paste', onPaste)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- isInternal/colHeaderText are stable; table is the live instance
  }, [onPasteRows, table])

  // ── row virtualizer ───────────────────────────────────────────────────────────────────
  // Mount only the rows that fit (plus a small overscan window) into the DOM. The scroll
  // container is `TableScroll`; the virtualizer takes the **post-pagination** row list (so
  // TanStack's existing per-page slicing still scopes what's reachable). When the user picks
  // page size = "All", the row list is the full filtered/grouped set — virtualization is the
  // *only* thing keeping the DOM small at that scale.
  //
  // Row heights: we estimate ~26px from the existing typography (5px top/bottom padding +
  // 11px monospace line-height + small fudge). Each row gets a `data-index` and uses
  // `measureElement` so group rows / edit-mode rows / dialog-changed rows that grow a few
  // pixels taller get measured for real — no need to hardcode multiple sizes per kind.
  const tableScrollRef = useRef<HTMLDivElement | null>(null)
  const visibleRows = table.getRowModel().rows
  const rowVirtualizer = useVirtualizer({
    count: visibleRows.length,
    getScrollElement: () => tableScrollRef.current,
    estimateSize: () => 26,
    overscan: 10,
    // Track measurements by the stable row id (not the index) so a prepend/remove shifts the cache
    // WITH the rows — fixes the "added top row renders stale until you scroll" glitch.
    getItemKey: (index) => visibleRows[index]?.id ?? String(index),
    // The default `measureElement` reads each row's true height via getBoundingClientRect +
    // correlates by `data-index`. We pass `ref={rowVirtualizer.measureElement}` on each row
    // (below) so a group row, edit-mode row, or wrapped cell that's taller than the estimate
    // gets re-measured automatically — no need to override the function here.
  })
  const virtualItems = rowVirtualizer.getVirtualItems()
  // Spacer math: top spacer pushes content down to the first visible row's `start`; bottom
  // spacer fills the gap between the last visible row's `end` and the total height. Both
  // empty rows are styled with the right height; cell content is intentionally empty (a
  // single `<td colSpan>` keeps the `<tr>` shape valid without rendering anything).
  const totalSize = rowVirtualizer.getTotalSize()
  const paddingTop = virtualItems.length > 0 ? virtualItems[0].start : 0
  const paddingBottom = virtualItems.length > 0 ? totalSize - virtualItems[virtualItems.length - 1].end : 0
  // The thead carries 1 sticky header row + optionally 1 sticky filter row above it. The
  // virtualizer's body is everything below that. `colCount` is what each spacer row needs
  // to span — equal to the visible column count.

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
  const totalRows = table.getRowModel().rows.length // post-filter, post-group flat-ish count for display
  const filteredLeafCount = table.getFilteredRowModel().rows.length
  const totalPages = Math.max(1, table.getPageCount())
  // ``PAGE_SIZE_ALL`` makes `pageIndex * pageSize` overflow into Infinity; clamp to the row
  // count so the "Showing X-Y of Z" line stays sensible at the All setting.
  const showAll = pageSize === PAGE_SIZE_ALL
  const from = totalRows === 0 ? 0 : (showAll ? 1 : pageIndex * pageSize + 1)
  const to = showAll ? totalRows : Math.min((pageIndex + 1) * pageSize, totalRows)
  const visibleColCount = table.getVisibleLeafColumns().length
  const groupableCols = table.getAllLeafColumns().filter((c) => c.getCanGroup())

  return (
    <Wrap>
      <ToolbarRow>
        {toolbar}
        <SearchBox>
          <Search size={13} />
          <input value={globalFilter} onChange={(e) => table.setGlobalFilter(e.target.value)} placeholder={t('table.search')} />
        </SearchBox>
        {toolbarAfterSearch}
        <Spacer />
        {toolbarRight}
        <ActionGroup>
          <CtrlBtn $active={showFilters} onClick={() => setShowFilters((v) => !v)} title={t('table.filters')}>
            <Filter size={13} /> {t('table.filters')}
          </CtrlBtn>
          {columnFilters.length > 0 && (
            <CtrlBtn onClick={() => setColumnFilters([])} title={t('table.clearFilters')}>
              <FilterX size={13} /> {t('table.clearFilters')}
            </CtrlBtn>
          )}
          {groupableCols.length > 0 && (
            <MenuWrap ref={groupRef}>
              <CtrlBtn $active={grouping.length > 0} onClick={() => setGroupOpen((v) => !v)} title={t('table.group')}>
                <Group size={13} /> {t('table.group')}{grouping.length ? ` (${grouping.length})` : ''}
              </CtrlBtn>
              <MenuPortal open={groupOpen} anchorRef={groupRef} onClose={() => setGroupOpen(false)} minWidth={230}>
                  <MenuTitle>{t('table.groupBy')}</MenuTitle>
                  {/* "None" — clears every grouping in one click. Renders only when at least
                      one grouping is active; collapsing each grouped column individually was
                      the only way to ungroup before this entry landed. */}
                  {grouping.length > 0 && (
                    <ColRow onClick={() => setGrouping([])} style={{ cursor: 'pointer' }}>
                      <CheckBox $on={false} />
                      <ColLabel>{t('table.groupNone', '(None — ungroup all)')}</ColLabel>
                    </ColRow>
                  )}
                  {effectiveOrder.map((colId) => {
                    const col = table.getColumn(colId)
                    if (!col || !col.getCanGroup()) return null
                    const on = col.getIsGrouped()
                    return (
                      <ColRow key={colId} onClick={() => col.toggleGrouping()} style={{ cursor: 'pointer' }}>
                        <CheckBox $on={on}>{on && <Check size={9} />}</CheckBox>
                        <ColLabel>{colHeaderText(col)}</ColLabel>
                      </ColRow>
                    )
                  })}
              </MenuPortal>
            </MenuWrap>
          )}
          <MenuWrap ref={colRef}>
            <CtrlBtn onClick={() => setColOpen((v) => !v)} title={t('table.columns')}>
              <Columns3 size={13} /> {t('table.columns')}
            </CtrlBtn>
            <MenuPortal open={colOpen} anchorRef={colRef} onClose={() => setColOpen(false)} minWidth={230}>
                <MenuHead>
                  <MenuTitle>{t('table.columns')}</MenuTitle>
                  <Spacer />
                  <MiniLink type="button" onClick={() => table.toggleAllColumnsVisible(true)}>{t('table.selectAll', 'All')}</MiniLink>
                  <MiniLink type="button" onClick={() => effectiveOrder.forEach((id) => { const c = table.getColumn(id); if (c && !isInternal(c)) c.toggleVisibility(false) })}>{t('table.selectNone', 'None')}</MiniLink>
                  {/* "Reset" wipes the saved visibility + order from localStorage and re-applies
                      the screen's defaults (the ``hidden`` hints on each column). Needed when a
                      ``visible_when`` rule drops a column based on a server filter but an older
                      saved entry still hides it — the operator would otherwise have to hunt
                      through the column list to un-stick it. */}
                  <MiniLink type="button" onClick={resetGrid} title={t('table.resetColumnsHint', 'Clear the saved column visibility / order for this table — handy when a visible_when rule conflicts with an older saved state.')}>
                    {t('table.resetColumns', 'Reset')}
                  </MiniLink>
                </MenuHead>
                {effectiveOrder.map((colId, idx) => {
                  const col = table.getColumn(colId)
                  if (!col || isInternal(col)) return null
                  return (
                    <ColRow key={colId}>
                      <ArrowBtn onClick={() => moveColumn(idx, 'up')} disabled={idx === 0} title={t('common.up')}><ArrowUp size={10} /></ArrowBtn>
                      <ArrowBtn onClick={() => moveColumn(idx, 'down')} disabled={idx === effectiveOrder.length - 1} title={t('common.down')}><ArrowDown size={10} /></ArrowBtn>
                      <CheckBox $on={col.getIsVisible()} onClick={() => col.toggleVisibility()}>{col.getIsVisible() && <Check size={9} />}</CheckBox>
                      <ColLabel onClick={() => col.toggleVisibility()}>{colHeaderText(col)}</ColLabel>
                    </ColRow>
                  )
                })}
              </MenuPortal>
          </MenuWrap>
          <MenuWrap ref={exportRef}>
            <CtrlBtn onClick={() => setExportOpen((v) => !v)} title={t('table.export')}>
              <Download size={13} /> {t('table.export')} <ChevronDown size={11} />
            </CtrlBtn>
            <MenuPortal open={exportOpen} anchorRef={exportRef} onClose={() => setExportOpen(false)} minWidth={150}>
                <DropdownItem onClick={exportCsv}><FileText size={13} /> CSV (.csv)</DropdownItem>
                <DropdownItem onClick={exportExcel}><TableIcon size={13} /> Excel (.xlsx)</DropdownItem>
              </MenuPortal>
          </MenuWrap>
        </ActionGroup>
      </ToolbarRow>

      <TableScroll ref={tableScrollRef}>
        {/* Stretch-when-narrow, scroll-when-wide. ``minWidth`` = sum of natural column widths
            keeps each column at least content-fit, but the styled ``width: 100%`` lets the
            table fill the scroll container when the total is *less* than the viewport — no
            empty space on the right of narrow results. Wider-than-viewport tables overflow
            past 100% (minWidth wins) and the surrounding TableScroll handles the horizontal
            scrollbar. */}
        <Table style={{ minWidth: table.getTotalSize() }}>
          <thead>
            {showFilters && (
              <tr ref={filterRowRef}>
                {table.getHeaderGroups()[0]?.headers.map((h) => (
                  <FilterTh key={h.id}>
                    <ColumnFilterControl column={h.column} />
                  </FilterTh>
                ))}
              </tr>
            )}
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const canSort = h.column.getCanSort()
                  const sorted = h.column.getIsSorted()
                  const sortIx = h.column.getSortIndex()
                  const internal = isInternal(h.column)
                  const reorderable = !internal && h.column.getCanHide() !== false // a real column
                  const align = colAlign(h.column)
                  return (
                    <Th
                      key={h.id}
                      $sortable={canSort}
                      $dropTarget={reorderable && dragOverId === h.column.id}
                      // only constrain a column that asked for a width (a `width` display hint, or the
                      // narrow internal select/status columns); the rest auto-size to content
                      style={{
                        ...(h.column.columnDef.size != null ? { width: h.getSize(), minWidth: h.column.columnDef.minSize } : null),
                        textAlign: align,
                        // Pin the header directly below the sticky filter row (0 when hidden).
                        // Overlap by 1px: ``offsetHeight`` is integer-rounded, so without it a
                        // sub-pixel seam between the two sticky rows lets scrolling data peek
                        // through. The filter row's higher z-index hides the overlap.
                        top: filterRowH ? filterRowH - 1 : undefined,
                      }}
                      onClick={canSort ? h.column.getToggleSortingHandler() : undefined}
                      draggable={reorderable}
                      onDragStart={(e) => {
                        dragColRef.current = h.column.id
                        e.dataTransfer.effectAllowed = 'move'
                      }}
                      onDragOver={(e) => {
                        if (!reorderable || !dragColRef.current || dragColRef.current === h.column.id) return
                        e.preventDefault()
                        e.dataTransfer.dropEffect = 'move'
                        if (dragOverId !== h.column.id) setDragOverId(h.column.id)
                      }}
                      onDragLeave={() => { if (dragOverId === h.column.id) setDragOverId(null) }}
                      onDrop={(e) => {
                        e.preventDefault()
                        if (reorderable && dragColRef.current) dropColumn(dragColRef.current, h.column.id)
                        dragColRef.current = null
                        setDragOverId(null)
                      }}
                      onDragEnd={() => { dragColRef.current = null; setDragOverId(null) }}
                    >
                      <ThInner $align={align}>
                        {h.column.getIsGrouped() && <GroupBadge title={t('table.grouped', 'grouped')}><Group size={11} /></GroupBadge>}
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {canSort && (
                          <SortMark $active={!!sorted}>
                            {sorted === 'asc' ? <ArrowUp size={11} /> : sorted === 'desc' ? <ArrowDown size={11} /> : <ChevronsUpDown size={11} />}
                            {sorted && sorting.length > 1 && <SortIx>{sortIx + 1}</SortIx>}
                          </SortMark>
                        )}
                      </ThInner>
                    </Th>
                  )
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {visibleRows.length === 0 ? (
              <tr><td colSpan={visibleColCount}><Empty>{t('table.noResults')}</Empty></td></tr>
            ) : (
              <>
                {/* Top spacer — pushes the first visible row down to its virtual `start`
                    offset. Empty `<td colSpan>` keeps the row a valid `<tr>` without
                    contributing to column auto-sizing (no inline content to measure). */}
                {paddingTop > 0 && (
                  <tr aria-hidden="true" style={{ height: paddingTop }}>
                    <td colSpan={visibleColCount} />
                  </tr>
                )}
                {virtualItems.map((virtualItem) => {
                  const row = visibleRows[virtualItem.index]
                  const RowEl = row.getIsGrouped() ? GroupTr : Tr
                  const cls = row.getIsGrouped() ? undefined : rowClassName?.(row.original)
                  // Whole-row click → onRowClick. Bail if the click landed on an interactive child
                  // (input/button/a/select/textarea — the edit-mode cells, copy buttons, group toggles,
                  // etc.) so those keep working without firing the screen dialog underneath. Group
                  // rows never trigger — they're a grouping affordance, not a real record.
                  const clickable = !row.getIsGrouped() && onRowClick != null
                  const onClick = clickable
                    ? (e: React.MouseEvent<HTMLTableRowElement>) => {
                        if ((e.target as HTMLElement).closest('input,button,a,select,textarea,label')) return
                        onRowClick!(row.original)
                      }
                    : undefined
                  // Right-click on a (non-grouped) row → onRowContextMenu(row, event). We
                  // preventDefault so the browser's native menu doesn't fight the consumer's
                  // overlay; the consumer is expected to read `event.clientX`/`clientY` to
                  // position the menu. Headers + pagination keep their native menu (we don't
                  // attach this on <th> or the toolbar).
                  const contextable = !row.getIsGrouped() && onRowContextMenu != null
                  const onContextMenu = contextable
                    ? (e: React.MouseEvent<HTMLTableRowElement>) => {
                        e.preventDefault()
                        onRowContextMenu!(row.original, e)
                      }
                    : undefined
                  return (
                    <RowEl
                      // `data-index` + `ref={rowVirtualizer.measureElement}` let the virtualizer
                      // measure each row's real height after layout — so a group row, an
                      // edit-mode row with taller cell content, or a dialog-changed row that
                      // wraps don't drift the spacer math.
                      key={row.id} className={cls}
                      data-index={virtualItem.index}
                      ref={rowVirtualizer.measureElement}
                      onClick={onClick} onContextMenu={onContextMenu}
                      style={clickable ? { cursor: 'pointer' } : undefined}
                    >
                      {row.getVisibleCells().map((cell) => (
                        <Td
                          key={cell.id}
                          style={{
                            ...(cell.column.columnDef.size != null ? { width: cell.column.getSize(), minWidth: cell.column.columnDef.minSize } : null),
                            paddingLeft: cell.getIsGrouped() ? `${12 + row.depth * 16}px` : undefined,
                            textAlign: colAlign(cell.column),
                          }}
                        >
                          {cell.getIsGrouped() ? (
                            <GroupCellBtn onClick={row.getToggleExpandedHandler()}>
                              {row.getIsExpanded() ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                              {flexRender(cell.column.columnDef.cell, cell.getContext())}
                              <GroupCount>({row.subRows.length})</GroupCount>
                            </GroupCellBtn>
                          ) : cell.getIsAggregated() || cell.getIsPlaceholder() ? null : (
                            flexRender(cell.column.columnDef.cell, cell.getContext())
                          )}
                      </Td>
                    ))}
                  </RowEl>
                  )
                })}
                {/* Bottom spacer — fills the gap between the last visible row's `end` and the
                    virtualizer's total scrollable height. Same shape as the top spacer. */}
                {paddingBottom > 0 && (
                  <tr aria-hidden="true" style={{ height: paddingBottom }}>
                    <td colSpan={visibleColCount} />
                  </tr>
                )}
              </>
            )}
          </tbody>
        </Table>
      </TableScroll>

      <PaginationRow>
        <PagLeft>
          <span>{t('table.rowsPerPage')}</span>
          <PageSizeSelect value={pageSize} onChange={(e) => { table.setPageSize(+e.target.value); table.setPageIndex(0) }}>
            {PAGE_SIZE_OPTIONS.map((n) => (
              // ``PAGE_SIZE_ALL`` is the sentinel for "no pagination — show every loaded row".
              // The virtualizer keeps the DOM small even at this size; the operator gets a
              // single scrollable list instead of paging through 50-row chunks.
              <option key={n} value={n}>{n === PAGE_SIZE_ALL ? t('table.showAll', 'All') : n}</option>
            ))}
          </PageSizeSelect>
          <span>
            {t('table.showing', { from, to, total: totalRows })}
            {grouping.length > 0 ? ` · ${t('table.rowsTotal', { count: filteredLeafCount })}` : ''}
          </span>
        </PagLeft>
        <PagRight>
          <PagBtn onClick={() => table.setPageIndex(0)} disabled={!table.getCanPreviousPage()} title={t('common.first')}><ChevronsLeft size={13} /></PagBtn>
          <PagBtn onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()} title={t('common.prev')}><ChevronLeft size={13} /></PagBtn>
          {pageNumbers(pageIndex + 1, totalPages).map((p, i) =>
            p === '…'
              ? <span key={`e${i}`} style={{ color: colors.text.muted, padding: '0 2px', fontSize: fontSize.sm }}>…</span>
              : <PagBtn key={p} $active={p === pageIndex + 1} onClick={() => table.setPageIndex((p as number) - 1)}>{p}</PagBtn>,
          )}
          <PagBtn onClick={() => table.nextPage()} disabled={!table.getCanNextPage()} title={t('common.next')}><ChevronRight size={13} /></PagBtn>
          <PagBtn onClick={() => table.setPageIndex(totalPages - 1)} disabled={!table.getCanNextPage()} title={t('common.last')}><ChevronsRight size={13} /></PagBtn>
        </PagRight>
      </PaginationRow>
    </Wrap>
  )
}

export default DataTable
