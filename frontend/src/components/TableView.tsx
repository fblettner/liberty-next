import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Table as TableIcon, Play, ChevronLeft, ChevronRight, ArrowUp, ArrowDown } from 'lucide-react'
import { api, ApiError } from '../api'
import type { ConnectorMeta, QueryResult, SqlQueryMeta } from '../types'
import { PageLayout, Card, Button, Input, Field, Banner, Centered, Tag, Row, Stack, SpinnerRing } from '../ui'
import { colors, fontSize, fonts, radius } from '../theme'

const PAGE_SIZE = 50

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
    cursor: pointer;
    user-select: none;
    white-space: nowrap;
    font-weight: 600;
    &:hover { color: ${colors.text.primary}; }
  }
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

export function TableView() {
  const { t } = useTranslation()
  const { connector = '', query = '' } = useParams()
  const [meta, setMeta] = useState<SqlQueryMeta | null>(null)
  const [metaErr, setMetaErr] = useState<string | null>(null)
  const [params, setParams] = useState<Record<string, string>>({})
  const [result, setResult] = useState<QueryResult | null>(null)
  const [runErr, setRunErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [sort, setSort] = useState<{ col: string; dir: 1 | -1 } | null>(null)
  const [page, setPage] = useState(0)

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
    setSort(null)
    setPage(0)
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

  const sortedRows = useMemo(() => {
    if (!result) return []
    if (!sort) return result.rows
    const { col, dir } = sort
    return [...result.rows].sort((a, b) => {
      const av = a[col],
        bv = b[col]
      if (av === bv) return 0
      if (av == null) return 1
      if (bv == null) return -1
      return (av < bv ? -1 : 1) * dir
    })
  }, [result, sort])

  const pageRows = sortedRows.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE)
  const pages = result ? Math.max(1, Math.ceil(result.rows.length / PAGE_SIZE)) : 1

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
              <>
                <TableScroll>
                  <DataTable>
                    <thead>
                      <tr>
                        {result.columns.map((c) => (
                          <th
                            key={c.name}
                            title={c.type ?? undefined}
                            onClick={() =>
                              setSort((s) =>
                                s && s.col === c.name
                                  ? { col: c.name, dir: (s.dir * -1) as 1 | -1 }
                                  : { col: c.name, dir: 1 },
                              )
                            }
                          >
                            {c.name}
                            {sort?.col === c.name && (
                              <span className="ix">{sort.dir === 1 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}</span>
                            )}
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {pageRows.map((row, i) => (
                        <tr key={i}>
                          {result.columns.map((c) => {
                            const { text, isNull } = cellText(row[c.name])
                            return (
                              <td key={c.name} className={isNull ? 'null' : undefined}>
                                {text}
                              </td>
                            )
                          })}
                        </tr>
                      ))}
                    </tbody>
                  </DataTable>
                </TableScroll>
                {pages > 1 && (
                  <Row>
                    <Button $size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
                      <ChevronLeft size={13} /> {t('common.prev')}
                    </Button>
                    <Meta>
                      {t('common.page')} {page + 1} {t('common.of')} {pages}
                    </Meta>
                    <Button $size="sm" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>
                      {t('common.next')} <ChevronRight size={13} />
                    </Button>
                  </Row>
                )}
              </>
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
