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

const Sub = styled.span`
  display: inline-flex; align-items: center; gap: 8px;
`

export default function TableView({ connector, query }: { connector: string; query: string }) {
  const { t } = useTranslation()
  const { menus } = useWorkspace()
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

  // Auto-load: run a SELECT immediately when the screen opens, once, if the query asks for it.
  const autoRan = useRef(false)
  useEffect(() => { autoRan.current = false }, [connector, query])
  useEffect(() => {
    if (meta?.auto_load && meta.statement_type === 'SELECT' && !autoRan.current && !result && !busy) {
      autoRan.current = true
      run()
    }
  }, [meta, result, busy, run])

  const menuLabel = findMenuLabel(menus, { kind: 'sql', connector, target: query })
  const friendlyName = menuLabel || meta?.label || meta?.description || `${connector}.${query}`

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
          {meta.description && meta.description !== friendlyName ? <span>· {meta.description}</span> : null}
        </Sub>
      }
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
              <ResultTable
                key={result.columns.map((c) => c.name).join('|')}
                result={result}
                connector={connector}
                query={query}
                updateQuery={meta.update_query}
                insertQuery={meta.insert_query}
                deleteQuery={meta.delete_query}
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
