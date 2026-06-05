// Shared actions editor (actions.toml) — the reusable named-action registry referenced by screens
// via `call_action`. A shared action is a named chain of the same typed steps a screen uses, so the
// step editing reuses the exact `ActionTreeView` the Screen Designer mounts. Left: the action list
// (+ add / rename / delete). Right: the selected action's label / description + its steps.
//
// prompt_fields (collected once when the action runs) aren't edited here yet — author them in
// actions.toml directly; most shared actions take their inputs from the caller's `call_action`
// param_binds instead.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Plus, Trash2, Save } from 'lucide-react'
import {
  Banner, Button, Field, FrameworkEnumsContext, Input, useModals,
  type FrameworkEnums, type JsonSchema,
} from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'
import { validateId } from '../../services/idValidator'
import ActionTreeView from './ActionTreeView'
import type { ActionPath } from './actionPath'

type Row = Record<string, unknown>

interface ActionsDoc {
  path: string
  actions: Record<string, Row>   // id → { label?, description?, prompt_fields?, steps? }
}

const Layout = styled.div`display: grid; grid-template-columns: 260px 1fr; gap: 16px; align-items: start;`
const ListPane = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  overflow: hidden; display: flex; flex-direction: column;
`
const ListRow = styled.button<{ $active?: boolean }>`
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px; width: 100%;
  padding: 8px 12px; border: none; border-bottom: 1px solid ${colors.border}; cursor: pointer; text-align: left;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  & .id { font-family: ${fonts.mono}; font-size: ${fontSize.sm};
    color: ${({ $active }) => ($active ? colors.blue.main : colors.text.primary)}; }
  & .lbl { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  &:hover { background: var(--hover-subtle); }
`
const EditorPane = styled.div`display: flex; flex-direction: column; gap: 12px; min-width: 0;`
const Bar = styled.div`display: flex; align-items: center; gap: 8px; margin-bottom: 12px;`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; margin-bottom: 8px;`
const Two = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 10px;`
const SubHead = styled.div`font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; margin: 4px 0 2px;`
const ParamRow = styled.div`display: grid; grid-template-columns: 1fr 1fr 28px; gap: 6px; align-items: center;`
const SmallRemove = styled.button`
  height: 28px; width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid transparent; background: transparent; color: ${colors.text.muted}; border-radius: ${radius.sm}; cursor: pointer;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; background: ${colors.red.bg}; }
