// The studio — shared actions (actions.toml): named sequences of steps a screen calls (`call_action`) and an
// API endpoint can be SERVED by (`EndpointDef.action`). A list page (same shell + row operations as the Screens
// list): click a row to design it in the full-height ActionDesignerModal; per row: Run (inputs → the answer),
// Expose as an endpoint, Find usages / Clone / Delete; the endpoints a service already serves are shown on it.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Plus, Trash2, Copy, GitBranch, GitMerge, Edit3, Link2, Play, Share2 } from 'lucide-react'
import { Banner, Button, SaveProblems, useModals } from '../../common'
import { failureOf, targetOf, type SaveFailure, type SaveProblem } from '../../services/saveProblems'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'
import { newObjectFromLabel, validateId } from '../../services/idValidator'
import ActionDesignerModal from './ActionDesignerModal'
import { FindUsagesModal, type FindUsagesTarget } from './FindUsagesModal'
import { FindDependenciesModal, type DependencySeed } from './FindDependenciesModal'
import { StudioExposeModal, StudioRunModal, type StudioService } from './StudioModals'
import { useWorkspace } from '../../workspace/WorkspaceContext'

type Row = Record<string, unknown>

const Bar = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 10px; flex-wrap: wrap;`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; margin-bottom: 12px; line-height: 1.5;`
const List = styled.div`display: flex; flex-direction: column; gap: 6px;`
const Item = styled.div`
  display: flex; align-items: center; gap: 12px; padding: 10px 12px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; cursor: pointer;
  & > .icon { color: ${colors.text.muted}; flex-shrink: 0; }
  & > .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .label { font-size: ${fontSize.sm}; color: ${colors.text.secondary}; }
  & .meta { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & > .actions { display: flex; gap: 4px; flex-shrink: 0; }
  &:hover { border-color: ${colors.blue.border}; background: var(--hover-subtle); }
`

