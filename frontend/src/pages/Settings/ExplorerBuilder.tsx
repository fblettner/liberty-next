// Explorer settings — `config/explorer.toml`, the models of an app: the hub entity, the entities around it
// (each a query or a table with a cardinality, a key, measures) and the relations joining them.
// A scope bar picks the app, a picker its model. The model is DRAWN (ExplorerDiagram: the hub in the middle,
// the details on its left, its dimensions on its right, what does not reach the hub set apart); a click on a
// card or on a line picks it and the panel beside edits it — the model's own settings when nothing is picked.
// Saved whole (PUT /admin/config/explorer/parsed + reload). An entity's columns open ExplorerEntityModal.
import { useCallback, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Plus, Trash2, Edit3, Database, Link2, Save, Boxes, Star, ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, Field, Input, SearchSelect, SpinnerRing, Tag, useModals, type SearchSelectOption, HeaderActions } from '../../common'
import type { ExplorerDoc, ExplorerEntityBody, ExplorerModelBody, ExplorerRelationBody } from '../../types/config'
import type { QueryResult } from '../../types/connectors'
import { ExplorerEntityModal, type ExplorerEntityRecord } from './ExplorerEntityModal'
import { ScopeBar, type ScopeOption } from './ScopeBar'
import { AddScopeModal } from './AddScopeModal'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import { BuilderShell as Shell, BuilderToolbar as Toolbar } from '../../common/builderShell'
import { FlowCanvas } from './flowKit'
import ExplorerDiagram, { type DiagramPick } from './ExplorerDiagram'
import { modelDiagram } from '../../services/explorerDiagram'
import { useDirtyGuard } from '../../tabs/dirtyGuard'

type Models = Record<string, Record<string, ExplorerModelBody>>   // app → model id → body

// the diagram on the left, the picked thing's settings beside it
const Split = styled.div`flex: 1; min-height: 0; display: flex; gap: 14px; align-items: stretch;`
const CanvasSide = styled.div`flex: 1; min-width: 0; min-height: 0; display: flex; flex-direction: column; gap: 8px;`
const CanvasBar = styled.div`
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap; flex-shrink: 0; font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};
  & .spacer { flex: 1; }
  & .warn { color: ${colors.orange.main}; }
`
const SidePanel = styled(Card)`flex: 0 0 min(420px, 36%); min-width: 0; min-height: 0; padding: 0; display: flex; flex-direction: column; overflow: hidden;`
const SideHead = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input}; flex-wrap: wrap;
  & > svg { color: ${colors.blue.main}; flex-shrink: 0; }
  & .name { flex: 1; min-width: 80px; font-weight: 600; color: ${colors.text.primary}; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`
const SideBody = styled.div`flex: 1; min-height: 0; overflow-y: auto; padding: 14px; display: flex; flex-direction: column; gap: 12px;`
const Facts = styled.div`
  display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 5px 12px; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & .k { color: ${colors.text.muted}; }
  & .v { color: ${colors.text.primary}; overflow-wrap: anywhere; }
  & .v.mono { font-family: ${fonts.mono}; }
`
const SubTitle = styled.div`font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: ${colors.text.muted};`
const RelLink = styled.button`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 10px; text-align: left; cursor: pointer; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.primary};
  & .on { flex: 1; min-width: 0; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: ${fontSize.micro}; }
  &:hover { border-color: ${colors.blue.border}; }
`
const StartBox = styled.div`
  max-width: 560px; margin: 24px auto; display: flex; flex-direction: column; gap: 12px; font-family: ${fonts.sans};
  & .title { font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; }
  & .way { display: flex; gap: 12px; padding: 12px 14px; border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: var(--bg-modal); }
  & .way > svg { flex-shrink: 0; color: ${colors.blue.main}; margin-top: 2px; }
  & .way .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; font-size: ${fontSize.sm}; color: ${colors.text.muted}; }
  & .way strong { color: ${colors.text.primary}; font-weight: 600; }
