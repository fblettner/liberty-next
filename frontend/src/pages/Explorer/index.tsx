// The Data Explorer page (/explorer): a model per app (GET /api/explorer/{app}/model), the one
// AnalysisDefinition the fields pane and the shelves edit, and the result — an aggregate table,
// the detail rows, or the rows behind an aggregate cell (drill). Pivot, charts, saved analyses
// and export come in the next parts. The analysis re-runs on its own when its definition changes.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Compass, Play, Table2, List, ArrowLeft, Code2, X } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { useAuth } from '../../auth/AuthContext'
import { Banner, Button, Centered, Input, PageLayout, SearchSelect, SpinnerRing, type SearchSelectOption } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import {
  addField, addFilter, buildAggregateRequest, buildDetailRequest, dateFields, drillCellsOf, emptyDefinition, fieldIndex, usedKeys, withCalculated,
} from '../../services/explorer'
import type { AggregateResult, AnalysisDefinition, DetailResult, DrillCell, ExplorerModel, ExplorerModelSummary, FieldType } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'
import { FieldsPane } from './FieldsPane'
import { Shelves } from './Shelves'
import { PeriodBar } from './PeriodBar'
import { ResultTable } from './ResultTable'

const Body = styled.div`display: grid; grid-template-columns: 260px 1fr; gap: 12px; flex: 1; min-height: 0;`
const Canvas = styled.div`display: flex; flex-direction: column; gap: 8px; min-height: 0; min-width: 0;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap;`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; padding: 5px 10px; border: 0; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')}; color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  & + & { border-left: 1px solid ${colors.border}; }
  &:disabled { opacity: 0.5; cursor: default; }
`
const Note = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Empty = styled.div`flex: 1; display: flex; align-items: center; justify-content: center; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; border: 1px dashed ${colors.border}; border-radius: ${radius.md};`
const DrillBar = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; font-family: ${fonts.sans};`
const Pill = styled.span`padding: 2px 8px; border-radius: ${radius.md}; border: 1px solid ${colors.blue.border}; background: ${colors.blue.bg}; color: ${colors.text.primary}; font-size: ${fontSize.sm};`
const Sql = styled.pre`margin: 0; padding: 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.secondary}; font-size: ${fontSize.micro}; font-family: ${fonts.mono}; white-space: pre-wrap; max-height: 220px; overflow: auto;`

const PAGE_SIZE = 50

