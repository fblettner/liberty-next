// The result as a table, on the shared grid (search, filters, sort, grouping, column chooser,
// export): aggregate rows — dimension cells through their enum / lookup rule, measures as numbers —
// or detail rows paged by the server. A click on a row is the parent's (drill, or the record).
import { useMemo } from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { DataTable } from '../../common/DataTable'
import type { FilterKind, FilterMeta } from '../../common/DataTableFilter'
import type { ShowModes } from '../../services/explorer'
import type { CodeShow, ResultColumn } from '../../types/explorer'
import { filterPropsFor } from '../TableView/resultColumns'
import { CellSpan } from '../TableView/styled'
import { isCoded, ruleOf, showOf, useDimText } from './useDimText'

type Row = Record<string, unknown>

const isNum = (c: ResultColumn) => c.kind === 'measure' || c.type === 'number'
/** The column's code beside its heading, when the heading is a label and not the code itself. */
const codeOf = (c: ResultColumn): string | undefined => {
  if (!c.field || c.entity === 'calc') return undefined
  const code = c.field.split('.').pop()
  return code && code !== c.label ? code : undefined
}

export function ResultTable({ columns, rows, lang, onRowClick, paging, exportName, bare, shows }: {
  columns: ResultColumn[]
  rows: Row[]
  lang: string
  onRowClick?: (row: Row) => void
  paging?: { page: number; pageSize: number; total: number; onPage: (p: number) => void }
  /** the name of the file the grid's own export writes */
  exportName?: string
  /** no toolbar: a table inside a dashboard tile */
  bare?: boolean
  /** a coded column shown as its label (the default), its code, or both — the choice on the field's chip */
  shows?: ShowModes
}) {
  const { t } = useTranslation()
  const nf = useMemo(() => new Intl.NumberFormat(lang, { maximumFractionDigits: 2 }), [lang])
  // a column with dictionary decimals (a JDE amount) shows exactly that many places
  const nfs = useMemo(() => new Map(columns.filter((c) => c.decimals != null).map((c) => [c.key, new Intl.NumberFormat(lang, { minimumFractionDigits: c.decimals, maximumFractionDigits: c.decimals })])), [columns, lang])
  const dimText = useDimText(columns, shows)

  const defs = useMemo<ColumnDef<Row, unknown>[]>(() => columns.flatMap((c) => {
    // an enum / lookup column sorts, filters, searches and exports on what it shows (label, code or both);
    // it is a code even when its values are numbers (a company number): no thousands separator, not a figure
    const coded = isCoded(c)
    const num = isNum(c) && !coded
    const align: FilterMeta['align'] = num ? 'right' : undefined
    const rule = ruleOf(c)
    const kind: FilterKind = num ? 'number' : !coded && !c.bucket && (c.type === 'date' || c.type === 'datetime') ? 'date' : 'text'
    const fp = filterPropsFor(kind, undefined, align)
    const head = (c.kind === 'measure' ? (c.field ? t(`explorer.aggLabel.${c.aggregate}`, { label: c.label }) : t('explorer.rowCount')) : c.label)
      + (c.bucket ? ` · ${t(`explorer.bucket.${c.bucket}`)}` : '')
    const fmt = nfs.get(c.key) ?? nf
    // one grid column reading the result column *as* a code, a label, or as the field's own choice
    const column = (id: string, header: string, as: CodeShow | undefined, note: string | undefined): ColumnDef<Row, unknown> => ({
      id,
      header,
      accessorFn: (row) => { const v = row[c.key]; return coded && v !== null && v !== undefined ? dimText(c, v, as).text : v },
      filterFn: fp.filterFn,
      meta: { ...fp.meta, headerNote: note } satisfies FilterMeta,
      cell: (info) => {
        // a group's heading cell carries the grouped value (already what is shown), a row its raw value
        const grouped = info.cell.getIsGrouped()
        const v = grouped ? info.getValue() : (info.row.original as Row)[c.key]
        if (v === null || v === undefined) return <CellSpan className="null">—</CellSpan>
        if (num && typeof v === 'number') return <CellSpan style={{ textAlign: 'right' }}>{fmt.format(v)}</CellSpan>
        if (rule && !grouped) {
          const r = dimText(c, v, as)
          return <CellSpan className={r.kind === 'plain' ? undefined : r.kind} title={String(v)}>{r.text}</CellSpan>
        }
        return <CellSpan>{String(v)}</CellSpan>
      },
    })
    // "code + label": two columns, like a data screen's — the code (ID), then the label
    if (coded && showOf(c, shows) === 'both') return [column(c.key, `${head} ${t('table.idColumnSuffix')}`, 'code', codeOf(c)), column(`${c.key}__label`, head, 'label', undefined)]
    return [column(c.key, head, undefined, codeOf(c))]
  }), [columns, dimText, shows, nf, nfs, t])

  // another set of columns is another table: its sort, filters and grouping start again
  const shape = defs.map((d) => d.id).join('|')
  return (
    <DataTable<Row> key={shape} columns={defs} data={rows} onRowClick={onRowClick} serverPaging={paging}
      exportFilename={exportName ?? 'explorer'} chromeless={bare} />
  )
}
