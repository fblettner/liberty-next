// Run a connector's named SQL query: a param form built from the query's
// params/bind_params, then SELECT → a sortable/paged grid (ResultTable) or a
// writable statement → confirm + affected-rows banner. Rendered inside a tab
// (see components/TabHost) — `connector`/`query` come in as props, not route params.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Table as TableIcon, Play } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import type { ConnectorMeta, QueryResult, SqlQueryMeta } from '../../types/connectors'
import { PageLayout, Card, Button, Input, Field, Banner, Centered, Tag, Mono, Row, Stack, SpinnerRing } from '../../common'
import { colors } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { findMenuLabel } from '../../services/menuLabels'
import { Meta } from './styled'
import { ResultTable } from './ResultTable'
import { FilterPanel, type ServerFilter } from './FilterPanel'

const Sub = styled.span`
  display: inline-flex; align-items: center; gap: 8px;
`

export default function TableView({ connector, query }: { connector: string; query: string }) {
  const { t } = useTranslation()
  const { menus } = useWorkspace()
  const [meta, setMeta] = useState<SqlQueryMeta | null>(null)
  const [metaErr, setMetaErr] = useState<string | null>(null)
  const [params, setParams] = useState<Record<string, string>>({})
  const [filters, setFilters] = useState<Record<string, ServerFilter>>({})  // server-filter fields (v1's col_filter columns)
  const [maxRows, setMaxRows] = useState('')  // override the configured row cap for this run (DbVisualizer-style); blank = use the default
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
        setFilters({})
      })
      .catch((e) => setMetaErr(e instanceof ApiError ? e.message : String(e)))
  }, [connector, query])

  // Server-filter fields: result columns flagged `filter` in the query's `columns` config (v1's
  // col_filter). The migrated SQL is wrapped in `SELECT * FROM (…) _flt WHERE …` with a `:<col>` +
  // optional `:<col>_op` bind per such column, so the value here actually pre-filters server-side.
  const filterCols = useMemo(() => (meta?.columns ?? []).filter((c) => c.filter), [meta])
  const filterBindNames = useMemo(() => {
    const s = new Set<string>()
    for (const c of filterCols) { s.add(c.name); s.add(`${c.name}_op`) }
    return s
  }, [filterCols])

  const paramNames = useMemo(() => {
    if (!meta) return [] as string[]
    const seen = new Set<string>()
    const out: string[] = []
    for (const n of [...meta.bind_params, ...meta.params.map((p) => p.name)]) {
      if (!seen.has(n) && !filterBindNames.has(n)) {  // filter columns get their own panel, not the param form
        seen.add(n)
        out.push(n)
      }
    }
    return out
  }, [meta, filterBindNames])

  const run = useCallback(async () => {
    if (!meta) return
    if (meta.writable && !window.confirm(t('table.runWritableConfirm', { q: `${connector}.${query}` }))) return
    setBusy(true)
    setRunErr(null)
    const sent: Record<string, string> = {}
    for (const [k, v] of Object.entries(params)) if (v !== '') sent[k] = v
    for (const [name, f] of Object.entries(filters)) if (f.val !== '') { sent[name] = f.val; sent[`${name}_op`] = f.op }
    const limit = maxRows.trim()  // `_limit=N` overrides the connector/pool/query row cap for this run
    try {
      let res: QueryResult
      if (meta.statement_type === 'SELECT') {
        const qs = new URLSearchParams(limit ? { ...sent, _limit: limit } : sent).toString()
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
  }, [meta, params, filters, maxRows, connector, query, t])

  // Auto-load: run a SELECT immediately when the screen opens, once, if the query asks for it.
  const autoRan = useRef(false)
  useEffect(() => { autoRan.current = false }, [connector, query])
  useEffect(() => {
    if (meta?.auto_load && meta.statement_type === 'SELECT' && !autoRan.current && !result && !busy) {
      autoRan.current = true
      run()
    }
  }, [meta, result, busy, run])

  // Title = the screen's description (v1 ly_tables.tbl_label) — the menu label is already shown
  // on the tab; fall back to the menu label, then the technical name.
  const menuLabel = findMenuLabel(menus, { kind: 'sql', connector, target: query })
  const friendlyName = meta?.description || meta?.label || menuLabel || `${connector}.${query}`

  if (metaErr)
    return (
      <PageLayout icon={<TableIcon size={18} />} title={friendlyName}>
        <Banner $tone="error">{metaErr}</Banner>
      </PageLayout>
    )
  if (!meta)
    return (
      <PageLayout icon={<TableIcon size={18} />} title={friendlyName}>
        <Centered />
      </PageLayout>
    )

  return (
    <PageLayout
      icon={<TableIcon size={18} />}
      title={
        <>
          {friendlyName}
          <Tag $tone="blue">{meta.statement_type}</Tag>
          {meta.writable && <Tag $tone="orange">{t('table.writable')}</Tag>}
        </>
      }
      description={
        <Sub>
          <Mono>{connector}.{query}</Mono>
          {menuLabel && menuLabel !== friendlyName ? <span>· {menuLabel}</span> : null}
        </Sub>
      }
    >
      <Stack gap={14}>
        <Card>
          <Stack gap={12}>
            <FilterPanel
              cols={filterCols}
              values={filters}
              onChange={(name, next) => setFilters((f) => ({ ...f, [name]: next }))}
              onClearAll={() => setFilters({})}
              autoLoad={meta.auto_load}
            />
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
              {meta.statement_type === 'SELECT' && (
                <div style={{ width: 110 }}>
                  {/* override the configured row cap for this run (DbVisualizer-style) — blank = use the default */}
                  <Field label={t('table.maxRows')}>
                    <Input
                      type="number"
                      min={1}
                      placeholder={t('table.maxRowsHint')}
                      value={maxRows}
                      onChange={(e) => setMaxRows(e.target.value)}
                    />
                  </Field>
                </div>
              )}
              <Button $variant="primary" onClick={run} disabled={busy}>
                {busy ? <SpinnerRing size={14} thickness={2} /> : <Play size={14} />}
                {busy ? t('common.running') : t('common.run')}
              </Button>
            </Row>
          </Stack>
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
              <ResultTable
                key={result.columns.map((c) => c.name).join('|')}
                result={result}
                connector={connector}
                query={query}
                updateQuery={meta.update_query}
                insertQuery={meta.insert_query}
                deleteQuery={meta.delete_query}
                keyColumns={meta.key_columns}
                onSaved={run}
              />
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
