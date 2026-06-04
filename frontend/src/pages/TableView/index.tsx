// Run a connector's named SQL query: a param form built from the query's
// params/bind_params, then SELECT → a sortable/paged grid (ResultTable) or a
// writable statement → confirm + affected-rows banner. Rendered inside a tab
// (see components/TabHost) — `connector`/`query` come in as props, not route params.
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useLocation, useSearchParams } from 'react-router-dom'
import { Table as TableIcon, Play, BarChart3, Network } from 'lucide-react'
import { api, ApiError, streamNDJSON } from '../../api/client'
import type { Column, ConnectorMeta, QueryResult, SqlQueryMeta } from '../../types/connectors'
import { PageLayout, Input, Field, Banner, Centered, Tag, Mono, Row, Stack, SpinnerRing, useModals } from '../../common'
import { colors, radius, fontSize, fonts } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { findMenuLabel } from '../../services/menuLabels'
import { tabPath } from '../../tabs/TabsContext'
import type { ScreenDetail } from '../../types/screens'
import { Meta } from './styled'
import { ResultTable } from './ResultTable'
import { FilterPanel, type ServerFilter } from './FilterPanel'
// ChartView pulls in Recharts (~75 kB gz) — split into its own chunk so the table view's
// initial load stays tight. The chunk only fetches when the operator toggles to Chart mode.
const ChartView = lazy(() => import('./ChartView').then((m) => ({ default: m.ChartView })))
// TreeView is much smaller (no third-party tree lib — recursive React) but follows the same
// lazy pattern for consistency. Only loads when the operator toggles to Tree mode AND only
// surfaces as a toggle when the screen carries a ``treeview`` config.
const TreeView = lazy(() => import('./TreeView').then((m) => ({ default: m.TreeView })))

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

