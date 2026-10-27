// The result as a table: aggregate rows (dimension cells rendered through their enum / lookup
// rule, measures as numbers) sorted on the loaded rows by clicking a header; or detail rows with
// server paging. A click on an aggregate row drills to its detail rows (the parent decides).
import { useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '../../common'
import { ruleCell } from '../../services/cells'
import { useLookupBatch, type LookupSpec } from '../../services/lookups'
import type { Column } from '../../types/connectors'
import type { ResultColumn } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'

const Wrap = styled.div`flex: 1; min-height: 0; overflow: auto; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card};`
const Table = styled.table`
  border-collapse: separate; border-spacing: 0; width: 100%; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & th { position: sticky; top: 0; z-index: 1; background: ${colors.bg.card}; text-align: left; padding: 8px 10px; border-bottom: 1px solid ${colors.border};
         font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em; white-space: nowrap; cursor: pointer; user-select: none; }
  & th.num, & td.num { text-align: right; font-variant-numeric: tabular-nums; }
  & td { padding: 6px 10px; border-bottom: 1px solid ${colors.border}; color: ${colors.text.primary}; white-space: nowrap; }
  & td.null { color: ${colors.text.muted}; font-style: italic; }
  & tbody tr:hover { background: var(--hover-subtle); }
  & tbody tr.click { cursor: pointer; }
`
const Footer = styled.div`display: flex; align-items: center; gap: 8px; padding: 6px 0 0; font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`

export function ResultTable({ columns, rows, lang, onRowClick, paging }: {
  columns: ResultColumn[]
  rows: Record<string, unknown>[]
  lang: string
  onRowClick?: (row: Record<string, unknown>) => void
  paging?: { page: number; pageSize: number; total: number; onPage: (p: number) => void }
}) {
  const { t } = useTranslation()
  const [sort, setSort] = useState<{ key: string; dir: 'asc' | 'desc' } | null>(null)
  const nf = useMemo(() => new Intl.NumberFormat(lang, { maximumFractionDigits: 2 }), [lang])
  const specs = useMemo<LookupSpec[]>(() => columns.flatMap((c) => (c.rule?.kind === 'lookup'
    ? [{ connector: c.rule.connector, query: c.rule.query, value: c.rule.value, label: c.rule.label, params: c.rule.params, sources: c.rule.sources }] : [])), [columns])
  const lookups = useLookupBatch(specs)
  const lookupFor = (c: ResultColumn): Map<string, string> | undefined => {
    const rule = c.rule
    if (rule?.kind !== 'lookup') return undefined
    const i = specs.findIndex((s) => s.query === rule.query && s.connector === rule.connector)
    return i >= 0 ? [...lookups.values()][i] : undefined
  }
  const sorted = useMemo(() => {
    if (!sort || paging) return rows
    const k = sort.key
    const nulls = rows.filter((r) => r[k] == null)
    const rest = rows.filter((r) => r[k] != null).sort((a, b) => {
      const x = a[k] as string | number, y = b[k] as string | number
      return (x < y ? -1 : x > y ? 1 : 0) * (sort.dir === 'desc' ? -1 : 1)
    })
    return [...rest, ...nulls]
  }, [rows, sort, paging])

  const isNum = (c: ResultColumn) => c.kind === 'measure' || c.type === 'number'
  const cell = (c: ResultColumn, v: unknown) => {
    if (v === null || v === undefined) return <td key={c.key} className="null">—</td>
    if (isNum(c) && typeof v === 'number') return <td key={c.key} className="num">{nf.format(v)}</td>
    if (c.rule && !c.bucket) {
      const col: Column = { name: c.key, type: null, rule: c.rule }
      const r = ruleCell(v, col, undefined, lookupFor(c))
      return <td key={c.key} title={String(v)}>{r.text}</td>
    }
    return <td key={c.key}>{String(v)}</td>
  }
  const pages = paging ? Math.max(1, Math.ceil(paging.total / paging.pageSize)) : 1

  return (
    <>
      <Wrap>
        <Table>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} className={isNum(c) ? 'num' : undefined}
                  onClick={() => !paging && setSort((s) => (s?.key === c.key ? (s.dir === 'asc' ? { key: c.key, dir: 'desc' } : null) : { key: c.key, dir: 'asc' }))}
                  title={c.key}>
                  {c.kind === 'measure' ? (c.field ? t(`explorer.aggLabel.${c.aggregate}`, { label: c.label }) : t('explorer.rowCount')) : c.label}
                  {c.bucket ? ` · ${t(`explorer.bucket.${c.bucket}`)}` : ''}
                  {sort?.key === c.key ? (sort.dir === 'asc' ? <ArrowUp size={10} /> : <ArrowDown size={10} />) : null}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i} className={onRowClick ? 'click' : undefined} onClick={onRowClick ? () => onRowClick(r) : undefined}>
                {columns.map((c) => cell(c, r[c.key]))}
              </tr>
            ))}
          </tbody>
        </Table>
      </Wrap>
      {paging && (
        <Footer>
          <Button $size="sm" $variant="ghost" disabled={paging.page <= 1} onClick={() => paging.onPage(paging.page - 1)}><ChevronLeft size={13} /></Button>
          <span>{t('explorer.pageOf', { page: paging.page, pages, total: paging.total })}</span>
          <Button $size="sm" $variant="ghost" disabled={paging.page >= pages} onClick={() => paging.onPage(paging.page + 1)}><ChevronRight size={13} /></Button>
        </Footer>
      )}
    </>
  )
}