`
const PresetNote = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans}; padding: 0 2px;`
const RelRow = styled.div`display: grid; grid-template-columns: 1fr auto 1fr; gap: 8px; align-items: center;`
const Pairs = styled.div`display: flex; flex-direction: column; gap: 6px;`
const PairRow = styled.div`display: grid; grid-template-columns: 1fr auto 1fr auto; gap: 8px; align-items: center;`
const Arrow = styled.span`color: ${colors.text.muted}; font-family: ${fonts.mono};`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 12px 4px; text-align: center;`


export default function ExplorerBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { focusApp: currentApp, connectors, refresh: refreshWorkspace } = useWorkspace()
  const [scope, setScope] = useState('')
  const [modelId, setModelId] = useState('')
  const [doc, setDoc] = useState<Models | null>(null)
  const [saved, setSaved] = useState<Models | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [addScopeOpen, setAddScopeOpen] = useState(false)
  const [editing, setEditing] = useState<ExplorerEntityRecord | null | undefined>(undefined)
  const [pick, setPick] = useState<DiagramPick>(null)
  // where Back returns from a relation: the entity it was opened from (else the model)
  const [cameFrom, setCameFrom] = useState<string | null>(null)
  const [presets, setPresets] = useState<{ id: string; label?: string; connector?: string; tables?: { name: string }[] }[]>([])
  // Column names per entity query (`connector.query` → names), discovered once for the relation pickers.
  const [entityColumns, setEntityColumns] = useState<Record<string, string[]>>({})

  const load = () => {
    setError(null)
    api.get<ExplorerDoc>('/admin/config/explorer/parsed')
      .then((d) => { setDoc(d.explorer); setSaved(d.explorer) })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  const scopes = useMemo<ScopeOption[]>(() => Object.keys(doc ?? {}).sort().map((v) => ({ value: v, label: v })), [doc])
  // Every Screen Assistant preset — a preset is declared on a CONNECTOR (jdedwards), a model on an
  // APP (nomajde), so no filter: the option shows the preset's connector.
  useEffect(() => {
    api.get<{ presets: { id: string; label?: string; connector?: string; tables?: { name: string }[] }[] }>('/admin/assistant/presets')
      .then((r) => setPresets(r.presets)).catch(() => setPresets([]))
  }, [])
  useEffect(() => {
    if (!doc) return
    setScope((cur) => (cur && scopes.some((s) => s.value === cur)) ? cur : (currentApp && scopes.some((s) => s.value === currentApp)) ? currentApp : (scopes[0]?.value ?? ''))
  }, [doc, scopes, currentApp])
  const addCandidates = useMemo(() => {
    const have = new Set(Object.keys(doc ?? {}))
    return (connectors ?? []).filter((c) => c.type === 'sql' && !have.has(c.name)).map((c) => c.name).sort()
  }, [doc, connectors])

  const modelIds = useMemo(() => Object.keys(doc?.[scope] ?? {}).sort(), [doc, scope])
  useEffect(() => { setModelId((cur) => (cur && modelIds.includes(cur) ? cur : (modelIds[0] ?? ''))) }, [modelIds])
  const model = modelId ? doc?.[scope]?.[modelId] : undefined
  useEffect(() => { setPick(null); setCameFrom(null) }, [scope, modelId])      // another model: nothing of it is picked yet
  const columnsOf = useCallback((entityId: string): string[] => {
    const e = model?.entities[entityId]
    return e ? (entityColumns[`${e.connector || scope}.${e.query}`] ?? []) : []
  }, [model, entityColumns, scope])
  useEffect(() => {
    if (!model) return
    for (const e of Object.values(model.entities)) {
      const k = `${e.connector || scope}.${e.query}`
      if (!e.query || k in entityColumns) continue   // a table entity's columns are discovered by the model, not here
      setEntityColumns((p) => ({ ...p, [k]: [] }))
      api.get<QueryResult>(`/api/sql/${encodeURIComponent(e.connector || scope)}/${encodeURIComponent(e.query)}?_limit=1`)
        .then((r) => setEntityColumns((p) => ({ ...p, [k]: r.columns.map((c) => c.name.toUpperCase()) })))
        .catch(() => { /* the picker stays free-text for that entity */ })
    }
  }, [model, scope, entityColumns])
  const dirty = JSON.stringify(doc) !== JSON.stringify(saved)
  useDirtyGuard(dirty)          // the shell asks before a closed tab or a reload loses these edits
  const update = (patch: Partial<ExplorerModelBody>) => setDoc((p) => (p && p[scope]?.[modelId] ? { ...p, [scope]: { ...p[scope], [modelId]: { ...p[scope][modelId], ...patch } } } : p))
  const entityIds = Object.keys(model?.entities ?? {})
  const presetTables = useMemo(() => (model?.preset ? (presets.find((p) => p.id === model.preset)?.tables ?? []).map((x) => x.name.toLowerCase()) : []), [model?.preset, presets])
  const entityOpts: SearchSelectOption[] = [...new Set([...entityIds, ...presetTables])].map((e) => ({ value: e, label: model?.entities[e]?.label || e, mono: e }))

  const persist = async () => {
    if (!doc) return
    setBusy(true); setError(null); setStatus(null)
    const pruned: Models = {}
    for (const [app, byId] of Object.entries(doc)) {
      const kept = Object.entries(byId).filter(([, m]) => Object.keys(m.entities ?? {}).length > 0)
        .map(([id, m]) => [id, { ...m, relations: (m.relations ?? []).map((r) => ({ ...r, on: (r.on ?? []).filter((p) => p[0] && p[1]) })) }] as const)
      if (kept.length) pruned[app] = Object.fromEntries(kept)
    }
    try {
      await api.put('/admin/config/explorer/parsed', { explorer: pruned })
      await api.post('/admin/reload')
      refreshWorkspace()        // the Explorer page's model list comes from the workspace
      setSaved(doc)
      setStatus(t('settings.explorer.saved'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const onEntitySave = (id: string, body: ExplorerEntityBody) => {
    if (!model) return
    const entities = { ...model.entities, [id]: body }
    update({ entities, hub: model.hub || id })
    setEditing(undefined)
  }
  const removeEntity = async (id: string) => {
    if (!model) return
    if (!(await modals.confirm({ title: t('common.delete'), message: t('settings.explorer.deleteEntity', { id }), variant: 'danger' }))) return
    const entities = { ...model.entities }
    delete entities[id]
    update({ entities, relations: (model.relations ?? []).filter((r) => r.from !== id && r.to !== id), hub: model.hub === id ? (Object.keys(entities)[0] ?? '') : model.hub })
  }
  const addModel = async () => {
    const id = (await modals.prompt({ title: t('settings.explorer.addModel'), message: t('settings.explorer.modelPrompt'), submitLabel: t('common.ok'),
      validate: (v) => (!/^[A-Za-z0-9_-]+$/.test(v.trim()) ? { error: t('settings.explorer.idChars') } : modelIds.includes(v.trim()) ? { error: t('settings.explorer.idTaken') } : null) }))?.trim()
    if (!id) return
    setDoc((p) => ({ ...(p ?? {}), [scope]: { ...(p?.[scope] ?? {}), [id]: { hub: '', entities: {}, relations: [] } } }))
    setModelId(id)
  }
  /** Picking a preset fills the model: its tables as entities, the base table as hub, its joins as
   *  relations resolved on the real columns (POST /admin/explorer/from-preset). Entities already
   *  declared are kept and completed; an existing hub / relations are kept too. */
  const applyPreset = async (presetId: string) => {
    if (!model) return
    if (!presetId) { update({ preset: undefined }); return }
    if (entityIds.length > 0 && !(await modals.confirm({ title: t('settings.explorer.preset'), message: t('settings.explorer.applyPresetConfirm', { preset: presetId }) }))) return
    setBusy(true); setError(null); setStatus(null)
    try {
      const r = await api.post<{ model: ExplorerModelBody; warnings: string[]; dictionary?: { created: string[]; skipped: string[] } }>('/admin/explorer/from-preset', { app: scope, preset: presetId, model })
      update({ ...r.model, preset: presetId })
      const dict = r.dictionary && r.dictionary.created.length ? t('settings.explorer.presetDictionary', { n: r.dictionary.created.length }) : ''
      setStatus((r.warnings.length ? r.warnings.join(' · ') : t('settings.explorer.presetApplied', { n: Object.keys(r.model.entities ?? {}).length })) + dict)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }
  const removeModel = async () => {
    if (!modelId || !(await modals.confirm({ title: t('common.delete'), message: t('settings.explorer.deleteModel', { id: modelId }), variant: 'danger' }))) return
    setDoc((p) => { if (!p) return p; const byId = { ...p[scope] }; delete byId[modelId]; return { ...p, [scope]: byId } })
  }
  const setRelation = (i: number, patch: Partial<ExplorerRelationBody>) => update({ relations: (model?.relations ?? []).map((r, k) => (k === i ? { ...r, ...patch } : r)) })

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Centered />

  return (
    <Shell>
      <ScopeBar scopes={scopes} value={scope} onChange={setScope} label={t('settings.explorer.app')}
        emptyHint={t('settings.explorer.noScopes')} onAddScope={() => setAddScopeOpen(true)} />
      <Toolbar>
        <Boxes size={14} style={{ color: colors.text.muted }} />
        <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{t('settings.explorer.model')}</span>
        <div style={{ width: 240 }}>
          <SearchSelect sort value={modelId} onChange={setModelId} options={modelIds.map((id) => ({ value: id, label: doc?.[scope]?.[id]?.label || id, mono: id }))} placeholder={t('settings.explorer.noModel')} disabled={!scope} />
        </div>
      </Toolbar>
      {/* the model's buttons are in the page header, before Reload config */}
      <HeaderActions>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        {busy && <SpinnerRing size={14} thickness={2} />}
        <Button $variant="ghost" $size="sm" onClick={() => void addModel()} disabled={busy || !scope}><Plus size={13} /> {t('settings.explorer.addModel')}</Button>
        {modelId && <Button $variant="ghost" $size="sm" onClick={() => void removeModel()} disabled={busy} style={{ color: colors.red.main }}><Trash2 size={13} /> {t('settings.explorer.deleteModelBtn')}</Button>}
        <Button $variant="primary" $size="sm" onClick={() => void persist()} disabled={busy || !dirty}><Save size={13} /> {t('common.save')}</Button>
      </HeaderActions>
      {status && <Banner $tone="ok">{status}</Banner>}
      {error && <Banner $tone="error">{error}</Banner>}
      {scope && modelIds.length === 0 && <Empty>{t('settings.explorer.noModels')}</Empty>}
      {model && (() => {
        const diagram = modelDiagram(model)
        const relations = model.relations ?? []
        const pickedEntity = pick?.kind === 'entity' && model.entities[pick.id] ? pick.id : null
        const pickedRelation = pick?.kind === 'relation' && relations[pick.index] ? pick.index : null
        const unfinished = diagram.edges.filter((e) => !e.complete).length
        const addRelation = (from?: string) => {
          const a = from ?? entityIds.find((x) => x !== model.hub) ?? entityIds[0]
          const b = a === model.hub ? (entityIds.find((x) => x !== model.hub) ?? model.hub) : model.hub
          update({ relations: [...relations, { from: a, to: b, on: [] }] })
          setPick({ kind: 'relation', index: relations.length })
        }
        const pairsText = (r: ExplorerRelationBody) => (r.on ?? []).filter((p) => p[0] && p[1]).map((p) => (p[0] === p[1] ? p[0] : `${p[0]} = ${p[1]}`)).join(', ')
        return (
          <Split>
            <CanvasSide>
              <CanvasBar>
                <Button $variant="ghost" $size="sm" onClick={() => setEditing(null)} disabled={busy}><Plus size={13} /> {t('settings.explorer.addEntity')}</Button>
                <Button $variant="ghost" $size="sm" onClick={() => addRelation()} disabled={busy || entityIds.length < 2}><Link2 size={13} /> {t('settings.explorer.addRelation')}</Button>
                <span className="spacer" />
                <span>{t('settings.explorer.diagram.counts', { entities: entityIds.length, relations: relations.length })}</span>
                {diagram.loose.length > 0 && <span className="warn">· {t('settings.explorer.diagram.looseCount', { n: diagram.loose.length })}</span>}
                {unfinished > 0 && <span className="warn">· {t('settings.explorer.diagram.unfinished', { n: unfinished })}</span>}
              </CanvasBar>
              <FlowCanvas zoomKey="explorer">
                {entityIds.length === 0
                  ? (
                    // an empty model: the two ways to start — a prebuilt one (a preset: its tables, hub and joins), or by hand
                    <StartBox data-start>
                      <div className="title">{t('settings.explorer.start.title')}</div>
                      <div className="way">
                        <Boxes size={16} />
                        <div className="text">
                          <strong>{t('settings.explorer.start.preset')}</strong>
                          <span>{t('settings.explorer.start.presetHint')}</span>
                          <div style={{ maxWidth: 380 }}>
                            <SearchSelect sort value={model.preset ?? ''} placeholder={t('settings.explorer.start.pickPreset')} disabled={busy || presets.length === 0}
                              options={presets.map((p) => ({ value: p.id, label: p.label || p.id, mono: `${p.connector ? `${p.connector} · ` : ''}${(p.tables ?? []).map((x) => x.name).join(', ')}` }))}
                              onChange={(v) => void applyPreset(v)} />
                          </div>
                          {presets.length === 0 && <span>{t('settings.explorer.start.noPreset')}</span>}
                        </div>
                      </div>
                      <div className="way">
                        <Database size={16} />
                        <div className="text">
                          <strong>{t('settings.explorer.start.byHand')}</strong>
                          <span>{t('settings.explorer.noEntities')}</span>
                          <Button $variant="ghost" $size="sm" style={{ alignSelf: 'flex-start' }} onClick={() => setEditing(null)} disabled={busy}><Plus size={13} /> {t('settings.explorer.addEntity')}</Button>
                        </div>
                      </div>
                    </StartBox>
                  )
                  : <ExplorerDiagram model={model} app={scope} picked={pick} onPick={(p) => { setCameFrom(null); setPick(p) }} />}
              </FlowCanvas>
            </CanvasSide>
            <SidePanel>
              {pickedEntity ? (() => {
                const id = pickedEntity, e = model.entities[id]
                const measures = Object.entries(e.measures ?? {})
                const mine = relations.map((r, index) => ({ r, index })).filter(({ r }) => r.from === id || r.to === id)
                return (
                  <>
                    <SideHead>
                      <Button $variant="ghost" $size="sm" data-back onClick={() => { setCameFrom(null); setPick(null) }}><ArrowLeft size={13} /> {modelId}</Button>
                      {model.hub === id ? <Star size={15} /> : <Database size={15} />}
                      <span className="name">{id}</span>
                      {model.hub === id ? <Tag $tone="blue">{t('settings.explorer.hub')}</Tag> : <Tag $tone={e.cardinality === 'many' ? 'orange' : 'neutral'}>{e.cardinality === 'many' ? '1..n' : '1..1'}</Tag>}
                    </SideHead>
                    <SideBody data-side="entity">
                      <Facts>
                        {e.label && <><span className="k">{t('settings.explorer.label')}</span><span className="v">{e.label}</span></>}
                        <span className="k">{e.query ? t('settings.explorer.query') : t('settings.explorer.table')}</span>
                        <span className="v mono">{e.query ? `${e.connector || scope}.${e.query}` : `${e.schema ? `${e.schema}.` : ''}${e.table ?? ''}`}</span>
                        {(e.key?.length ?? 0) > 0 && <><span className="k">{t('settings.explorer.key')}</span><span className="v mono">{e.key?.join(', ')}</span></>}
                        {measures.length > 0 && <><span className="k">{t('settings.explorer.diagram.measures')}</span><span className="v mono">{measures.map(([k]) => k).join(', ')}</span></>}
                        {e.default_date && <><span className="k">{t('settings.explorer.defaultDate')}</span><span className="v mono">{e.default_date}</span></>}
                        {e.screen && <><span className="k">{t('settings.explorer.screen')}</span><span className="v mono">{e.screen}</span></>}
                      </Facts>
                      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        <Button $variant="primary" $size="sm" onClick={() => setEditing({ ...e, id })} disabled={busy}><Edit3 size={13} /> {t('common.edit')}</Button>
                        {model.hub !== id && <Button $variant="ghost" $size="sm" onClick={() => update({ hub: id })} disabled={busy}><Star size={13} /> {t('settings.explorer.diagram.makeHub')}</Button>}
                        <Button $variant="ghost" $size="sm" onClick={() => void removeEntity(id).then(() => setPick(null))} disabled={busy} style={{ color: colors.red.main }}><Trash2 size={13} /> {t('common.delete')}</Button>
                      </div>
                      <SubTitle>{t('settings.explorer.relations')}</SubTitle>
                      {mine.length === 0 && <Empty style={{ padding: '4px 0', textAlign: 'left' }}>{model.hub === id ? t('settings.explorer.noRelations') : t('settings.explorer.diagram.notLinked')}</Empty>}
                      {mine.map(({ r, index }) => (
                        <RelLink key={index} type="button" onClick={() => { setCameFrom(id); setPick({ kind: 'relation', index }) }}>
                          <Link2 size={13} /> {r.from === id ? r.to : r.from}<span className="on">{pairsText(r) || t('settings.explorer.diagram.noPair')}</span>
                        </RelLink>
                      ))}
                      <Button $variant="ghost" $size="sm" style={{ alignSelf: 'flex-start' }} onClick={() => { setCameFrom(id); addRelation(id) }} disabled={busy || entityIds.length < 2}>
                        <Plus size={13} /> {t('settings.explorer.addRelation')}
                      </Button>
                    </SideBody>
                  </>
                )
              })() : pickedRelation != null ? (() => {
                const i = pickedRelation, r = relations[i]
                const fromCols = columnsOf(r.from).map((c) => ({ value: c, label: c }))
                const toCols = columnsOf(r.to).map((c) => ({ value: c, label: c }))
                const pairs = (r.on ?? []).length ? r.on : [['', '']]
                const setPair = (k: number, side: 0 | 1, v: string) => setRelation(i, { on: pairs.map((p, j) => (j === k ? (side === 0 ? [v, p[1]] : [p[0], v]) : p)) })
                // Back: to the entity this relation was opened from when it still exists, else to the model
                const backEntity = cameFrom && model.entities[cameFrom] ? cameFrom : null
                const goBack = () => { setCameFrom(null); setPick(backEntity ? { kind: 'entity', id: backEntity } : null) }
                return (
                  <>
                    <SideHead>
                      <Button $variant="ghost" $size="sm" data-back onClick={goBack}><ArrowLeft size={13} /> {backEntity ?? modelId}</Button>
                      <Link2 size={15} />
                      <span className="name">{r.from} → {r.to}</span>
                    </SideHead>
                    <SideBody data-side="relation">
                      <RelRow>
                        <SearchSelect value={r.from} onChange={(v) => setRelation(i, { from: v, on: [] })} options={entityOpts} />
                        <Arrow>→</Arrow>
                        <SearchSelect value={r.to} onChange={(v) => setRelation(i, { to: v, on: [] })} options={entityOpts} />
                      </RelRow>
                      <SubTitle>{t('settings.explorer.diagram.joinedOn')}</SubTitle>
                      <Pairs>
                        {pairs.map((p, k) => (
                          <PairRow key={k}>
                            <SearchSelect value={p[0]} onChange={(v) => setPair(k, 0, v.toUpperCase())} options={fromCols} allowCustom placeholder={t('settings.explorer.columnOf', { entity: r.from })} />
                            <Arrow>=</Arrow>
                            <SearchSelect value={p[1]} onChange={(v) => setPair(k, 1, v.toUpperCase())} options={toCols} allowCustom placeholder={t('settings.explorer.columnOf', { entity: r.to })} />
                            <span style={{ display: 'flex', gap: 2 }}>
                              {k === pairs.length - 1 && <Button $variant="ghost" $size="sm" onClick={() => setRelation(i, { on: [...pairs, ['', '']] })}><Plus size={13} /></Button>}
                              {pairs.length > 1 && <Button $variant="ghost" $size="sm" onClick={() => setRelation(i, { on: pairs.filter((_, j) => j !== k) })}><Trash2 size={13} /></Button>}
                            </span>
                          </PairRow>
                        ))}
                      </Pairs>
                      <Button $variant="ghost" $size="sm" style={{ alignSelf: 'flex-start', color: colors.red.main }}
                        onClick={() => { update({ relations: relations.filter((_, k) => k !== i) }); goBack() }}>
                        <Trash2 size={13} /> {t('settings.explorer.diagram.deleteRelation')}
                      </Button>
                    </SideBody>
                  </>
                )
              })() : (
                <>
                  <SideHead><Boxes size={15} /><span className="name">{modelId}</span></SideHead>
                  <SideBody data-side="model">
                    <Field label={t('settings.explorer.label')}><Input value={model.label ?? ''} onChange={(e) => update({ label: e.target.value || undefined })} placeholder={scope} /></Field>
                    <Field label={t('settings.explorer.hub')}><SearchSelect value={model.hub ?? ''} onChange={(v) => update({ hub: v })} options={entityOpts} placeholder={t('settings.access.pickItem')} /></Field>
                    <Field label={t('settings.explorer.description')}><Input value={model.description ?? ''} onChange={(e) => update({ description: e.target.value || undefined })} /></Field>
                    <Field label={t('settings.explorer.preset')}>
                      <SearchSelect sort value={model.preset ?? ''} anyLabel={t('settings.explorer.noPreset')} options={presets.map((p) => ({ value: p.id, label: p.label || p.id, mono: `${p.connector ? `${p.connector} · ` : ''}${(p.tables ?? []).map((x) => x.name).join(', ')}` }))}
                        onChange={(v) => void applyPreset(v)} disabled={busy} />
                    </Field>
                    {model.preset && <PresetNote>{t('settings.explorer.presetNote', { preset: model.preset })}</PresetNote>}
                    <PresetNote>{t('settings.explorer.diagram.hint')}</PresetNote>
                  </SideBody>
                </>
              )}
            </SidePanel>
          </Split>
        )
      })()}
      {editing !== undefined && model && (
        <ExplorerEntityModal initial={editing} app={scope} takenIds={entityIds.filter((x) => x !== editing?.id)} onSave={onEntitySave} onClose={() => setEditing(undefined)} />
      )}
      {addScopeOpen && (
        <AddScopeModal candidates={addCandidates} title={t('settings.explorer.addScope')} emptyHint={t('settings.explorer.addScopeEmpty')}
          onPick={(c) => { setDoc((p) => ({ ...(p ?? {}), [c]: p?.[c] ?? {} })); setScope(c); setStatus(null) }}
          onClose={() => setAddScopeOpen(false)} />
      )}
    </Shell>
  )
}
