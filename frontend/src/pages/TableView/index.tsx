// Run a connector's named SQL query: a param form built from the query's
// params/bind_params, then SELECT → a sortable/paged grid (ResultTable) or a
// writable statement → confirm + affected-rows banner. Rendered inside a tab
// (see components/TabHost) — `connector`/`query` come in as props, not route params.
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useSearchParams } from 'react-router-dom'
import { Table as TableIcon, Play, BarChart3 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import type { ConnectorMeta, QueryResult, SqlQueryMeta } from '../../types/connectors'
import { PageLayout, Input, Field, Banner, Centered, Tag, Mono, Row, Stack, SpinnerRing } from '../../common'
import { colors, radius, fontSize, fonts } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { findMenuLabel } from '../../services/menuLabels'
import type { ScreenDetail } from '../../types/screens'
import { Meta } from './styled'
import { ResultTable } from './ResultTable'
import { FilterPanel, type ServerFilter } from './FilterPanel'
// ChartView pulls in Recharts (~75 kB gz) — split into its own chunk so the table view's
// initial load stays tight. The chunk only fetches when the operator toggles to Chart mode.
const ChartView = lazy(() => import('./ChartView').then((m) => ({ default: m.ChartView })))

// Run / Max-rows controls — sized to sit inline with the grid's 28px-tall toolbar buttons
// (Run goes just right of the search box, Max-rows at the far right before the Filters button).
const RunBtn = styled.button`
  display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 14px; flex-shrink: 0;
  border: 1px solid ${colors.blue.border}; border-radius: ${radius.md}; background: ${colors.blue.bg};
  color: ${colors.blue.main}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600; cursor: pointer;
  white-space: nowrap; transition: background 0.15s, color 0.15s, border-color 0.15s;
  &:hover:not(:disabled) { background: var(--hover-subtle); color: ${colors.text.primary}; }
  &:disabled { opacity: 0.5; cursor: default; }
`
const MaxRowsBox = styled.label`
  display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 10px; flex-shrink: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; white-space: nowrap;
  & input {
    width: 60px; border: none; background: transparent; outline: none; text-align: right;
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
    &::placeholder { color: ${colors.text.muted}; text-align: left; }
    &::-webkit-outer-spin-button, &::-webkit-inner-spin-button { -webkit-appearance: none; margin: 0; }
  }
  &:focus-within { border-color: ${colors.blue.border}; }
`
// A two-state segmented control: [Table | Chart]. Sits at the right of the Meta line above the
// data area; the active half is highlighted. Always paired with a SELECT result — meaningless
// for writes, so it's hidden in those cases by render condition (not by disabling).
const ViewToggle = styled.div`
  display: inline-flex; height: 24px; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  overflow: hidden; flex-shrink: 0;
  & button {
    display: inline-flex; align-items: center; gap: 4px; padding: 0 9px; border: none;
    background: transparent; color: ${colors.text.muted}; cursor: pointer; font-size: ${fontSize.micro};
    & + button { border-left: 1px solid ${colors.border}; }
    &:hover { color: ${colors.text.primary}; }
    &[aria-pressed='true'] { color: ${colors.blue.main}; background: ${colors.blue.bg}; }
  }
`

export default function TableView({ connector, query }: { connector: string; query: string }) {
  const { t } = useTranslation()
  const { menus, findScreen } = useWorkspace()
  // URL search params seed the param form / filter panel on initial load — that's how the
  // row-menu's NavigateAction drill-down works ("?USR_ID=42" → the destination's USR_ID param
  // is pre-filled, then auto_load fires the query against that value). Also makes screens
  // deep-linkable / shareable. Only consumed once per (connector, query) mount; subsequent
  // user edits to the param form are not reflected back into the URL (the form is the source
  // of truth after that point).
  const [searchParams] = useSearchParams()
  const [meta, setMeta] = useState<SqlQueryMeta | null>(null)
  const [metaErr, setMetaErr] = useState<string | null>(null)
  const [params, setParams] = useState<Record<string, string>>({})
  const [filters, setFilters] = useState<Record<string, ServerFilter>>({})  // server-filter fields (v1's col_filter columns)
  const [maxRows, setMaxRows] = useState('')  // override the configured row cap for this run (DbVisualizer-style); blank = use the default
  const [result, setResult] = useState<QueryResult | null>(null)
  const [runErr, setRunErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Full screen detail — fetched lazily when the workspace flags a screen for this (connector,
  // query). Carries the dialog body the ScreenDialog renders; null means no screen / no dialog,
  // and ResultTable falls back to its inline grid editor.
  const [screen, setScreen] = useState<ScreenDetail | null>(null)
  // Table / Chart toggle (Phase 8 slice 1). The Chart side is lazy-imported (Recharts) so the
  // chunk only loads when the operator toggles it. The toggle resets to 'table' each remount —
  // chart-mode-as-default would surprise an operator opening a fresh tab.
  const [view, setView] = useState<'table' | 'chart'>('table')

  useEffect(() => {
    setMeta(null)
    setMetaErr(null)
    setResult(null)
    setScreen(null)
    api
      .get<ConnectorMeta>(`/api/connectors/${encodeURIComponent(connector)}`)
      .then((c) => {
        if (c.type !== 'sql') throw new Error(`${connector} is not a SQL connector`)
        const q = c.queries.find((x) => x.name === query)
        if (!q) throw new Error(`query ${query} not found on ${connector}`)
        setMeta(q)
        // Seed the param form: declared `default`s first, then overlay any matching URL search
        // params (NavigateAction's drill-down vehicle). Filter-flagged columns also flow into
        // the FilterPanel below — keyed by column name on the result, set both maps so whichever
        // gate the destination uses picks the value up.
        const init: Record<string, string> = {}
        const filterInit: Record<string, ServerFilter> = {}
        for (const p of q.params) if (p.default != null) init[p.name] = p.default
        const declared = new Set([...q.params.map((p) => p.name), ...q.bind_params])
        const filterCols = new Set((q.columns ?? []).filter((c) => c.filter).map((c) => c.name))
        searchParams.forEach((v, k) => {
          if (declared.has(k)) init[k] = v
          // The FilterPanel uses `equals` as the natural op for choice-like (ENUM/LOOKUP) columns
          // and the migrated SQL wrap accepts it; that matches NavigateAction's "filter by this value"
          // intent. Operators wiring a fancier op via URL params would need a separate convention.
          if (filterCols.has(k)) filterInit[k] = { val: v, op: 'equals' }
        })
        setParams(init)
        setFilters(filterInit)
      })
      .catch((e) => setMetaErr(e instanceof ApiError ? e.message : String(e)))
    // searchParams intentionally NOT in deps — we capture its value at load time (effect re-runs
    // only when the tab switches to a different query). A back-button navigation that changes
    // ?USR_ID without changing (connector, query) won't re-seed; the user can hit Run.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connector, query])

  // Fetch the full screen body iff a screen for this (connector, query) is in the workspace
  // catalog *and* carries at least one of the renderable bits (dialog / row_menu / toolbar
  // actions). A 404 (e.g. the catalog raced ahead of a delete) silently degrades to "no screen"
  // → inline editor; this is best-effort UX, not a security gate.
  useEffect(() => {
    const stub = findScreen(connector, query)
    // Phase 1 — fetch the screen body whenever *any* renderable bit is present, including the
    // new ``has_columns`` flag. A screen with no dialog / row_menu / actions but with its own
    // ``columns`` list still needs the body so the grid picks the screen's hints instead of
    // the query's.
    if (!stub || (!stub.has_dialog && !stub.has_row_menu && !stub.has_actions && !stub.has_columns)) {
      setScreen(null); return
    }
    let cancelled = false
    api
      .get<ScreenDetail>(`/api/screens/${encodeURIComponent(stub.app)}/${encodeURIComponent(stub.id)}`)
      .then((s) => { if (!cancelled) setScreen(s) })
      .catch(() => { if (!cancelled) setScreen(null) })
    return () => { cancelled = true }
  }, [findScreen, connector, query])

  // Server-filter fields: columns flagged `filter` in the *screen*'s ``columns`` if present,
  // else the query's `columns` config (v1's col_filter). The migrated SQL is wrapped in
  // `SELECT * FROM (…) _flt WHERE …` with a `:<col>` + optional `:<col>_op` bind per such
  // column, so the value here actually pre-filters server-side. (Phase 1 prefers the screen
  // when set; Phase 2 will make this the only source.)
  const filterCols = useMemo(
    () => (screen?.columns?.length ? screen.columns : (meta?.columns ?? [])).filter((c) => c.filter),
    [screen, meta],
  )
  const filterBindNames = useMemo(() => {
    const s = new Set<string>()
    for (const c of filterCols) { s.add(c.name); s.add(`${c.name}_op`) }
    return s
  }, [filterCols])

  // the set/non-empty server filters as {col: value} — drives the grid's `visible_when` columns
  const activeFilters = useMemo(
    () => Object.fromEntries(Object.entries(filters).filter(([, f]) => f.val !== '').map(([k, f]) => [k, f.val])),
    [filters],
  )

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

  // Phase 1 — overlay the screen's ``columns`` (when set) on top of the SQL result's discovered
  // columns. The screen ships the resolved label/format/hidden/filter/filter_from/visible_when/
  // rule/width/align/dd from its own ColumnHint list; the SQL result carries the discovered
  // ``type`` (best-effort, from cursor.description). The merge: each screen column wins for
  // display; ``type`` comes from the matching result column (case-insensitive); result columns
  // the screen doesn't hint at follow at the end (keep discovery order, unhinted). Empty
  // screen.columns → pass the result through untouched (back-compat).
  const effectiveResult = useMemo<QueryResult | null>(() => {
    if (!result) return null
    if (!screen?.columns?.length) return result
    const byLower = new Map(result.columns.map((c) => [c.name.toLowerCase(), c]))
    const used = new Set<string>()
    const merged: typeof result.columns = []
    for (const sc of screen.columns) {
      const rc = byLower.get(sc.name.toLowerCase())
      if (!rc) continue   // stale screen hint — column the query doesn't return
      used.add(rc.name)
      merged.push({ ...sc, name: rc.name, type: rc.type ?? sc.type ?? null })
    }
    for (const rc of result.columns) {
      if (!used.has(rc.name)) merged.push(rc)
    }
    return { ...result, columns: merged }
  }, [result, screen])

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

  const hasSelectResult = result?.statement_type === 'SELECT'
  // override this run's row cap (DbVisualizer-style) — blank = the configured connector/pool/query cap
  const maxRowsField = meta.statement_type === 'SELECT' ? (
    <MaxRowsBox title={t('table.maxRowsHint')}>
      {t('table.maxRows')}
      <input
        type="number" min={1} placeholder={t('table.maxRowsHint')}
        value={maxRows} onChange={(e) => setMaxRows(e.target.value)}
      />
    </MaxRowsBox>
  ) : null
  const runBtn = (
    <RunBtn onClick={run} disabled={busy}>
      {busy ? <SpinnerRing size={13} thickness={2} /> : <Play size={13} />}
      {busy ? t('common.running') : t('common.run')}
    </RunBtn>
  )
  // The standalone control bar carries the param form; it also carries Run + Max-rows until the
  // first SELECT result lands — after that those two move into the grid's own toolbar (see below),
  // so the data has the screen to itself.
  const showBar = paramNames.length > 0 || !hasSelectResult

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
      description={<Mono>{connector}.{query}</Mono>}
    >
      {/* The Stack fills PageContent's height; the chrome above the grid (filter panel /
          control bar / result meta line) takes its natural height, the DataTable below has
          `flex: 1` and flexes into whatever's left. The TanStack table scrolls *inside*
          itself — the filter panel and toolbar stay fixed. */}
      <Stack gap={14} style={{ flex: 1, minHeight: 0 }}>
        {filterCols.length > 0 && (
          <FilterPanel
            cols={filterCols}
            values={filters}
            onChange={(name, next) => setFilters((f) => ({ ...f, [name]: next }))}
            onClearAll={() => setFilters({})}
            autoLoad={meta.auto_load}
          />
        )}

        {showBar && (
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
            {!result && maxRowsField}
            {!hasSelectResult && runBtn}
          </Row>
        )}

        {runErr && <Banner $tone="error">{runErr}</Banner>}

        {effectiveResult && effectiveResult.statement_type === 'SELECT' && (
          // The inner Stack is the one that wraps ResultMeta + ResultTable. It must also
          // `flex: 1; minHeight: 0` so the chain reaches all the way down to DataTable —
          // without this the inner Stack grows to its content's natural height (table rows ×
          // row height), the outer Stack overflows PageContent, and the page scrolls instead
          // of the table.
          <Stack gap={10} style={{ flex: 1, minHeight: 0 }}>
            <Row align="center" style={{ justifyContent: 'space-between' }}>
              <Meta style={{ flex: 1 }}>
                {t(effectiveResult.row_count === 1 ? 'table.rows_one' : 'table.rows_other', { count: effectiveResult.row_count })} ·{' '}
                {effectiveResult.duration_ms.toFixed(1)} {t('common.ms')}
                {effectiveResult.truncated && (
                  <span style={{ color: colors.red.main }}> · {t('table.truncatedTo', { n: effectiveResult.rows.length })}</span>
                )}
              </Meta>
              {effectiveResult.columns.length > 0 && (
                <ViewToggle role="group" aria-label={t('table.viewToggle')}>
                  <button type="button" aria-pressed={view === 'table'} onClick={() => setView('table')} title={t('table.viewTable')}>
                    <TableIcon size={12} /> {t('table.viewTable')}
                  </button>
                  <button type="button" aria-pressed={view === 'chart'} onClick={() => setView('chart')} title={t('table.viewChart')}>
                    <BarChart3 size={12} /> {t('table.viewChart')}
                  </button>
                </ViewToggle>
              )}
            </Row>
            {effectiveResult.columns.length === 0 ? (
              <Meta>{t('table.noColumns')}</Meta>
            ) : view === 'chart' ? (
              // Suspense + lazy: the Recharts chunk only fetches when the operator first
              // toggles to Chart mode. The fallback shows the existing centred spinner.
              <Suspense fallback={<Centered />}>
                <ChartView result={effectiveResult} connector={connector} query={query} />
              </Suspense>
            ) : (
              <ResultTable
                key={effectiveResult.columns.map((c) => c.name).join('|')}
                result={effectiveResult}
                connector={connector}
                query={query}
                updateQuery={meta.update_query}
                insertQuery={meta.insert_query}
                deleteQuery={meta.delete_query}
                keyColumns={meta.key_columns}
                onSaved={run}
                runControl={runBtn}
                maxRowsControl={maxRowsField}
                activeFilters={activeFilters}
                screen={screen}
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