export default function TableView({ connector, query, screenApp, screenId }: { connector: string; query: string; screenApp?: string; screenId?: string }) {
  const { t } = useTranslation()
  const modals = useModals()
  const { menus, findScreen, findScreenById } = useWorkspace()
  // URL search params seed the param form / filter panel on initial load — that's how the
  // row-menu's NavigateAction drill-down works ("?USR_ID=42" → the destination's USR_ID param
  // is pre-filled, then auto_load fires the query against that value). Also makes screens
  // deep-linkable / shareable. Only consumed once per (connector, query) mount; subsequent
  // user edits to the param form are not reflected back into the URL (the form is the source
  // of truth after that point).
  const [searchParams] = useSearchParams()
  const location = useLocation()
  // Drill-down binds (bugfix): a row-menu ``navigate`` pushes ``/sql/<c>/<q>?<binds>`` to filter the
  // destination by the clicked row. ``drillKey`` is the URL query string; ``lastDrillKey`` is the
  // binds we've already applied to the filter/param state. The ``applyDrill`` effect (below
  // ``autoLoad``) reconciles them reactively — so the binds land regardless of the connector-vs-
  // screen fetch order, and re-applying on a re-navigation to an already-open tab (tabs are keyed
  // by (connector, query) only and stay mounted, so they don't remount on re-navigation). Without
  // this the operator sees ALL rows instead of the selected row's.
  const drillKey = searchParams.toString()
  const lastDrillKey = useRef<string | null>(null)
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
  // Table / Chart / Tree toggle. Chart pulls in Recharts (~75 kB gz) and Tree is a tiny
  // recursive React component — both lazy-imported. The Tree button only surfaces when the
  // current screen carries a ``treeview`` config (gate below); on screens without one the
  // toggle reads Table / Chart as before. Resets to 'table' on remount — opening a fresh
  // tab in chart or tree mode would surprise the operator.
  const [view, setView] = useState<'table' | 'chart' | 'tree'>('table')

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
        for (const p of q.params) if (p.default != null) init[p.name] = p.default
        const declared = new Set([...q.params.map((p) => p.name), ...q.bind_params])
        // Only declared params are seeded here. Filter-column seeding (the drill-down vehicle —
        // Phase 3 moved filter columns to the Screen) is owned by the ``applyDrill`` effect below:
        // it waits for the screen so it knows which columns are filter-flagged, and it's reactive
        // (keyed on the URL binds), so it survives the connector-meta-vs-screen fetch race AND a
        // re-navigation to an already-open tab — both of which the old mount-time seed missed.
        searchParams.forEach((v, k) => { if (declared.has(k)) init[k] = v })
        setParams(init)
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
  // ``screenReady`` flips true once the screen-body fetch *settles* (loaded, 404'd, or no stub) —
  // applyDrill waits on this rather than on ``screen`` being non-null, so a screen whose body fails
  // to load doesn't hang the drill seed (and the auto-run it gates) forever.
  const [screenReady, setScreenReady] = useState(false)
  useEffect(() => {
    // When a specific screen is requested (a ``screen`` menu leaf → ``/screen/<app>/<id>``), open
    // THAT screen by id — two screens on the same read query stay distinct. Otherwise resolve the
    // screen by (connector, query) as usual (the inline / generic path).
    const stub = screenId ? findScreenById(screenApp ?? connector, screenId) : findScreen(connector, query)
    setScreenReady(false)
    // Phase 3 — every Screen drives per-query behaviour now (columns / auto_load / audit / max_rows
    // / update_query / etc.), so always fetch the full body when a Screen exists for this
    // (connector, query). Falls back to inline editor only when no screen is configured.
    if (!stub) { setScreen(null); setScreenReady(true); return }
    let cancelled = false
    api
      .get<ScreenDetail>(`/api/screens/${encodeURIComponent(stub.app)}/${encodeURIComponent(stub.id)}`)
      .then((s) => {
        if (cancelled) return
        setScreen(s)
        // Drill-down filter seeding moved to the ``applyDrill`` effect below — it reads the screen
        // + URL reactively so it doesn't depend on this fetch winning a race or being captured at
        // the right moment.
      })
      .catch(() => { if (!cancelled) setScreen(null) })
      .finally(() => { if (!cancelled) setScreenReady(true) })
    return () => { cancelled = true }
  }, [findScreen, findScreenById, connector, query, screenApp, screenId])

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

  // Live row count while the streaming SELECT is in flight — surfaced on the Run button so
  // the operator sees "Running… 1,200 rows" instead of an undifferentiated spinner. Reset to
  // null on each new run + when the result lands.
  const [streamCount, setStreamCount] = useState<number | null>(null)
  // AbortController for the in-flight stream — cancelled when the user navigates away or
  // kicks off a new run. The fetch reader's `cancel()` releases the server-side cursor too.
  const streamAbortRef = useRef<AbortController | null>(null)

  const run = useCallback(async () => {
    if (!meta) return
    if (meta.writable) {
      const ok = await modals.confirm({
        title: t('table.run'),
        message: t('table.runWritableConfirm', { q: `${connector}.${query}` }),
        confirmLabel: t('table.run'),
      })
      if (!ok) return
    }
    // Cancel any in-flight stream before starting a new one — releases the previous server-
    // side cursor + lets the next run's `meta` event swap columns cleanly.
    streamAbortRef.current?.abort()
    setBusy(true)
    setRunErr(null)
    setStreamCount(null)
    const sent: Record<string, string> = {}
    for (const [k, v] of Object.entries(params)) if (v !== '') sent[k] = v
    for (const [name, f] of Object.entries(filters)) if (f.val !== '') { sent[name] = f.val; sent[`${name}_op`] = f.op }
    const limit = maxRows.trim()  // `_limit=N` overrides the connector/pool/query row cap for this run
    try {
      if (meta.statement_type === 'SELECT') {
        // **Streaming path** — emits `meta` once (columns + cap), then N `rows` events
        // (chunks of 100 rows each by default), then `done`. We assemble a growing
        // QueryResult and re-render in batched flushes (every ~120ms) so the operator sees
        // rows appear live without React stuttering on per-chunk re-renders. On a 15K-row
        // query that took ~6s end-to-end before, the first rows land in ~100ms and the grid
        // is responsive throughout.
        const qs = new URLSearchParams({ ...sent, _stream: '1', ...(limit ? { _limit: limit } : {}) }).toString()
        const path = `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}?${qs}`
        const ctrl = new AbortController()
        streamAbortRef.current = ctrl
        const startedAt = performance.now()
        let cols: Column[] = []
        let allRows: Record<string, unknown>[] = []
        let lastFlush = startedAt
        let streamErr: string | null = null

        const flush = (finalEvent?: { truncated: boolean; duration_ms: number }) => {
          const dur = finalEvent ? finalEvent.duration_ms : (performance.now() - startedAt)
          // Copy `allRows` so React sees a new array reference (and TanStack's row model
          // picks up the new rows). The Column array is replaced on every flush too — it's
          // small and the meta event sets it once at the top of the stream.
          setResult({
            connector, query,
            statement_type: 'SELECT',
            columns: cols,
            rows: [...allRows],
            rowcount: -1,
            row_count: allRows.length,
            duration_ms: dur,
            truncated: finalEvent?.truncated ?? false,
          })
        }

        await streamNDJSON(path, { method: 'GET', signal: ctrl.signal }, (data) => {
          const ev = data as
            | { kind: 'meta'; columns: Column[]; cap: number; chunk_size: number }
            | { kind: 'rows'; rows: Record<string, unknown>[]; sent: number }
            | { kind: 'done'; total: number; truncated: boolean; duration_ms: number; rowcount: number }
            | { kind: 'error'; detail: string }
            | { kind: 'unknown' }
          if (ev.kind === 'meta') {
            cols = ev.columns
            allRows = []
            // Seed the grid with the columns immediately — empty rows yet, but the headers
            // + filter row paint right away while the first chunk is still in flight.
            flush()
          } else if (ev.kind === 'rows') {
            allRows = allRows.concat(ev.rows)
            setStreamCount(ev.sent)
            const now = performance.now()
            // Batched re-render — every ~120ms or so. Under that interval, React + TanStack
            // would do per-chunk diffs and we'd lose the speedup. The final `done` event
            // forces a flush regardless of timing.
            if (now - lastFlush > 120) {
              lastFlush = now
              flush()
            }
          } else if (ev.kind === 'done') {
            flush({ truncated: ev.truncated, duration_ms: ev.duration_ms })
          } else if (ev.kind === 'error') {
            streamErr = ev.detail
          }
        })

        if (streamErr) setRunErr(streamErr)
      } else {
        const res = await api.post<QueryResult>(
          `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(query)}`,
          { params: sent },
        )
        setResult(res)
      }
    } catch (e) {
      // An AbortError from a deliberate cancellation isn't an error to surface — the operator
      // just kicked off another run, or navigated away. Anything else is real.
      if ((e as { name?: string }).name === 'AbortError') return
      setRunErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
      setStreamCount(null)
    }
  }, [meta, params, filters, maxRows, connector, query, t, modals])

  // Abort the in-flight stream when the screen unmounts (tab close / route change). Without
  // this the server keeps the cursor open until the client connection drops — wasteful, and
  // on Oracle can leak cursors at the pool level until they're recycled.
  useEffect(() => () => { streamAbortRef.current?.abort() }, [])

  // True when *this* tab's TableView is the active route. Every tab stays mounted (TabHost) and
  // they all share one ``useSearchParams`` / ``useLocation``, so the URL binds belong to whichever
  // tab the path points at — gate the drill logic on this so an inactive tab never reacts to
  // another tab's drill URL. A screen tab routes through ``/screen/<app>/<id>`` (not
  // ``/sql/<connector>/<query>``), so match the right path — otherwise the drill binds (filters
  // passed between screens) never seed and auto_load runs unfiltered.
  const onThisTab = location.pathname === (
    screenId
      ? tabPath({ kind: 'screen', connector: screenApp ?? connector, target: screenId })
      : tabPath({ kind: 'sql', connector, target: query })
  )

  // Auto-load: run a SELECT immediately when the screen opens, once, if the screen asks for it.
  // Phase 3 — ``auto_load`` is a screen-level flag now; fall back to the (deprecated) meta-level
  // bool for back-compat with connectors-only setups.
  const autoLoad = screen?.auto_load ?? meta?.auto_load ?? false
  const autoRan = useRef(false)
  useEffect(() => { autoRan.current = false }, [connector, query])
  useEffect(() => {
    if (!autoLoad || meta?.statement_type !== 'SELECT' || autoRan.current || result || busy) return
    // Hold the first auto-run until any drill-down binds in the URL have been *applied* to the
    // filter/param state (``applyDrill`` below marks them once meta + screen are ready). Without
    // this the auto-run could fire before the binds land → the row-menu "Display Roles" shows ALL
    // rows. ``lastDrillKey`` tracks the applied binds; while they differ we wait.
    if (onThisTab && drillKey && drillKey !== lastDrillKey.current) return
    autoRan.current = true
    run()
  }, [autoLoad, meta, result, busy, run, onThisTab, drillKey])

  // applyDrill — seed the param form + filter panel from the URL drill binds, *reactively*. Fires
  // whenever this tab is the active route and the URL binds differ from what's been applied, so it
  // covers all the cases the old mount-time seed missed: the connector-meta-vs-screen fetch race,
  // a re-navigation to an already-open tab, and a reactivated tab. Waits for ``meta`` (declared
  // param names) and — when a screen exists — the screen body (so the filter-flagged columns are
  // known) before marking the binds applied, so it never seeds with half the picture.
  useEffect(() => {
    if (!onThisTab || drillKey === lastDrillKey.current) return
    if (drillKey === '') { lastDrillKey.current = drillKey; return }  // no binds → nothing to seed
    if (!meta || !screenReady) return                                // wait for declared params + screen filter cols
    const declared = new Set([...meta.params.map((p) => p.name), ...meta.bind_params])
    const filterCols = new Set((screen?.columns ?? []).filter((c) => c.filter).map((c) => c.name))
    const nextParams: Record<string, string> = {}
    const nextFilters: Record<string, ServerFilter> = {}
    searchParams.forEach((v, k) => {
      if (declared.has(k)) nextParams[k] = v
      if (filterCols.has(k)) nextFilters[k] = { val: v, op: 'equals' }   // SQL wrap expects `equals`
    })
    lastDrillKey.current = drillKey                                  // applied (meta + screen ready)
    if (Object.keys(nextParams).length === 0 && Object.keys(nextFilters).length === 0) return
    setParams((cur) => ({ ...cur, ...nextParams }))
    setFilters((cur) => ({ ...cur, ...nextFilters }))
    // Re-arm auto_load so it runs with the freshly-applied binds (covers a re-navigation where the
    // grid already shows a previous result). Only blank when the screen auto-loads — a manual-run
    // screen keeps its rows and picks up the binds on the next Run rather than going blank.
    if (autoLoad) {
      autoRan.current = false
      setResult(null)
    }
  }, [onThisTab, drillKey, meta, screen, screenReady, searchParams, autoLoad])

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
  // Live row count surfaces inside the Run button while a stream is in flight — operator
  // sees "Running… 1,200 rows" instead of an undifferentiated spinner. The streaming SELECT
  // path keeps appending; this just reads the running cumulative count.
  const runLabel = busy
    ? (streamCount != null
        ? t('table.runningRows', { count: streamCount, defaultValue: 'Running… {{count}} rows' })
        : t('common.running'))
    : t('common.run')
  const runBtn = (
    <RunBtn onClick={run} disabled={busy}>
      {busy ? <SpinnerRing size={13} thickness={2} /> : <Play size={13} />}
      {runLabel}
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
            autoLoad={autoLoad}
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
                  {/* Tree button gated on the screen carrying a ``treeview`` config — surfaces
                      on hierarchy-shaped screens (security_menus, sod_processes, …) and stays
                      hidden on flat tables. */}
                  {screen?.treeview && (
                    <button type="button" aria-pressed={view === 'tree'} onClick={() => setView('tree')} title={t('table.viewTree', 'Tree')}>
                      <Network size={12} /> {t('table.viewTree', 'Tree')}
                    </button>
                  )}
                </ViewToggle>
              )}
            </Row>
            {effectiveResult.columns.length === 0 ? (
              <Meta>{t('table.noColumns')}</Meta>
            ) : view === 'chart' ? (
              // Suspense + lazy: the Recharts chunk only fetches when the operator first
              // toggles to Chart mode. The fallback shows the existing centred spinner.
              <Suspense fallback={<Centered />}>
                <ChartView result={effectiveResult} connector={connector} query={query} screen={screen} />
              </Suspense>
            ) : view === 'tree' && screen?.treeview ? (
              // Tree-mode falls back to Table when the screen has no treeview config
              // (defensive — the toggle button is gated above, but a stale ``view='tree'``
              // could persist across a screen swap). Same Suspense + lazy pattern as Chart.
              <Suspense fallback={<Centered />}>
                <TreeView result={effectiveResult} screen={screen} />
              </Suspense>
            ) : (
              <ResultTable
                key={effectiveResult.columns.map((c) => c.name).join('|')}
                result={effectiveResult}
                connector={connector}
                query={query}
                /* Phase 3 — CRUD companions + key_columns live on Screen now; fall back to
                 * the (deprecated) meta.* fields when no screen exists (back-compat). */
                updateQuery={screen?.update_query ?? meta.update_query ?? null}
                insertQuery={screen?.insert_query ?? meta.insert_query ?? null}
                deleteQuery={screen?.delete_query ?? meta.delete_query ?? null}
                keyColumns={screen?.key_columns ?? meta.key_columns}
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
