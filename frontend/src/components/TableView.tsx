import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import styled from '@emotion/styled'
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
import { Table as TableIcon, Play, ChevronLeft, ChevronRight, ArrowUp, ArrowDown } from 'lucide-react'
import { api, ApiError } from '../api'
import type { ConnectorMeta, QueryResult, SqlQueryMeta } from '../types'
import { PageLayout, Card, Button, Input, Field, Banner, Centered, Tag, Row, Stack, SpinnerRing } from '../ui'
import { colors, fontSize, fonts, radius } from '../theme'

const PAGE_SIZE = 50

type DataRow = Record<string, unknown>

function cellText(v: unknown): { text: string; isNull: boolean } {
  if (v === null || v === undefined) return { text: 'null', isNull: true }
  if (typeof v === 'object') return { text: JSON.stringify(v), isNull: false }
  return { text: String(v), isNull: false }
}

const Title = styled.span`
  font-family: ${fonts.mono};
`

const Meta = styled.div`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
`

const TableScroll = styled.div`
  overflow: auto;
  max-height: 58vh;
  border: 1px solid ${colors.border};
  border-radius: ${radius.md};
`

const DataTable = styled.table`
  border-collapse: collapse;
  width: 100%;
  font-size: ${fontSize.base};
  font-family: ${fonts.mono};

  th {
    position: sticky;
    top: 0;
    background: ${colors.bg.dropdown};
    color: ${colors.text.secondary};
    text-align: left;
    padding: 6px 10px;
    border-bottom: 1px solid ${colors.border};
    border-right: 1px solid ${colors.border};
    user-select: none;
    white-space: nowrap;
    font-weight: 600;
  }
  th.sortable { cursor: pointer; }
  th.sortable:hover { color: ${colors.text.primary}; }
  th .ix { display: inline-flex; vertical-align: middle; margin-left: 4px; color: ${colors.blue.main}; }
  td {
    padding: 4px 10px;
    border-bottom: 1px solid ${colors.border};
    border-right: 1px solid ${colors.border};
    white-space: nowrap;
    color: ${colors.text.secondary};
  }
  td.null { color: ${colors.text.muted}; font-style: italic; }
  tr:hover td { background: var(--hover-subtle); }
`

function ResultTable({ result }: { result: QueryResult }) {
  const { t } = useTranslation()
  const [sorting, setSorting] = useState<SortingState>([])

  const data = useMemo<DataRow[]>(() => result.rows, [result])
  const colType = useMemo(
    () => Object.fromEntries(result.columns.map((c) => [c.name, c.type] as const)),
    [result],
  )
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

export function TableView() {
  const { t } = useTranslation()
  const { connector = '', query = '' } = useParams()
  const [meta, setMeta] = useState<SqlQueryMeta | null>(null)
  const [metaErr, setMetaErr] = useState<string | null>(null)
  const [params, setParams] = useState<Record<string, string>>({})
  const [result, setResult] = useState<QueryResult | null>(null)
  const [runErr, setRunErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setMeta(null)
    setMetaErr(null)
    setResult(null)
    api
      .get<ConnectorMeta>(`/api/connectors/${encodeURIComponent(connector)}`)
      .then((c) => {
        if (c.type !== 'sql') throw new Error(`${connector} is not a SQL connector`)
        const q = c.queries.find((x) => x.name === query)
        if (!q) throw new Error(`query ${query} not found on ${connector}`)
        setMeta(q)
        const init: Record<string, string> = {}
        for (const p of q.params) if (p.default != null) init[p.name] = p.default
        setParams(init)
      })
      .catch((e) => setMetaErr(e instanceof ApiError ? e.message : String(e)))
  }, [connector, query])

  const paramNames = useMemo(() => {
    if (!meta) return [] as string[]
    const seen = new Set<string>()
    const out: string[] = []
    for (const n of [...meta.bind_params, ...meta.params.map((p) => p.name)]) {
      if (!seen.has(n)) {
        seen.add(n)
        out.push(n)
      }
    }
    return out
  }, [meta])

  const run = useCallback(async () => {
    if (!meta) return
    if (meta.writable && !window.confirm(t('table.runWritableConfirm', { q: `${connector}.${query}` }))) return
    setBusy(true)
    setRunErr(null)
    const sent: Record<string, string> = {}
    for (const [k, v] of Object.entries(params)) if (v !== '') sent[k] = v
    try {
      let res: QueryResult
      if (meta.statement_type === 'SELECT') {
        const qs = new URLSearchParams(sent).toString()
        res = await api.get<QueryResult>(
          `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}${qs ? `?${qs}` : ''}`,
        )
      } else {
        res = await api.post<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}`, {
          params: sent,
        })
      }
      setResult(res)
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [meta, params, connector, query, t])

  if (metaErr)
    return (
      <PageLayout icon={<TableIcon size={18} />} title={`${connector}.${query}`}>
        <Banner $tone="error">{metaErr}</Banner>
      </PageLayout>
    )
  if (!meta)
    return (
      <PageLayout icon={<TableIcon size={18} />} title={`${connector}.${query}`}>
        <Centered />
      </PageLayout>
    )

  return (
    <PageLayout
      icon={<TableIcon size={18} />}
      title={
        <>
          <Title>
            {connector}.{query}
          </Title>
          <Tag $tone="blue">{meta.statement_type}</Tag>
          {meta.writable && <Tag $tone="orange">{t('table.writable')}</Tag>}
        </>
      }
      description={meta.label || meta.description || undefined}
    >
      <Stack gap={14}>
        <Card>
          <Row align="flex-end">
            {paramNames.map((name) => {
              const def = meta.params.find((p) => p.name === name)
              return (
                <div key={name} style={{ minWidth: 160 }}>
                  <Field label={def?.label ?? name}>
                    <Input
                      type="text"
                      placeholder={def?.default != null ? t('table.defaultPrefix', { v: def.default }) : t('table.nullIfBlank')}
                      value={params[name] ?? ''}
                      onChange={(e) => setParams((p) => ({ ...p, [name]: e.target.value }))}
                    />
                  </Field>
                </div>
              )
            })}
            <Button $variant="primary" onClick={run} disabled={busy}>
              {busy ? <SpinnerRing size={14} thickness={2} /> : <Play size={14} />}
              {busy ? t('common.running') : t('common.run')}
            </Button>
          </Row>
          {runErr && (
            <div style={{ marginTop: 12 }}>
              <Banner $tone="error">{runErr}</Banner>
            </div>
          )}
        </Card>

        {result && result.statement_type === 'SELECT' && (
          <Stack gap={10}>
            <Meta>
              {t(result.row_count === 1 ? 'table.rows_one' : 'table.rows_other', { count: result.row_count })} ·{' '}
              {result.duration_ms.toFixed(1)} {t('common.ms')}
              {result.truncated && (
                <span style={{ color: colors.red.main }}> · {t('table.truncatedTo', { n: result.rows.length })}</span>
              )}
            </Meta>
            {result.columns.length === 0 ? (
              <Meta>{t('table.noColumns')}</Meta>
            ) : (
              <ResultTable key={result.columns.map((c) => c.name).join('|')} result={result} />
            )}
          </Stack>
        )}

        {result && result.statement_type !== 'SELECT' && (
          <Banner $tone="ok">
            {t('table.ok', { stmt: result.statement_type })} —{' '}
            {t(result.rowcount === 1 ? 'table.affected_one' : 'table.affected_other', { count: result.rowcount })} ·{' '}
            {result.duration_ms.toFixed(1)} {t('common.ms')}
          </Banner>
        )}
      </Stack>
    </PageLayout>
  )
}