export default function ExplorerPage() {
  const { t, i18n } = useTranslation()
  const lang = i18n.language || 'en'
  const { user } = useAuth()
  const { currentApp } = useWorkspace()
  const [models, setModels] = useState<ExplorerModelSummary[] | null>(null)
  const [app, setApp] = useState<string>('')
  const [model, setModel] = useState<ExplorerModel | null>(null)
  const [def, setDef] = useState<AnalysisDefinition | null>(null)
  const [calcTypes] = useState<Record<string, FieldType>>({})
  const [result, setResult] = useState<AggregateResult | null>(null)
  const [detail, setDetail] = useState<DetailResult | null>(null)
  const [drill, setDrill] = useState<DrillCell[] | null>(null)
  const [page, setPage] = useState(1)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showSql, setShowSql] = useState(false)
  const runId = useRef(0)

  // The models the caller may use; the current app's when it has one.
  useEffect(() => {
    api.get<{ models: ExplorerModelSummary[] }>('/api/explorer')
      .then((r) => { setModels(r.models); setApp((cur) => cur || (currentApp && r.models.some((m) => m.app === currentApp) ? currentApp : (r.models[0]?.app ?? ''))) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [currentApp])
  useEffect(() => {
    if (!app) return
    setModel(null); setDef(null); setResult(null); setDetail(null); setDrill(null); setError(null)
    api.get<ExplorerModel>(`/api/explorer/${encodeURIComponent(app)}/model`)
      .then((m) => { setModel(m); setDef(emptyDefinition(m.hub)) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [app])

  const entities = useMemo(() => (model ? withCalculated(model, def?.calculated ?? [], calcTypes) : []), [model, def?.calculated, calcTypes])
  const fields = useMemo(() => fieldIndex(entities), [entities])
  const used = useMemo(() => (def ? usedKeys(def) : new Set<string>()), [def])
  const dates = useMemo(() => (def ? dateFields(entities, def.entity) : []), [entities, def])
  const entityOpts: SearchSelectOption[] = (model?.entities ?? []).map((e) => ({ value: e.id, label: e.label, mono: e.id }))

  // Run — aggregate, detail (the rows shelf as columns) or the drill's detail; stale replies ignored.
  const run = useCallback(async (d: AnalysisDefinition, opts: { drill?: DrillCell[] | null; page?: number } = {}) => {
    if (!app) return
    const id = ++runId.current
    const wantDetail = d.view === 'detail' || !!opts.drill
    if (!wantDetail && d.rows.length === 0 && d.measures.length === 0) { setResult(null); return }
    setRunning(true); setError(null)
    try {
      if (wantDetail) {
        const dr = opts.drill ?? undefined
        const columns = dr ? drillColumns(d, model) : undefined
        const r = await api.post<DetailResult>(`/api/explorer/${encodeURIComponent(app)}/detail`, buildDetailRequest(d, { drill: dr, columns, page: opts.page ?? 1, pageSize: PAGE_SIZE, debug: !!user?.is_superuser }))
        if (id === runId.current) setDetail(r)
      } else {
        const r = await api.post<AggregateResult>(`/api/explorer/${encodeURIComponent(app)}/query`, buildAggregateRequest(d, { debug: !!user?.is_superuser }))
        if (id === runId.current) setResult(r)
      }
    } catch (e) {
      if (id === runId.current) setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      if (id === runId.current) setRunning(false)
    }
  }, [app, model, user?.is_superuser])

  // Re-run when the definition changes (debounced), leaving a drill when the analysis moves on.
  useEffect(() => {
    if (!def) return
    setDrill(null); setPage(1)
    const h = window.setTimeout(() => { void run(def) }, 300)
    return () => window.clearTimeout(h)
  }, [def, run])

  const onDrill = (row: Record<string, unknown>) => {
    if (!def || !result) return
    const cells = drillCellsOf(row, def.rows)
    setDrill(cells); setPage(1)
    void run(def, { drill: cells, page: 1 })
  }
  const onPage = (p: number) => { if (!def) return; setPage(p); void run(def, { drill, page: p }) }
  const setView = (view: AnalysisDefinition['view']) => { if (def) setDef({ ...def, view }) }

  if (error && !model) return <PageLayout icon={<Compass size={18} />} title={t('explorer.title')}><Banner $tone="error">{error}</Banner></PageLayout>
  if (models && models.length === 0) return <PageLayout icon={<Compass size={18} />} title={t('explorer.title')}><Empty>{t('explorer.noModels')}</Empty></PageLayout>
  if (!model || !def) return <PageLayout icon={<Compass size={18} />} title={t('explorer.title')}><Centered /></PageLayout>

  const showing = drill ? detail : def.view === 'detail' ? detail : result
  const sql = showing?.sql

  return (
    <PageLayout icon={<Compass size={18} />} title={t('explorer.title')} description={<span>{model.label}{model.description ? ` — ${model.description}` : ''}</span>}
      headerRight={models && models.length > 1 ? (
        <div style={{ width: 200 }}><SearchSelect value={app} options={models.map((m) => ({ value: m.app, label: m.label, mono: m.app }))} onChange={setApp} /></div>
      ) : undefined}>
      {model.warnings.length > 0 && user?.is_superuser && <Banner $tone="warning" style={{ marginBottom: 8 }}>{model.warnings.join(' · ')}</Banner>}
      <Body>
        <FieldsPane entities={entities} primary={def.entity} used={used}
          onAdd={(f) => setDef(addField(def, f))} onFilter={(f) => setDef(addFilter(def, f))}
          onPrimary={(e) => setDef({ ...emptyDefinition(e), view: def.view })} />
        <Canvas>
          <Toolbar>
            <div style={{ width: 200 }}><SearchSelect value={def.entity} options={entityOpts} onChange={(e) => setDef({ ...emptyDefinition(e), view: def.view })} /></div>
            <Seg>
              <SegBtn type="button" $active={def.view === 'table'} onClick={() => setView('table')}><Table2 size={13} /> {t('explorer.view.table')}</SegBtn>
              <SegBtn type="button" $active={def.view === 'detail'} onClick={() => setView('detail')}><List size={13} /> {t('explorer.view.detail')}</SegBtn>
            </Seg>
            {def.view !== 'detail' && (
              <>
                <Note>{t('explorer.topN')}</Note>
                <div style={{ width: 90 }}><Input type="number" min={1} max={10000} value={def.limit} onChange={(e) => setDef({ ...def, limit: Math.max(1, Math.min(10000, Number(e.target.value) || 1)) })} /></div>
              </>
            )}
            <div style={{ flex: 1 }} />
            {running && <SpinnerRing size={14} thickness={2} />}
            {result && !drill && def.view !== 'detail' && (
              <Note>{t('explorer.groupsNote', { groups: result.groups, scanned: result.scanned })}{result.truncated ? ` · ${t('explorer.truncated', { limit: def.limit })}` : ''}{result.scan_truncated ? ` · ${t('explorer.scanTruncated')}` : ''}</Note>
            )}
            {user?.is_superuser && sql && <Button $size="sm" $variant="ghost" onClick={() => setShowSql((v) => !v)} title="SQL"><Code2 size={13} /></Button>}
            <Button $size="sm" $variant="primary" onClick={() => void run(def, { drill, page })} disabled={running}><Play size={13} /> {t('explorer.run')}</Button>
          </Toolbar>
          <PeriodBar period={def.period} dateFields={dates} onChange={(p) => setDef({ ...def, period: p })} />
          <Shelves def={def} fields={fields} onChange={setDef} />
          {error && <Banner $tone="error">{error}</Banner>}
          {showSql && sql && <Sql>{sql}</Sql>}
          {drill && (
            <DrillBar>
              <Button $size="sm" $variant="ghost" onClick={() => { setDrill(null); setPage(1) }}><ArrowLeft size={13} /> {t('explorer.backToAnalysis')}</Button>
              {drill.map((c) => <Pill key={c.field}>{fields.get(c.field)?.label ?? c.field}: {c.value === null ? t('explorer.emptyValue') : String(c.value)}</Pill>)}
              <Button $size="sm" $variant="ghost" onClick={() => { setDrill(null); setPage(1) }} title={t('common.close', 'Close')}><X size={13} /></Button>
            </DrillBar>
          )}
          {drill || def.view === 'detail' ? (
            detail ? (
              <ResultTable columns={detail.columns} rows={detail.rows} lang={lang} paging={{ page, pageSize: PAGE_SIZE, total: detail.total, onPage }} />
            ) : <Empty>{running ? t('common.loading', 'Loading…') : t('explorer.detailHint')}</Empty>
          ) : result ? (
            <ResultTable columns={result.columns} rows={result.rows} lang={lang} onRowClick={def.rows.length > 0 ? onDrill : undefined} />
          ) : (
            <Empty>{t('explorer.emptyHint')}</Empty>
          )}
        </Canvas>
      </Body>
    </PageLayout>
  )
}

/** The detail columns behind an aggregate cell: the primary's key columns, the dimensions'
 *  fields, then the measures' fields (calculated ones included). */
function drillColumns(def: AnalysisDefinition, model: ExplorerModel | null): string[] {
  const primary = model?.entities.find((e) => e.id === def.entity)
  const keys = (primary?.key ?? []).map((k) => `${def.entity}.${k.toUpperCase()}`)
  const dims = def.rows.map((r) => r.field)
  const meas = def.measures.flatMap((m) => (m.field ? [m.field] : []))
  return [...new Set([...keys, ...dims, ...meas])]
}
