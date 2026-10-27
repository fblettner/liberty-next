// The Data Explorer page (/explorer): a model per app (GET /api/explorer/{app}/model), the one
// AnalysisDefinition the fields pane and the shelves edit, and the result — a table, a pivot, a
// chart, KPI cards, the detail rows, or the rows behind an aggregate cell (drill). Switching
// between table / pivot / chart redraws the last aggregate without a new query; the analysis
// re-runs on its own when its definition changes. Two modes: Analyses (the editor, with the
// saved analyses picker, Save / Save as / Delete) and Dashboards (grids of saved analyses).
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Compass, Play, Table2, List, ArrowLeft, Code2, X, Grid3x3, BarChart3, LineChart, PieChart, Gauge, Save, Copy, Trash2, LayoutDashboard, FlaskConical, Pencil, FileDown } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { useAuth } from '../../auth/AuthContext'
import { Banner, Button, Centered, Checkbox, Input, PageLayout, SearchSelect, SpinnerRing, useModals, type SearchSelectOption } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import {
  addField, addFilter, buildAggregateRequest, buildDetailRequest, dateFields, drillCellsOf, emptyDefinition, fieldIndex, measureKey, usedKeys, withCalculated,
} from '../../services/explorer'
import { deleteSaved, downloadExport, listSaved, saveSaved, savedOptions, type DashboardDefinition, type SavedAnalysis, type SavedDashboard } from '../../services/explorerSaved'
import type { AggregateResult, AnalysisDefinition, AnalysisView, CalcDef, DetailResult, DrillCell, ExplorerModel, ExplorerModelSummary, FieldType } from '../../types/explorer'
import { colors, fontSize, fonts, radius } from '../../theme'
import { FieldsPane } from './FieldsPane'
import { Shelves } from './Shelves'
import { PeriodBar } from './PeriodBar'
import { ResultTable } from './ResultTable'
import { PivotView } from './PivotView'
import { ChartView } from './ChartView'
import { CalcModal } from './CalcModal'
import { RecordDialog } from './RecordDialog'
import { SaveModal } from './SaveModal'
import { DashboardsView } from './DashboardsView'

const Body = styled.div`display: grid; grid-template-columns: 260px 1fr; gap: 12px; flex: 1; min-height: 0;`
const Canvas = styled.div`display: flex; flex-direction: column; gap: 8px; min-height: 0; min-width: 0;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap;`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; padding: 5px 9px; border: 0; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')}; color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  & + & { border-left: 1px solid ${colors.border}; }
  &:disabled { opacity: 0.5; cursor: default; }
`
const Note = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Empty = styled.div`flex: 1; display: flex; align-items: center; justify-content: center; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; border: 1px dashed ${colors.border}; border-radius: ${radius.md};`
const DrillBar = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; font-family: ${fonts.sans};`
const Pill = styled.span`padding: 2px 8px; border-radius: ${radius.md}; border: 1px solid ${colors.blue.border}; background: ${colors.blue.bg}; color: ${colors.text.primary}; font-size: ${fontSize.sm};`
const Sql = styled.pre`margin: 0; padding: 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.secondary}; font-size: ${fontSize.micro}; font-family: ${fonts.mono}; white-space: pre-wrap; max-height: 220px; overflow: auto;`
const Options = styled.div`display: flex; align-items: center; gap: 10px; flex-wrap: wrap; font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const ModeBar = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px;`

const PAGE_SIZE = 50
const AGG_VIEWS: AnalysisView[] = ['table', 'pivot', 'bar', 'line', 'pie', 'kpi']
type Mode = 'analyses' | 'dashboards'