export default function ActionsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const [doc, setDoc] = useState<Record<string, Row> | null>(null)
  const [editing, setEditing] = useState<string | null>(null)
  const [usagesTarget, setUsagesTarget] = useState<FindUsagesTarget | null>(null)
  const [depsSeeds, setDepsSeeds] = useState<DependencySeed[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  // a save the server refused: its problems one by one, each opening the service it is in
  const [failure, setFailure] = useState<SaveFailure | null>(null)
  const [busy, setBusy] = useState(false)
  // the services as the server runs them: their inputs, the endpoints they serve, where one can be exposed
  const [studio, setStudio] = useState<{ services: StudioService[]; connectors: string[]; default_connector: string } | null>(null)
  const [running, setRunning] = useState<StudioService | null>(null)
  const [exposing, setExposing] = useState<StudioService | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const { refresh } = useWorkspace()

  const load = () => {
    api.get<{ services: StudioService[]; connectors: string[]; default_connector: string }>('/admin/studio/services')
      .then(setStudio).catch(() => setStudio(null))
    return api.get<{ actions: Record<string, Row> }>('/admin/config/actions/parsed')
      .then((d) => setDoc(d.actions))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }
  useEffect(() => { void load() }, [])

  // List-level ops persist the whole actions.toml + reload (per-action editing is in the modal).
  const persist = async (next: Record<string, Row>) => {
    setBusy(true); setError(null); setFailure(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/actions/parsed', { actions: next })
      await api.post('/admin/reload')
      setDoc(next)
      void load()
    } catch (e) {
      setFailure(failureOf(e))
    } finally { setBusy(false) }
  }
  // "Open" on a problem of a refused save: the service it is in, in its designer.
  const problemOpener = (p: SaveProblem): (() => void) | null => {
    const id = targetOf('actions', p.path)?.id
    return id && doc?.[id] ? () => setEditing(id) : null
  }

  const addAction = async () => {
    if (!doc) return
    const existing = Object.keys(doc)
    // the NAME is asked for; the id is made from it (shown while it is typed)
    const name = (await modals.prompt({
      title: t('settings.actions.add'),
      message: t('settings.newObject.name'),
      placeholder: t('settings.actions.nameExample'),
      submitLabel: t('common.add'),
      validate: (v) => (newObjectFromLabel(v, existing) ? null : t('settings.newObject.noLetters')),
      note: (v) => t('settings.newObject.idWillBe', { id: newObjectFromLabel(v, existing)?.id ?? '' }),
    }))?.trim()
    const made = name ? newObjectFromLabel(name, existing) : null
    if (!made) return
    await persist({ ...doc, [made.id]: { label: made.label, steps: [] } })
    setEditing(made.id)   // jump straight into the designer
  }

  const cloneAction = async (id: string) => {
    if (!doc) return
    const existing = Object.keys(doc)
    const newId = (await modals.prompt({
      title: t('settings.actions.clone'),
      message: t('settings.actions.clonePrompt', { id }),
      defaultValue: `${id}_copy`,
      placeholder: 'snake_case',
      submitLabel: t('common.clone'),
      validate: (v) => v === id
        ? { error: t('settings.tables.duplicateSameName') }
        : validateId({ kind: 'action', proposed: v, existing, mode: 'clone' }),
    }))?.trim()
    if (!newId) return
    await persist({ ...doc, [newId]: JSON.parse(JSON.stringify(doc[id])) as Row })
  }

  const renameAction = async (id: string) => {
    if (!doc) return
    const next = (await modals.prompt({
      title: t('settings.rename.button'),
      message: t('settings.actions.renamePrompt', { id }),
      defaultValue: id,
      submitLabel: t('settings.rename.button'),
      validate: (v) => validateId({ kind: 'action', proposed: v, existing: Object.keys(doc), mode: 'rename', currentName: id }),
    }))?.trim()
    if (!next || next === id) return
    setBusy(true); setError(null)
    try {
      // Cross-file rename — rewrites the actions.toml key + every screen's call_action.ref.
      const res = await api.post<{ total_refs?: number }>('/admin/config/rename', { kind: 'action', old_name: id, new_name: next })
      await api.post('/admin/reload')
      await load()
      setError(null)
      void res
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const deleteAction = async (id: string) => {
    if (!doc) return
    const ok = await modals.confirm({
      title: t('settings.actions.delete'),
      message: t('settings.actions.deleteConfirm', { id }),
    })
    if (!ok) return
    const next = { ...doc }; delete next[id]
    await persist(next)
  }

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Sub>{t('common.loading')}</Sub>

  const ids = Object.keys(doc).sort((a, b) => a.localeCompare(b))

  return (
    <>
      <Bar>
        <Button $variant="ghost" $size="sm" onClick={() => void addAction()} disabled={busy}>
          <Plus size={13} /> {t('settings.actions.add')}
        </Button>
        {error && <Banner $tone="error">{error}</Banner>}
      </Bar>
      {notice && <Banner $tone="ok">{notice}</Banner>}
      <SaveProblems failure={failure} opener={problemOpener} />
      <Sub>{t('settings.actions.hint')}</Sub>

      <List>
        {ids.map((id) => {
          const a = doc[id]
          const label = typeof a.label === 'string' ? a.label : ''
          const stepCount = Array.isArray(a.steps) ? a.steps.length : 0
          const paramCount = Array.isArray(a.params) ? a.params.length : 0
          const meta = [
            t('settings.actions.summary.steps', { n: stepCount }),
            paramCount > 0 && t('settings.actions.summary.inputs', { n: paramCount }),
          ].filter(Boolean).join(' · ')
          const service = studio?.services.find((x) => x.id === id) ?? null
          return (
            <Item key={id} onClick={() => setEditing(id)}>
              <Link2 className="icon" size={18} />
              <span className="text">
                <span className="name">{id}</span>
                {label && <span className="label">{label}</span>}
                <span className="meta">{meta}</span>
                {(service?.endpoints ?? []).map((e) => (
                  <span key={`${e.connector}.${e.endpoint}`} className="meta" style={{ fontFamily: fonts.mono }}>POST /api/http/{e.connector}/{e.endpoint}</span>
                ))}
              </span>
              <span className="actions" onClick={(e) => e.stopPropagation()}>
                <Button $variant="ghost" $size="sm" disabled={busy || !service} onClick={() => service && setRunning(service)}>
                  <Play size={13} /> {t('settings.studio.run')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy || !service} onClick={() => service && setExposing(service)}>
                  <Share2 size={13} /> {t('settings.studio.expose')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy}
                  onClick={() => setUsagesTarget({ kind: 'action', name: id, label: `shared action ${id}` })}>
                  <GitBranch size={13} /> {t('findUsages.button')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy}
                  onClick={() => setDepsSeeds([{ kind: 'action', name: id, label: `shared action ${id}` }])}>
                  <GitMerge size={13} /> {t('findDeps.button')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy} onClick={() => void renameAction(id)}>
                  <Edit3 size={13} /> {t('settings.rename.button')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy} onClick={() => void cloneAction(id)}>
                  <Copy size={13} /> {t('common.clone')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy} style={{ color: colors.red.main }} onClick={() => void deleteAction(id)}>
                  <Trash2 size={13} /> {t('common.delete')}
                </Button>
              </span>
            </Item>
          )
        })}
        {ids.length === 0 && <Sub>{t('settings.actions.empty')}</Sub>}
      </List>

      {editing && <ActionDesignerModal actionId={editing} onClose={() => setEditing(null)} onSaved={() => void load()} />}
      {running && <StudioRunModal service={running} onClose={() => setRunning(null)} />}
      {exposing && studio && (
        <StudioExposeModal
          service={exposing} connectors={studio.connectors} defaultConnector={studio.default_connector} onClose={() => setExposing(null)}
          onDone={(message) => { setExposing(null); setNotice(message); void load(); refresh() }}
        />
      )}
      {usagesTarget && <FindUsagesModal target={usagesTarget} onClose={() => setUsagesTarget(null)} />}
      {depsSeeds && <FindDependenciesModal seeds={depsSeeds} onClose={() => setDepsSeeds(null)} />}
    </>
  )
}
