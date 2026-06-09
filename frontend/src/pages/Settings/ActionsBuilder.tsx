// Shared actions (actions.toml) — the reusable named-action registry referenced by screens via
// `call_action`. A list page (same shell + row operations as the Screens list): click a row to edit
// it in the full-height ActionDesignerModal; per-row Find usages / Clone / Delete; Add up top.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Plus, Trash2, Copy, GitBranch, GitMerge, Edit3, Link2 } from 'lucide-react'
import { Banner, Button, useModals } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'
import { validateId } from '../../services/idValidator'
import ActionDesignerModal from './ActionDesignerModal'
import { FindUsagesModal, type FindUsagesTarget } from './FindUsagesModal'
import { FindDependenciesModal, type DependencySeed } from './FindDependenciesModal'

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
  const [busy, setBusy] = useState(false)

  const load = () =>
    api.get<{ actions: Record<string, Row> }>('/admin/config/actions/parsed')
      .then((d) => setDoc(d.actions))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  useEffect(() => { void load() }, [])

  // List-level ops persist the whole actions.toml + reload (per-action editing is in the modal).
  const persist = async (next: Record<string, Row>) => {
    setBusy(true); setError(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/actions/parsed', { actions: next })
      await api.post('/admin/reload')
      setDoc(next)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const addAction = async () => {
    if (!doc) return
    const existing = Object.keys(doc)
    const id = (await modals.prompt({
      title: t('settings.actions.add', 'New shared action'),
      message: t('settings.actions.namePrompt', 'Id for the new action:'),
      placeholder: 'snake_case',
      submitLabel: t('common.add', 'Add'),
      validate: (v) => validateId({ kind: 'action', proposed: v, existing, mode: 'add' }),
    }))?.trim()
    if (!id) return
    await persist({ ...doc, [id]: { label: '', steps: [] } })
    setEditing(id)   // jump straight into the designer
  }

  const cloneAction = async (id: string) => {
    if (!doc) return
    const existing = Object.keys(doc)
    const newId = (await modals.prompt({
      title: t('settings.actions.clone', 'Clone shared action'),
      message: t('settings.actions.clonePrompt', 'New id for the copy of "{{id}}":', { id }),
      defaultValue: `${id}_copy`,
      placeholder: 'snake_case',
      submitLabel: t('common.clone', 'Clone'),
      validate: (v) => v === id
        ? { error: t('settings.tables.duplicateSameName', 'Pick a different name.') }
        : validateId({ kind: 'action', proposed: v, existing, mode: 'clone' }),
    }))?.trim()
    if (!newId) return
    await persist({ ...doc, [newId]: JSON.parse(JSON.stringify(doc[id])) as Row })
  }

  const renameAction = async (id: string) => {
    if (!doc) return
    const next = (await modals.prompt({
      title: t('settings.rename.button', 'Rename'),
      message: t('settings.actions.renamePrompt', 'New id for "{{id}}":', { id }),
      defaultValue: id,
      submitLabel: t('settings.rename.button', 'Rename'),
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
      title: t('settings.actions.delete', 'Delete shared action'),
      message: t('settings.actions.deleteConfirm', 'Delete "{{id}}"? Screens that reference it will fail integrity (check Find usages first).', { id }),
    })
    if (!ok) return
    const next = { ...doc }; delete next[id]
    await persist(next)
  }

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Sub>{t('common.loading', 'Loading…')}</Sub>

  const ids = Object.keys(doc).sort((a, b) => a.localeCompare(b))

  return (
    <>
      <Bar>
        <Button $variant="ghost" $size="sm" onClick={() => void addAction()} disabled={busy}>
          <Plus size={13} /> {t('settings.actions.add', 'Add shared action')}
        </Button>
        {error && <Banner $tone="error">{error}</Banner>}
      </Bar>
      <Sub>{t('settings.actions.hint',
        'Reusable action chains referenced from screens via a call_action. Each is a named sequence of the same steps a screen uses (run_query / call_api / call_plugin / if / loop / chain). Click one to edit its inputs + steps.')}</Sub>

      <List>
        {ids.map((id) => {
          const a = doc[id]
          const label = typeof a.label === 'string' ? a.label : ''
          const stepCount = Array.isArray(a.steps) ? a.steps.length : 0
          const paramCount = Array.isArray(a.params) ? a.params.length : 0
          const meta = [
            t('settings.actions.summary.steps', '{{n}} step(s)', { n: stepCount }),
            paramCount > 0 && t('settings.actions.summary.inputs', '{{n}} input(s)', { n: paramCount }),
          ].filter(Boolean).join(' · ')
          return (
            <Item key={id} onClick={() => setEditing(id)}>
              <Link2 className="icon" size={18} />
              <span className="text">
                <span className="name">{id}</span>
                {label && <span className="label">{label}</span>}
                <span className="meta">{meta}</span>
              </span>
              <span className="actions" onClick={(e) => e.stopPropagation()}>
                <Button $variant="ghost" $size="sm" disabled={busy}
                  onClick={() => setUsagesTarget({ kind: 'action', name: id, label: `shared action ${id}` })}>
                  <GitBranch size={13} /> {t('findUsages.button', 'Find usages')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy}
                  onClick={() => setDepsSeeds([{ kind: 'action', name: id, label: `shared action ${id}` }])}>
                  <GitMerge size={13} /> {t('findDeps.button', 'Find dependencies')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy} onClick={() => void renameAction(id)}>
                  <Edit3 size={13} /> {t('settings.rename.button', 'Rename')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy} onClick={() => void cloneAction(id)}>
                  <Copy size={13} /> {t('common.clone', 'Clone')}
                </Button>
                <Button $variant="ghost" $size="sm" disabled={busy} style={{ color: colors.red.main }} onClick={() => void deleteAction(id)}>
                  <Trash2 size={13} /> {t('common.delete', 'Delete')}
                </Button>
              </span>
            </Item>
          )
        })}
        {ids.length === 0 && <Sub>{t('settings.actions.empty', 'No shared actions yet — add one to get started.')}</Sub>}
      </List>

      {editing && <ActionDesignerModal actionId={editing} onClose={() => setEditing(null)} onSaved={() => void load()} />}
      {usagesTarget && <FindUsagesModal target={usagesTarget} onClose={() => setUsagesTarget(null)} />}
      {depsSeeds && <FindDependenciesModal seeds={depsSeeds} onClose={() => setDepsSeeds(null)} />}
    </>
  )
}