export default function ExplorerPage() {
  const { t, i18n } = useTranslation()
  const lang = i18n.language || 'en'
  const { user } = useAuth()
  const modals = useModals()
  const { currentApp, explorerModels } = useWorkspace()
  const [models, setModels] = useState<ExplorerModelSummary[] | null>(null)
  const [app, setApp] = useState<string>('')
  const [modelId, setModelId] = useState<string>('')
  const [model, setModel] = useState<ExplorerModel | null>(null)
  const [mode, setMode] = useState<Mode>('analyses')
  const [def, setDef] = useState<AnalysisDefinition | null>(null)
  const [calcTypes, setCalcTypes] = useState<Record<string, FieldType>>({})
  const [result, setResult] = useState<AggregateResult | null>(null)
  const [detail, setDetail] = useState<DetailResult | null>(null)
  const [drill, setDrill] = useState<DrillCell[] | null>(null)
  const [page, setPage] = useState(1)
  const [running, setRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showSql, setShowSql] = useState(false)
  const [calcEditing, setCalcEditing] = useState<CalcDef | null | undefined>(undefined)
  const [record, setRecord] = useState<{ entity: string; row: Record<string, unknown> } | null>(null)
  // saved analyses + dashboards
  const [analyses, setAnalyses] = useState<SavedAnalysis[]>([])
  const [dashboards, setDashboards] = useState<SavedDashboard[]>([])
  const [currentAnalysis, setCurrentAnalysis] = useState<SavedAnalysis | null>(null)
  const [currentDashboard, setCurrentDashboard] = useState<SavedDashboard | null>(null)
  const [dashDef, setDashDef] = useState<DashboardDefinition>({ period: null, tiles: [] })
  const [dashEditing, setDashEditing] = useState(false)
  const [saveModal, setSaveModal] = useState<{ kind: 'analyses' | 'dashboards'; asNew: boolean } | null>(null)
  const runId = useRef(0)
  const pendingDrill = useRef<DrillCell[] | null>(null)

  // The model list is the workspace's (refreshed after a Settings save + reload); the first one
  // is picked when nothing is, and a model that disappeared falls back to the first.
  useEffect(() => {
    if (!explorerModels) return
    const list: ExplorerModelSummary[] = explorerModels.map((m) => ({ ...m, hub: '', entities: 0 }))
    setModels(list)
    if (list[0] && !list.some((m) => m.app === app && m.model === modelId)) { setApp(list[0].app); setModelId(list[0].model) }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- app / modelId are read, not followed
  }, [explorerModels])
  // The app picked in the left navigation drives the model, whenever that app has one.
  useEffect(() => {
    const first = currentApp ? models?.find((m) => m.app === currentApp) : undefined
    if (first) { setApp(first.app); setModelId((cur) => (models?.some((m) => m.app === first.app && m.model === cur) ? cur : first.model)) }
  }, [currentApp, models])
  const loadSaved = useCallback(async (a: string, m: string) => {
    try {
      const [an, da] = await Promise.all([listSaved<AnalysisDefinition>(a, m, 'analyses'), listSaved<DashboardDefinition>(a, m, 'dashboards')])
      setAnalyses(an); setDashboards(da)
    } catch { /* the pickers stay empty */ }
  }, [])
  useEffect(() => {
    if (!app || !modelId) return
    setModel(null); setDef(null); setResult(null); setDetail(null); setDrill(null); setError(null)
    setCurrentAnalysis(null); setCurrentDashboard(null); setDashDef({ period: null, tiles: [] }); setDashEditing(false)
    api.get<ExplorerModel>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(modelId)}/model`)
      .then((m) => { setModel(m); setDef(emptyDefinition(m.hub)) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
    void loadSaved(app, modelId)
  }, [app, modelId, loadSaved])

  const entities = useMemo(() => (model ? withCalculated(model, def?.calculated ?? [], calcTypes) : []), [model, def?.calculated, calcTypes])
  const fields = useMemo(() => fieldIndex(entities), [entities])
  const used = useMemo(() => (def ? usedKeys(def) : new Set<string>()), [def])
  const dates = useMemo(() => (def ? dateFields(entities, def.entity) : []), [entities, def])
  const entityOpts: SearchSelectOption[] = (model?.entities ?? []).map((e) => ({ value: e.id, label: e.label, mono: e.id }))
  const isAgg = (v: AnalysisView) => v !== 'detail'

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
        const r = await api.post<DetailResult>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(modelId)}/detail`, buildDetailRequest(d, { drill: dr, columns, page: opts.page ?? 1, pageSize: PAGE_SIZE, debug: !!user?.is_superuser }))
        if (id === runId.current) setDetail(r)
      } else {
        const r = await api.post<AggregateResult>(`/api/explorer/${encodeURIComponent(app)}/${encodeURIComponent(modelId)}/query`, buildAggregateRequest(d, { debug: !!user?.is_superuser }))
        if (id === runId.current) setResult(r)
      }
    } catch (e) {
      if (id === runId.current) setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      if (id === runId.current) setRunning(false)
    }
  }, [app, modelId, model, user?.is_superuser])

  // Re-run when the query-relevant part of the definition changes (a view switch between
  // table / pivot / chart only redraws). A drill handed over by a dashboard tile is applied here.
  const queryKey = def ? JSON.stringify(buildAggregateRequest(def)) + (def.view === 'detail' ? '|detail' : '') : ''
  useEffect(() => {
    if (!def) return
    const handed = pendingDrill.current
    pendingDrill.current = null
    setDrill(handed); setPage(1)
    const h = window.setTimeout(() => { void run(def, handed ? { drill: handed, page: 1 } : {}) }, 300)
    return () => window.clearTimeout(h)
    // eslint-disable-next-line react-hooks/exhaustive-deps -- queryKey is the serialised definition
  }, [queryKey, run])

  const onDrill = (values: Record<string, unknown>) => {
    if (!def) return
    const cells = drillCellsOf(values, def.rows)
    setDrill(cells); setPage(1)
    void run(def, { drill: cells, page: 1 })
  }
  const onPage = (p: number) => { if (!def) return; setPage(p); void run(def, { drill, page: p }) }
  const setView = (view: AnalysisView) => { if (def) setDef({ ...def, view }) }
  const saveCalc = (c: CalcDef, type: FieldType) => {
    if (!def) return
    const others = def.calculated.filter((x) => x.name !== (calcEditing?.name ?? c.name))
    setCalcTypes((m) => ({ ...m, [c.name]: type }))
    setDef({ ...def, calculated: [...others, c] })
    setCalcEditing(undefined)
  }
  const deleteCalc = async (name: string) => {
    if (!def) return
    if (!(await modals.confirm({ title: t('common.delete'), message: t('explorer.deleteCalc', { name }), variant: 'danger' }))) return
    const key = `calc.${name}`
    setDef({ ...def, calculated: def.calculated.filter((c) => c.name !== name), rows: def.rows.filter((r) => r.field !== key),
      measures: def.measures.filter((m) => m.field !== key), filterRows: def.filterRows.filter((f) => f.field !== key) })
  }
  const openRecord = (row: Record<string, unknown>) => {
    if (!def || !model) return
    const ent = model.entities.find((e) => e.id === def.entity)
    if (!ent?.screen || ent.key.length === 0) return
    const values: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(row)) if (k.startsWith(`${ent.id}.`)) values[k.slice(ent.id.length + 1).toUpperCase()] = v
    if (!ent.key.every((k) => k.toUpperCase() in values)) return
    setRecord({ entity: ent.id, row: values })
  }

  // ── saved analyses ──────────────────────────────────────────────────────────
  const loadAnalysis = (a: SavedAnalysis | null, handedDrill?: DrillCell[]) => {
    setCurrentAnalysis(a)
    if (handedDrill) pendingDrill.current = handedDrill
    if (a) setDef({ ...emptyDefinition(a.definition.entity || model?.hub || ''), ...a.definition })
    else if (model) setDef(emptyDefinition(model.hub))
    setMode('analyses')
  }
  const saveAnalysis = async (fields: { name: string; description: string; audience: string; shared: boolean }, asNew: boolean) => {
    if (!def) return
    const item = await saveSaved<AnalysisDefinition>(app, modelId, 'analyses', { id: asNew ? null : currentAnalysis?.id, as_new: asNew, name: fields.name, description: fields.description || null, audience: fields.audience || null, shared: fields.shared, definition: def })
    setCurrentAnalysis(item)
    await loadSaved(app, modelId)
  }
  const removeAnalysis = async () => {
    if (!currentAnalysis) return
    if (!(await modals.confirm({ title: t('common.delete'), message: t('explorer.deleteSaved', { name: currentAnalysis.name }), variant: 'danger' }))) return
    try { await deleteSaved(app, modelId, 'analyses', currentAnalysis.id); setCurrentAnalysis(null); await loadSaved(app, modelId) } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
  }
  const exportFile = async (format: 'xlsx' | 'pdf') => {
    setError(null)
    try {
      if (mode === 'dashboards') {
        if (!currentDashboard) return
        await downloadExport(app, modelId, { format, dashboard: currentDashboard.id })
      } else if (def) {
        await downloadExport(app, modelId, { format, definition: def, title: currentAnalysis?.name ?? t('explorer.title') })
      }
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
  }
  // ── dashboards ──────────────────────────────────────────────────────────────
  const loadDashboard = (d: SavedDashboard | null) => {
    setCurrentDashboard(d); setDashEditing(false)
    setDashDef(d ? { period: d.definition.period ?? null, tiles: d.definition.tiles ?? [] } : { period: null, tiles: [] })
  }
  const saveDashboard = async (fields: { name: string; description: string; audience: string; shared: boolean }, asNew: boolean) => {
    const item = await saveSaved<DashboardDefinition>(app, modelId, 'dashboards', { id: asNew ? null : currentDashboard?.id, as_new: asNew, name: fields.name, description: fields.description || null, audience: fields.audience || null, shared: fields.shared, definition: dashDef })
    setCurrentDashboard(item); setDashEditing(false)
    await loadSaved(app, modelId)
  }
  const removeDashboard = async () => {
    if (!currentDashboard) return
    if (!(await modals.confirm({ title: t('common.delete'), message: t('explorer.deleteSaved', { name: currentDashboard.name }), variant: 'danger' }))) return
    try { await deleteSaved(app, modelId, 'dashboards', currentDashboard.id); loadDashboard(null); await loadSaved(app, modelId) } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
  }

  if (error && !model) return <PageLayout icon={<Compass size={18} />} title={t('explorer.title')}><Banner $tone="error">{error}</Banner></PageLayout>
  if (models && models.length === 0) return <PageLayout icon={<Compass size={18} />} title={t('explorer.title')}><Empty>{t('explorer.noModels')}</Empty></PageLayout>
  if (!model || !def) return <PageLayout icon={<Compass size={18} />} title={t('explorer.title')}><Centered /></PageLayout>

  const showingDetail = !!drill || def.view === 'detail'
  const sql = showingDetail ? detail?.sql : result?.sql
  const measureOpts: SearchSelectOption[] = def.measures.map((m) => ({ value: measureKey(m.field, m.agg), label: m.field ? t(`explorer.aggLabel.${m.agg}`, { label: fields.get(m.field)?.label ?? m.field }) : t('explorer.rowCount') }))
  const dimOpts: SearchSelectOption[] = def.rows.map((r) => ({ value: r.bucket ? `${r.field}@${r.bucket}` : r.field, label: fields.get(r.field)?.label ?? r.field }))
  const recordEntity = record ? model.entities.find((e) => e.id === record.entity) : null
  const canOpenRecord = !!model.entities.find((e) => e.id === def.entity)?.screen
  const viewIcon: Record<AnalysisView, React.ReactNode> = { table: <Table2 size={13} />, pivot: <Grid3x3 size={13} />, bar: <BarChart3 size={13} />, line: <LineChart size={13} />, pie: <PieChart size={13} />, kpi: <Gauge size={13} />, detail: <List size={13} /> }
  const canDeleteAnalysis = !!currentAnalysis && (currentAnalysis.owner === user?.username || !!user?.is_superuser)
  const canDeleteDashboard = !!currentDashboard && (currentDashboard.owner === user?.username || !!user?.is_superuser)

  const modeBar = (
    <ModeBar>
      <Seg>
        <SegBtn type="button" $active={mode === 'analyses'} onClick={() => setMode('analyses')}><FlaskConical size={13} /> {t('explorer.analyses')} ({analyses.length})</SegBtn>
        <SegBtn type="button" $active={mode === 'dashboards'} onClick={() => setMode('dashboards')}><LayoutDashboard size={13} /> {t('explorer.dashboards')} ({dashboards.length})</SegBtn>
      </Seg>
      {mode === 'analyses' ? (
        <>
          <div style={{ width: 300 }}>
            <SearchSelect value={currentAnalysis?.id ?? ''} options={savedOptions(analyses, t('explorer.mine'))} anyLabel={t('explorer.newAnalysis')} placeholder={t('explorer.pickAnalysis')}
              onChange={(v) => loadAnalysis(analyses.find((a) => a.id === v) ?? null)} />
          </div>
          <Button $size="sm" $variant="ghost" onClick={() => setSaveModal({ kind: 'analyses', asNew: false })} disabled={def.rows.length === 0 && def.measures.length === 0}><Save size={13} /> {t('common.save')}</Button>
          {currentAnalysis && <Button $size="sm" $variant="ghost" onClick={() => setSaveModal({ kind: 'analyses', asNew: true })}><Copy size={13} /> {t('explorer.saveAs')}</Button>}
          {canDeleteAnalysis && <Button $size="sm" $variant="ghost" onClick={() => void removeAnalysis()} style={{ color: colors.red.main }}><Trash2 size={13} /> {t('common.delete')}</Button>}
          <Button $size="sm" $variant="ghost" onClick={() => void exportFile('xlsx')} disabled={def.rows.length === 0 && def.measures.length === 0} title={t('explorer.exportExcel')}><FileDown size={13} /> Excel</Button>
          <Button $size="sm" $variant="ghost" onClick={() => void exportFile('pdf')} disabled={def.rows.length === 0 && def.measures.length === 0} title={t('explorer.exportPdf')}><FileDown size={13} /> PDF</Button>
        </>
      ) : (
        <>
          <div style={{ width: 300 }}>
            <SearchSelect value={currentDashboard?.id ?? ''} options={savedOptions(dashboards, t('explorer.mine'))} anyLabel={t('explorer.newDashboard')} placeholder={t('explorer.pickDashboard')}
              onChange={(v) => loadDashboard(dashboards.find((d) => d.id === v) ?? null)} />
          </div>
          <Button $size="sm" $variant={dashEditing ? 'primary' : 'ghost'} onClick={() => setDashEditing((v) => !v)}><Pencil size={13} /> {t('explorer.editLayout')}</Button>
          <Button $size="sm" $variant="ghost" onClick={() => setSaveModal({ kind: 'dashboards', asNew: false })} disabled={dashDef.tiles.length === 0}><Save size={13} /> {t('common.save')}</Button>
          {currentDashboard && <Button $size="sm" $variant="ghost" onClick={() => setSaveModal({ kind: 'dashboards', asNew: true })}><Copy size={13} /> {t('explorer.saveAs')}</Button>}
          {canDeleteDashboard && <Button $size="sm" $variant="ghost" onClick={() => void removeDashboard()} style={{ color: colors.red.main }}><Trash2 size={13} /> {t('common.delete')}</Button>}
          {currentDashboard && <Button $size="sm" $variant="ghost" onClick={() => void exportFile('xlsx')} title={t('explorer.exportExcel')}><FileDown size={13} /> Excel</Button>}
          {currentDashboard && <Button $size="sm" $variant="ghost" onClick={() => void exportFile('pdf')} title={t('explorer.exportPdf')}><FileDown size={13} /> PDF</Button>}
        </>
      )}
    </ModeBar>
  )

  return (
    <PageLayout icon={<Compass size={18} />} title={t('explorer.title')} description={<span>{model.label}{model.description ? ` — ${model.description}` : ''}</span>}
      headerRight={models && models.length > 0 ? (
        <div style={{ width: 280 }}><SearchSelect value={`${app}/${modelId}`} options={models.map((m) => ({ value: `${m.app}/${m.model}`, label: m.label, mono: `${m.app} · ${m.model}` }))}
          onChange={(v) => { const [a, m] = v.split('/'); if (a && m) { setApp(a); setModelId(m) } }} /></div>
      ) : undefined}>
      {model.warnings.length > 0 && user?.is_superuser && <Banner $tone="warning" style={{ marginBottom: 8 }}>{model.warnings.join(' · ')}</Banner>}
      {modeBar}
      {mode === 'dashboards' ? (
        <>
          {error && <Banner $tone="error">{error}</Banner>}
          <DashboardsView app={app} model={modelId} definition={dashDef} analyses={analyses} editing={dashEditing} lang={lang} onChange={setDashDef}
            onOpenAnalysis={(a, cells) => loadAnalysis(a, cells)} />
        </>
      ) : (
        <Body>
          <FieldsPane entities={entities} primary={def.entity} used={used}
            onAdd={(f) => setDef(addField(def, f))} onFilter={(f) => setDef(addFilter(def, f))}
            onPrimary={(e) => setDef({ ...emptyDefinition(e), view: def.view })}
            onCalcNew={() => setCalcEditing(null)} onCalcEdit={(name) => setCalcEditing(def.calculated.find((c) => c.name === name) ?? null)} onCalcDelete={(name) => void deleteCalc(name)} />
          <Canvas>
            <Toolbar>
              <div style={{ width: 180 }}><SearchSelect value={def.entity} options={entityOpts} onChange={(e) => setDef({ ...emptyDefinition(e), view: def.view })} /></div>
              <Seg>
                {AGG_VIEWS.map((v) => <SegBtn key={v} type="button" $active={def.view === v} onClick={() => setView(v)} title={t(`explorer.view.${v}`)}>{viewIcon[v]}</SegBtn>)}
              </Seg>
              <Seg><SegBtn type="button" $active={def.view === 'detail'} onClick={() => setView('detail')}>{viewIcon.detail} {t('explorer.view.detail')}</SegBtn></Seg>
              {isAgg(def.view) && (
                <>
                  <Note>{t('explorer.topN')}</Note>
                  <div style={{ width: 90 }}><Input type="number" min={1} max={10000} value={def.limit} onChange={(e) => setDef({ ...def, limit: Math.max(1, Math.min(10000, Number(e.target.value) || 1)) })} /></div>
                </>
              )}
              <div style={{ flex: 1 }} />
              {running && <SpinnerRing size={14} thickness={2} />}
              {result && !showingDetail && (
                <Note>{t('explorer.groupsNote', { groups: result.groups, scanned: result.scanned })}{result.truncated ? ` · ${t('explorer.truncated', { limit: def.limit })}` : ''}{result.scan_truncated ? ` · ${t('explorer.scanTruncated')}` : ''}</Note>
              )}
              {user?.is_superuser && sql && <Button $size="sm" $variant="ghost" onClick={() => setShowSql((v) => !v)} title="SQL"><Code2 size={13} /></Button>}
              <Button $size="sm" $variant="primary" onClick={() => void run(def, { drill, page })} disabled={running}><Play size={13} /> {t('explorer.run')}</Button>
            </Toolbar>
            {(def.view === 'pivot' || ['bar', 'line', 'pie'].includes(def.view)) && !showingDetail && (
              <Options>
                <span>{t('explorer.measure')}</span>
                <div style={{ width: 200 }}><SearchSelect value={(def.view === 'pivot' ? def.pivot?.measure : def.chart?.measure) ?? ''} options={measureOpts} anyLabel={t('explorer.firstMeasure')}
                  onChange={(v) => setDef(def.view === 'pivot' ? { ...def, pivot: { ...def.pivot, measure: v || null } } : { ...def, chart: { ...def.chart, measure: v || null } })} /></div>
                <span>{def.view === 'pivot' ? t('explorer.pivotColumn') : t('explorer.series')}</span>
                <div style={{ width: 200 }}><SearchSelect value={def.pivotColumn ?? ''} options={dimOpts} anyLabel={t('explorer.auto')} onChange={(v) => setDef({ ...def, pivotColumn: v || null })} /></div>
                {def.view === 'pivot' ? (
                  <>
                    <span>{t('explorer.share')}</span>
                    <Seg>
                      {(['none', 'parent', 'total'] as const).map((p) => <SegBtn key={p} type="button" $active={(def.pivot?.percent ?? 'none') === p} onClick={() => setDef({ ...def, pivot: { ...def.pivot, percent: p } })}>{t(`explorer.shareOf.${p}`)}</SegBtn>)}
                    </Seg>
                  </>
                ) : def.view !== 'pie' ? (
                  <Checkbox checked={!!def.chart?.stacked} onChange={(v) => setDef({ ...def, chart: { ...def.chart, stacked: v } })} label={t('explorer.stacked')} />
                ) : null}
              </Options>
            )}
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
            {showingDetail ? (
              detail ? (
                <ResultTable columns={detail.columns} rows={detail.rows} lang={lang} paging={{ page, pageSize: PAGE_SIZE, total: detail.total, onPage }} onRowClick={canOpenRecord ? openRecord : undefined} />
              ) : <Empty>{running ? t('common.loading', 'Loading…') : t('explorer.detailHint')}</Empty>
            ) : !result ? (
              <Empty>{t('explorer.emptyHint')}</Empty>
            ) : def.view === 'table' ? (
              <ResultTable columns={result.columns} rows={result.rows} lang={lang} onRowClick={def.rows.length > 0 ? onDrill : undefined} />
            ) : def.view === 'pivot' ? (
              <PivotView result={result} measureKey={def.pivot?.measure ?? null} pivotKey={def.pivotColumn ?? null} percent={def.pivot?.percent ?? 'none'} lang={lang} onDrill={onDrill} />
            ) : (
              <ChartView result={result} view={def.view as 'bar' | 'line' | 'pie' | 'kpi'} measureKey={def.chart?.measure ?? null} seriesKey={def.pivotColumn ?? null} stacked={!!def.chart?.stacked} lang={lang} connector={app} />
            )}
          </Canvas>
        </Body>
      )}
      {calcEditing !== undefined && (
        <CalcModal app={app} model={modelId} entity={def.entity} primary={def.entity} entities={entities} calculated={def.calculated} initial={calcEditing} onSave={saveCalc} onClose={() => setCalcEditing(undefined)} />
      )}
      {record && recordEntity && (
        <RecordDialog app={app} entity={recordEntity} row={record.row} onClose={() => setRecord(null)} onSaved={() => void run(def, { drill, page })} />
      )}
      {saveModal && (
        <SaveModal kind={saveModal.kind} asNew={saveModal.asNew} current={saveModal.kind === 'analyses' ? currentAnalysis : currentDashboard}
          onSave={(f) => (saveModal.kind === 'analyses' ? saveAnalysis(f, saveModal.asNew) : saveDashboard(f, saveModal.asNew))} onClose={() => setSaveModal(null)} />
      )}
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