`

export default function ActionsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const [doc, setDoc] = useState<ActionsDoc | null>(null)
  const [defs, setDefs] = useState<Record<string, JsonSchema>>({})
  const [enums, setEnums] = useState<FrameworkEnums>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [path, setPath] = useState<ActionPath | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedNote, setSavedNote] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.get<{ actions?: { $defs?: Record<string, JsonSchema> }; framework_enums?: FrameworkEnums }>('/admin/config/schema'),
      api.get<ActionsDoc>('/admin/config/actions/parsed'),
    ])
      .then(([schema, d]) => {
        if (cancelled) return
        setDefs((schema.actions?.$defs ?? {}) as Record<string, JsonSchema>)
        setEnums(schema.framework_enums ?? {})
        setDoc(d)
        setSelectedId(Object.keys(d.actions)[0] ?? null)
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [])

  const actions = doc?.actions ?? {}
  const selected = selectedId ? actions[selectedId] : null

  const patchSelected = (p: Row) => {
    if (!selectedId || !doc) return
    const cur = { ...actions[selectedId], ...p }
    for (const k of Object.keys(p)) {
      const v = cur[k]
      if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) delete cur[k]
    }
    setDoc({ ...doc, actions: { ...actions, [selectedId]: cur } })
    setDirty(true); setSavedNote(null)
  }

  const addAction = async () => {
    if (!doc) return
    const existing = Object.keys(actions)
    const id = (await modals.prompt({
      title: t('settings.actions.add', 'New shared action'),
      message: t('settings.actions.namePrompt', 'Id for the new action:'),
      placeholder: 'snake_case',
      submitLabel: t('common.add', 'Add'),
      validate: (v) => validateId({ kind: 'action', proposed: v, existing, mode: 'add' }),
    }))?.trim()
    if (!id) return
    setDoc({ ...doc, actions: { ...actions, [id]: { label: '', steps: [] } } })
    setSelectedId(id); setPath(null); setDirty(true); setSavedNote(null)
  }

  const removeAction = async (id: string) => {
    if (!doc) return
    const ok = await modals.confirm({
      title: t('settings.actions.delete', 'Delete shared action'),
      message: t('settings.actions.deleteConfirm', 'Delete "{{id}}"? Screens that reference it will fail integrity.', { id }),
    })
    if (!ok) return
    const next = { ...actions }; delete next[id]
    setDoc({ ...doc, actions: next })
    if (selectedId === id) { setSelectedId(Object.keys(next)[0] ?? null); setPath(null) }
    setDirty(true); setSavedNote(null)
  }

  const save = async () => {
    if (!doc) return
    setSaving(true); setError(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/actions/parsed', { actions: doc.actions })
      setDirty(false)
      setSavedNote(t('settings.actions.saved', 'Saved. Reload config to apply (Integrity / Reload).'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  if (error && !doc) return <Banner $tone="error">{error}</Banner>
  if (!doc) return <Sub>{t('common.loading', 'Loading…')}</Sub>

  const steps = Array.isArray(selected?.steps) ? (selected!.steps as Row[]) : []
  const params = Array.isArray(selected?.params) ? (selected!.params as Row[]) : []
  const setParams = (next: Row[]) => patchSelected({ params: next })

  return (
    <FrameworkEnumsContext.Provider value={enums}>
      <Bar>
        <Button $variant="primary" $size="sm" onClick={() => void save()} disabled={!dirty || saving}>
          <Save size={14} /> {saving ? t('common.saving', 'Saving…') : t('common.save', 'Save')}
        </Button>
        {dirty && <Sub style={{ margin: 0 }}>{t('common.unsaved', 'Unsaved changes')}</Sub>}
        {savedNote && <Banner $tone="ok">{savedNote}</Banner>}
        {error && <Banner $tone="error">{error}</Banner>}
      </Bar>
      <Sub>{t('settings.actions.hint',
        'Reusable action chains referenced from screens via a call_action. Each is a named sequence of the same steps a screen uses (run_query / call_api / call_plugin / if / loop / chain).')}</Sub>
      <Layout>
        <ListPane>
          {Object.entries(actions).map(([id, a]) => (
            <ListRow key={id} $active={id === selectedId} onClick={() => { setSelectedId(id); setPath(null) }}>
              <span className="id">{id}</span>
              {typeof a.label === 'string' && a.label && <span className="lbl">{a.label}</span>}
            </ListRow>
          ))}
          <Button $variant="ghost" $size="sm" onClick={() => void addAction()} style={{ margin: 8, alignSelf: 'flex-start' }}>
            <Plus size={13} /> {t('settings.actions.add', 'Add shared action')}
          </Button>
        </ListPane>

        {selected && selectedId ? (
          <EditorPane>
            <Two>
              <Field label={t('settings.actions.label', 'Label')}>
                <Input value={typeof selected.label === 'string' ? selected.label : ''}
                  onChange={(e) => patchSelected({ label: e.target.value })} placeholder={selectedId} />
              </Field>
              <Field label={t('settings.actions.description', 'Description')}>
                <Input value={typeof selected.description === 'string' ? selected.description : ''}
                  onChange={(e) => patchSelected({ description: e.target.value })} />
              </Field>
            </Two>
            <div>
              <SubHead>{t('settings.actions.params', 'Inputs')}</SubHead>
              <Sub>{t('settings.actions.paramsHint',
                'The inputs this action needs. A step reads one as INPUT.<name>; a screen binds its source via call_action. The default is used when the caller doesn’t bind it — a literal (Y / QPRINT) or a built-in: #LOGIN_USER#, #SYSDATE#, #NOW#, #CURRENT_TIME#, #LANGUAGE#.')}</Sub>
              {params.map((p, i) => (
                <ParamRow key={i} style={{ marginBottom: 6 }}>
                  <Input value={typeof p.name === 'string' ? p.name : ''}
                    placeholder={t('settings.actions.paramName', 'name')}
                    onChange={(e) => { const next = params.slice(); next[i] = { ...next[i], name: e.target.value }; setParams(next) }} />
                  <Input value={typeof p.default === 'string' ? p.default : ''}
                    placeholder={t('settings.actions.paramDefault', 'default — literal or #LOGIN_USER#')}
                    onChange={(e) => {
                      const next = params.slice(); const cur = { ...next[i] }
                      if (e.target.value) cur.default = e.target.value; else delete cur.default
                      next[i] = cur; setParams(next)
                    }} />
                  <SmallRemove type="button" onClick={() => { const next = params.slice(); next.splice(i, 1); setParams(next) }}><Trash2 size={14} /></SmallRemove>
                </ParamRow>
              ))}
              <Button $variant="ghost" $size="sm" onClick={() => setParams([...params, { name: '' }])} style={{ marginTop: 2 }}>
                <Plus size={13} /> {t('settings.actions.addParam', 'Add input')}
              </Button>
            </div>
            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button $variant="danger" $size="sm" onClick={() => void removeAction(selectedId)}>
                <Trash2 size={13} /> {t('settings.actions.delete', 'Delete')}
              </Button>
            </div>
            <ActionTreeView
              actions={steps}
              onChange={(next) => patchSelected({ steps: next })}
              path={path}
              onPathChange={setPath}
              defs={defs}
              effectiveConnector=""
              onEditQuery={() => { /* edit queries in the Connectors tab */ }}
              rootLabel={selectedId}
              heading={t('settings.actions.steps', 'Steps')}
              hint={t('settings.actions.stepsHint', 'The ordered steps this action runs. Pin each query/plugin step’s connector explicitly (shared actions aren’t tied to one app).')}
            />
          </EditorPane>
        ) : (
          <Sub>{t('settings.actions.empty', 'No shared actions yet — add one to get started.')}</Sub>
        )}
      </Layout>
    </FrameworkEnumsContext.Provider>
  )
}
