// The pivot: the row dimensions as a collapsible hierarchy (subtotals on group rows for an
// additive measure), one dimension spread as columns, a share of the parent or of the total
// beside each value, Excel export. A click on a leaf drills to its rows (the parent decides).
import { useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, ChevronsDownUp, ChevronsUpDown, FileSpreadsheet } from 'lucide-react'
import * as XLSX from 'xlsx'
import { Button } from '../../common'
import { buildPivot, flattenPivot, isAdditive, pivotToSheet, share, type PivotNode } from '../../services/explorerPivot'
import type { AggregateResult, ResultColumn } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'

const Wrap = styled.div`flex: 1; min-height: 0; overflow: auto; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card};`
const Table = styled.table`
  border-collapse: separate; border-spacing: 0; width: 100%; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & th { position: sticky; top: 0; z-index: 1; background: ${colors.bg.card}; text-align: right; padding: 8px 10px; border-bottom: 1px solid ${colors.border};
         font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; }
  & th.lbl { text-align: left; }
  & td { padding: 5px 10px; border-bottom: 1px solid ${colors.border}; color: ${colors.text.primary}; white-space: nowrap; text-align: right; font-variant-numeric: tabular-nums; }
  & td.lbl { text-align: left; }
  & tr.group td { font-weight: 600; background: var(--hover-subtle); }
  & tr.total td { font-weight: 700; border-top: 2px solid ${colors.border}; }
  & tr.leaf { cursor: pointer; } & tr.leaf:hover td { background: var(--hover-subtle); }
  & .share { color: ${colors.text.muted}; font-size: ${fontSize.micro}; margin-left: 6px; }
  & .null { color: ${colors.text.muted}; }
`
const Toggle = styled.button`border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; vertical-align: middle; padding: 0 4px 0 0;`
const Bar = styled.div`display: flex; align-items: center; gap: 6px; justify-content: flex-end;`

export function PivotView({ result, measureKey, pivotKey, percent, lang, onDrill }: {
  result: AggregateResult
  measureKey: string | null
  pivotKey: string | null
  percent: 'none' | 'parent' | 'total'
  lang: string
  onDrill?: (values: Record<string, unknown>) => void
}) {
  const { t } = useTranslation()
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const dims = result.columns.filter((c) => c.kind === 'dimension')
  const measures = result.columns.filter((c) => c.kind === 'measure')
  const measure = measures.find((m) => m.key === measureKey) ?? measures[0]
  const pivotCol = pivotKey ? dims.find((d) => d.key === pivotKey) ?? null : dims.length >= 2 ? dims[dims.length - 1] : null
  const rowDims = dims.filter((d) => d.key !== pivotCol?.key)
  const additive = isAdditive(measure?.aggregate)
  const pivot = useMemo(() => (measure ? buildPivot(result.rows, rowDims.map((d) => d.key), pivotCol?.key ?? null, measure.key, additive) : null),
    [result.rows, rowDims, pivotCol, measure, additive])
  const nf = useMemo(() => new Intl.NumberFormat(lang, { maximumFractionDigits: 2 }), [lang])
  const pf = useMemo(() => new Intl.NumberFormat(lang, { style: 'percent', maximumFractionDigits: 1 }), [lang])
  if (!measure || !pivot) return null
  const flat = flattenPivot(pivot.root, collapsed)
  const cols = pivot.columns
  const multi = cols.length > 1 || !!pivotCol
  const label = (_c: ResultColumn | undefined, v: unknown) => (v === null || v === undefined ? '—' : String(v))
  const allIds = () => flattenPivot(pivot.root, new Set()).filter((x) => x.node.children.length > 0).map((x) => x.id)

  const cell = (v: number | null, parent: number | null, total: number | null, bold = false) => {
    if (v === null) return <span className="null">—</span>
    const s = percent === 'none' ? null : share(v, parent, total, percent)
    return <><span style={bold ? { fontWeight: 700 } : undefined}>{nf.format(v)}</span>{s !== null && <span className="share">{pf.format(s)}</span>}</>
  }
  const exportXlsx = () => {
    const sheet = pivotToSheet(pivot, rowDims.map((d) => d.label), t('explorer.total'))
    const ws = XLSX.utils.aoa_to_sheet(sheet)
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, 'Pivot')
    XLSX.writeFile(wb, 'explorer-pivot.xlsx')
  }
  const drill = (n: PivotNode, col: string | null) => {
    if (!onDrill) return
    const values: Record<string, unknown> = {}
    rowDims.forEach((d, i) => { if (i < n.path.length) values[d.key] = n.path[i] })
    if (pivotCol && col !== null) values[pivotCol.key] = col === '' ? null : col
    onDrill(values)
  }

  return (
    <>
      <Bar>
        <Button $size="sm" $variant="ghost" onClick={() => setCollapsed(new Set())} title={t('explorer.expandAll')}><ChevronsUpDown size={13} /></Button>
        <Button $size="sm" $variant="ghost" onClick={() => setCollapsed(new Set(allIds()))} title={t('explorer.collapseAll')}><ChevronsDownUp size={13} /></Button>
        <Button $size="sm" $variant="ghost" onClick={exportXlsx} title={t('explorer.exportExcel')}><FileSpreadsheet size={13} /></Button>
      </Bar>
      <Wrap>
        <Table>
          <thead>
            <tr>
              <th className="lbl">{rowDims.map((d) => d.label).join(' › ') || measure.label}</th>
              {multi && cols.map((c) => <th key={c}>{label(pivotCol ?? undefined, c === '' ? null : c)}</th>)}
              <th>{multi ? t('explorer.total') : t(`explorer.aggLabel.${measure.aggregate}`, { label: measure.label })}</th>
            </tr>
          </thead>
          <tbody>
            {flat.map(({ node, parent, id }) => {
              const group = node.children.length > 0
              const leaf = !group
              return (
                <tr key={id} className={group ? 'group' : 'leaf'} onClick={leaf ? () => drill(node, null) : undefined}>
                  <td className="lbl" style={{ paddingLeft: 10 + (node.level - 1) * 18 }}>
                    {group && (
                      <Toggle type="button" onClick={(e) => { e.stopPropagation(); setCollapsed((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n }) }}>
                        {collapsed.has(id) ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
                      </Toggle>
                    )}
                    {label(rowDims[node.level - 1], node.value)}
                  </td>
                  {multi && cols.map((c) => (
                    <td key={c} onClick={leaf ? (e) => { e.stopPropagation(); drill(node, c) } : undefined}>
                      {(group && !additive) ? <span className="null" /> : cell(node.cells[c] ?? null, parent.cells[c] ?? null, pivot.root.cells[c] ?? null)}
                    </td>
                  ))}
                  <td>{(group && !additive) ? <span className="null" /> : cell(node.total, parent.total, pivot.root.total, group)}</td>
                </tr>
              )
            })}
            {additive && (
              <tr className="total">
                <td className="lbl">{t('explorer.total')}</td>
                {multi && cols.map((c) => <td key={c}>{cell(pivot.root.cells[c] ?? null, null, pivot.root.total)}</td>)}
                <td>{cell(pivot.root.total, null, pivot.root.total)}</td>
              </tr>
            )}
          </tbody>
        </Table>
      </Wrap>
    </>
  )
}
