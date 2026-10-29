// Explorer settings — `config/explorer.toml`, one model per app: the hub entity, the entities
// around it (each a query with a cardinality, a key, measures) and the relations joining them.
// A scope bar picks the app; the page edits that app's model in place and saves it whole
// (PUT /admin/config/explorer/parsed + reload). Entities open ExplorerEntityModal.
import { useCallback, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Plus, Trash2, Edit3, Database, Link2, Save, Boxes } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Centered, Field, Input, SearchSelect, SpinnerRing, Tag, useModals, type SearchSelectOption } from '../../common'
import type { ExplorerDoc, ExplorerEntityBody, ExplorerModelBody, ExplorerRelationBody } from '../../types/config'
import type { QueryResult } from '../../types/connectors'
import { ExplorerEntityModal, type ExplorerEntityRecord } from './ExplorerEntityModal'
import { ScopeBar, type ScopeOption } from './ScopeBar'
import { AddScopeModal } from './AddScopeModal'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import { BuilderShell as Shell, BuilderToolbar as Toolbar } from '../../common/builderShell'

type Models = Record<string, Record<string, ExplorerModelBody>>   // app → model id → body

const Body = styled.div`flex: 1; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 12px;`
const Head = styled(Card)`display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 12px; padding: 12px 14px;`
const PresetNote = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans}; padding: 0 2px;`
const Section = styled(Card)`padding: 10px 14px; display: flex; flex-direction: column; gap: 6px;`
const SectionHead = styled.div`display: flex; align-items: center; gap: 8px; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; & .spacer { flex: 1; }`
const Item = styled.div`
  display: flex; align-items: center; gap: 12px; padding: 8px 12px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; cursor: pointer; min-width: 0;
  & > .icon { flex-shrink: 0; color: ${colors.blue.main}; }
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 2px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.base}; color: ${colors.text.primary}; }
  & .meta { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .actions { flex-shrink: 0; display: flex; gap: 4px; }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`
const RelRow = styled.div`display: grid; grid-template-columns: 1fr auto 1fr auto; gap: 8px; align-items: start; padding: 6px 0; border-bottom: 1px solid ${colors.border}; &:last-child { border-bottom: 0; }`
const Pairs = styled.div`grid-column: 1 / -1; display: flex; flex-direction: column; gap: 6px; padding-left: 12px;`
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
        <Button $variant="ghost" $size="sm" onClick={() => void addModel()} disabled={busy || !scope}><Plus size={13} /> {t('settings.explorer.addModel')}</Button>
        {modelId && <Button $variant="ghost" $size="sm" onClick={() => void removeModel()} disabled={busy} style={{ color: colors.red.main }}><Trash2 size={13} /> {t('settings.explorer.deleteModelBtn')}</Button>}
        <span style={{ flex: 1 }} />
        {busy && <SpinnerRing size={14} thickness={2} />}
        {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
        {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        <Button $variant="primary" $size="sm" onClick={() => void persist()} disabled={busy || !dirty}><Save size={13} /> {t('common.save')}</Button>
      </Toolbar>
      {scope && modelIds.length === 0 && <Empty>{t('settings.explorer.noModels')}</Empty>}
      {model && (
        <Body>
          <Head>
            <Field label={t('settings.explorer.label')}><Input value={model.label ?? ''} onChange={(e) => update({ label: e.target.value || undefined })} placeholder={scope} /></Field>
            <Field label={t('settings.explorer.hub')}><SearchSelect value={model.hub ?? ''} onChange={(v) => update({ hub: v })} options={entityOpts} placeholder={t('settings.access.pickItem')} /></Field>
            <Field label={t('settings.explorer.description')}><Input value={model.description ?? ''} onChange={(e) => update({ description: e.target.value || undefined })} /></Field>
            <Field label={t('settings.explorer.preset')}>
              <SearchSelect sort value={model.preset ?? ''} anyLabel={t('settings.explorer.noPreset')} options={presets.map((p) => ({ value: p.id, label: p.label || p.id, mono: `${p.connector ? `${p.connector} · ` : ''}${(p.tables ?? []).map((x) => x.name).join(', ')}` }))}
                onChange={(v) => void applyPreset(v)} disabled={busy} />
            </Field>
          </Head>
          {model.preset && <PresetNote>{t('settings.explorer.presetNote', { preset: model.preset })}</PresetNote>}
          <Section>
            <SectionHead><Database size={14} /> {t('settings.explorer.entities')} <span className="spacer" />
              <Button $variant="ghost" $size="sm" onClick={() => setEditing(null)} disabled={busy}><Plus size={13} /> {t('settings.explorer.addEntity')}</Button>
            </SectionHead>
            {entityIds.length === 0 && <Empty>{t('settings.explorer.noEntities')}</Empty>}
            {entityIds.map((id) => {
              const e = model.entities[id]
              const measures = Object.keys(e.measures ?? {})
              const meta = [e.query ? `${e.connector || scope}.${e.query}` : `${t('settings.explorer.table')} ${e.schema ? `${e.schema}.` : ''}${e.table ?? ''}`, e.key?.length ? `${t('settings.explorer.key')}: ${e.key.join(', ')}` : null,
                measures.length ? `Σ ${measures.join(', ')}` : null, e.default_date ? `${t('settings.explorer.defaultDate')}: ${e.default_date}` : null].filter(Boolean).join(' · ')
              return (
                <Item key={id} onClick={() => setEditing({ ...e, id })}>
                  <Database className="icon" size={16} />
                  <span className="text">
                    <span className="name">{id}{e.label ? <span style={{ color: colors.text.secondary, fontFamily: fonts.sans }}> — {e.label}</span> : null}
                      {model.hub === id && <Tag $tone="blue" style={{ marginLeft: 8, fontSize: fontSize.micro }}>{t('settings.explorer.hub')}</Tag>}
                      <Tag $tone={e.cardinality === 'many' ? 'orange' : 'neutral'} style={{ marginLeft: 6, fontSize: fontSize.micro }}>{model.hub === id || e.cardinality !== 'many' ? '1..1' : '1..n'}</Tag>
                    </span>
                    <span className="meta">{meta}</span>
                  </span>
                  <span className="actions" onClick={(ev) => ev.stopPropagation()}>
                    <Button $variant="ghost" $size="sm" onClick={() => setEditing({ ...e, id })} disabled={busy}><Edit3 size={13} /> {t('common.edit')}</Button>
                    <Button $variant="ghost" $size="sm" onClick={() => void removeEntity(id)} disabled={busy} style={{ color: colors.red.main }}><Trash2 size={13} /> {t('common.delete')}</Button>
                  </span>
                </Item>
              )
            })}
          </Section>
          <Section>
            <SectionHead><Link2 size={14} /> {t('settings.explorer.relations')} <span className="spacer" />
              <Button $variant="ghost" $size="sm" disabled={busy || entityIds.length < 2}
                onClick={() => update({ relations: [...(model.relations ?? []), { from: entityIds.find((x) => x !== model.hub) ?? entityIds[0], to: model.hub, on: [] }] })}>
                <Plus size={13} /> {t('settings.explorer.addRelation')}
              </Button>
            </SectionHead>
            {(model.relations ?? []).length === 0 && <Empty>{t('settings.explorer.noRelations')}</Empty>}
            {(model.relations ?? []).map((r, i) => {
              const fromCols = columnsOf(r.from).map((c) => ({ value: c, label: c }))
              const toCols = columnsOf(r.to).map((c) => ({ value: c, label: c }))
              const pairs = (r.on ?? []).length ? r.on : [['', '']]
              const setPair = (k: number, side: 0 | 1, v: string) => setRelation(i, { on: pairs.map((p, j) => (j === k ? (side === 0 ? [v, p[1]] : [p[0], v]) : p)) })
              return (
                <RelRow key={i}>
                  <SearchSelect value={r.from} onChange={(v) => setRelation(i, { from: v, on: [] })} options={entityOpts} />
                  <Arrow>→</Arrow>
                  <SearchSelect value={r.to} onChange={(v) => setRelation(i, { to: v, on: [] })} options={entityOpts} />
                  <Button $variant="ghost" $size="sm" style={{ color: colors.red.main }} onClick={() => update({ relations: (model.relations ?? []).filter((_, k) => k !== i) })}><Trash2 size={13} /></Button>
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
                </RelRow>
              )
            })}
          </Section>
        </Body>
      )}
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
