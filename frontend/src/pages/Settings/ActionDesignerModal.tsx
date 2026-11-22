// Full-height editor for ONE shared action — the same modal shell as ScreenDesignerModal. Fetches
// actions.toml + schema on open, edits the action's label / description / params / steps (steps via
// the existing ActionTreeView), and PUTs the merged actions.toml + reloads on Save. The action id
// is the dict key and is read-only here (renaming needs the cross-ref cascade — a list-level op).
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Workflow, List } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Centered, Field, FrameworkEnumsContext, Input, SearchSelect, useModals,
  type FrameworkEnums, type JsonSchema,
} from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import styled from '@emotion/styled'
import ActionFlow from './ActionFlow'
import ActionTreeView from './ActionTreeView'
import { EditQueryModal } from './EditQueryModal'
import { builtinSourceOptions } from './actionCandidates'
import type { ActionPath } from './actionPath'
import { colors, fontSize, fonts, radius } from '../../theme'
import { useDirtyGuard } from '../../tabs/dirtyGuard'

type Row = Record<string, unknown>

const BUILTIN_VALUE_OPTIONS = builtinSourceOptions()

const Body = styled.div`
  flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 20px;
  display: flex; flex-direction: column; gap: 14px;
`
const Two = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 10px;`
// the graphical view: the flow on the left, what is selected on the right (the service itself, or one step)
const Split = styled.div`flex: 1 1 auto; min-height: 0; display: flex; gap: 14px; padding: 16px 20px 20px;`
const FlowCol = styled.div`flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 10px;`
const Side = styled.div`
  flex: 0 0 min(480px, 42%); min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 14px;
  padding: 14px 16px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
`
const Switch = styled.div`
  display: inline-flex; align-self: flex-start; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;
  & button { display: inline-flex; align-items: center; gap: 6px; padding: 5px 12px; border: none; cursor: pointer; background: transparent;
    color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; }
  & button[data-on='true'] { background: ${colors.blue.bg}; color: ${colors.blue.main}; }
`
const SubHead = styled.div`font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; margin: 4px 0 2px;`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro}; line-height: 1.4; margin-bottom: 6px;`
const ParamRow = styled.div`display: grid; grid-template-columns: 1fr 1fr 28px; gap: 6px; align-items: center; margin-bottom: 6px;`
const SmallRemove = styled.button`
  height: 28px; width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid transparent; background: transparent; color: ${colors.text.muted}; border-radius: ${radius.sm}; cursor: pointer;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; background: ${colors.red.bg}; }
`

interface Props {
  actionId: string
  onClose: () => void
  onSaved?: () => void
}

export function ActionDesignerModal({ actionId, onClose, onSaved }: Props) {
  const { t } = useTranslation()
  const modals = useModals()
  const [doc, setDoc] = useState<Record<string, Row> | null>(null)   // id → action dict
  const [defs, setDefs] = useState<Record<string, JsonSchema>>({})
  const [enums, setEnums] = useState<FrameworkEnums | null>(null)
  const [value, setValue] = useState<Row | null>(null)              // the edited action
  const [path, setPath] = useState<ActionPath | null>(null)
  const [view, setView] = useState<'flow' | 'list'>('flow')
  const [original, setOriginal] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fullscreen, setFullscreen] = useState(true)
  // Edit-query raised from a run_query step's pencil — opens EditQueryModal (z-index 600, above this
  // editor's Overlay at 400). Previously a no-op, so the pencil appeared dead.
  const [editQuery, setEditQuery] = useState<{ connector: string; queryName: string } | null>(null)
  const closedRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.get<{ actions?: { $defs?: Record<string, JsonSchema> }; framework_enums?: FrameworkEnums }>('/admin/config/schema'),
      api.get<{ actions: Record<string, Row> }>('/admin/config/actions/parsed'),
    ])
      .then(([schema, d]) => {
        if (cancelled) return
        setDefs((schema.actions?.$defs ?? {}) as Record<string, JsonSchema>)
        setEnums(schema.framework_enums ?? {})
        setDoc(d.actions)
        const v = d.actions[actionId]
        if (v) { setValue(v); setOriginal(JSON.stringify(v)) }
        else setError(t('settings.actions.notFound', { id: actionId }))
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [actionId, t])

  const dirty = !!value && JSON.stringify(value) !== original
  useDirtyGuard(dirty)          // the shell asks before a closed tab or a reload loses these edits

  const patch = (p: Row) => {
    if (!value) return
    const cur = { ...value, ...p }
    for (const k of Object.keys(p)) {
      const v = cur[k]
      if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) delete cur[k]
    }
    setValue(cur)
  }

  const save = async () => {
    if (!doc || !value) return
    setBusy(true); setError(null)
    try {
      const next = { ...doc, [actionId]: value }
      await api.put<{ saved: boolean }>('/admin/config/actions/parsed', { actions: next })
      await api.post('/admin/reload')
      closedRef.current = true
      onSaved?.()
      onClose()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const cancel = async () => {
    if (closedRef.current) return
    if (!dirty) { onClose(); return }
    const choice = await modals.choose<'discard' | 'save' | 'keep'>({
      title: t('settings.screens.designer.unsavedTitle'),
      message: t('settings.actions.unsavedMsg'),
      options: [
        { value: 'discard', label: t('settings.screens.designer.discard'), variant: 'danger' },
        { value: 'save', label: t('common.save'), variant: 'primary' },
        { value: 'keep', label: t('settings.screens.designer.keepEditing'), variant: 'ghost', autoFocus: true },
      ],
      cancelValue: 'keep',
    })
    if (choice === 'keep' || choice == null) return
    if (choice === 'discard') onClose()
    else if (choice === 'save') await save()
  }

  // Escape is handled by EditorModalShell (→ onClose → ``cancel`` → the unsaved prompt).

  const params = Array.isArray(value?.params) ? (value!.params as Row[]) : []
  const setParams = (nx: Row[]) => patch({ params: nx })
  const steps = Array.isArray(value?.steps) ? (value!.steps as Row[]) : []

  const properties = !value ? null : (
    <>
        <Two>
          <Field label={t('settings.actions.label')}>
            <Input value={typeof value.label === 'string' ? value.label : ''} placeholder={actionId}
              onChange={(e) => patch({ label: e.target.value })} />
          </Field>
          <Field label={t('settings.actions.description')}>
            <Input value={typeof value.description === 'string' ? value.description : ''}
              onChange={(e) => patch({ description: e.target.value })} />
          </Field>
        </Two>

        <div>
          <SubHead>{t('settings.actions.params')}</SubHead>
          <Sub>{t('settings.actions.paramsHint')}</Sub>
          {params.map((p, i) => (
            <ParamRow key={i}>
              <Input value={typeof p.name === 'string' ? p.name : ''}
                placeholder={t('settings.actions.paramName')}
                onChange={(e) => { const nx = params.slice(); nx[i] = { ...nx[i], name: e.target.value }; setParams(nx) }} />
              <SearchSelect value={typeof p.default === 'string' ? p.default : ''}
                options={BUILTIN_VALUE_OPTIONS} allowCustom
                anyLabel={t('common.none')}
                placeholder={t('settings.actions.paramDefault')}
                onChange={(v) => { const nx = params.slice(); const cur = { ...nx[i] }; if (v) cur.default = v; else delete cur.default; nx[i] = cur; setParams(nx) }} />
              <SmallRemove type="button" onClick={() => { const nx = params.slice(); nx.splice(i, 1); setParams(nx) }}><Trash2 size={14} /></SmallRemove>
            </ParamRow>
          ))}
          <Button $variant="ghost" $size="sm" onClick={() => setParams([...params, { name: '' }])}>
            <Plus size={13} /> {t('settings.actions.addParam')}
          </Button>
        </div>
    </>
  )
  const stepEditor = (
          <ActionTreeView
            actions={steps}
            onChange={(nx) => patch({ steps: nx })}
            path={path}
            onPathChange={setPath}
            defs={defs}
            effectiveConnector=""
            onEditQuery={(connector, queryName) => setEditQuery({ connector, queryName })}
            rootLabel={actionId}
            // Heading rendered by the outer <SubHead> above — don't let ActionTreeView paint a
            // second "Steps". Keep the hint (it has no outer equivalent).
            hint={t('settings.actions.stepsHint')}
          />
  )
  const viewSwitch = (
    <Switch>
      <button type="button" data-on={view === 'flow'} onClick={() => setView('flow')}><Workflow size={14} /> {t('settings.studio.viewFlow')}</button>
      <button type="button" data-on={view === 'list'} onClick={() => { setView('list'); setPath(null) }}><List size={14} /> {t('settings.studio.viewList')}</button>
    </Switch>
  )
  const inner = (!doc) ? <Centered />
    : error ? <Banner $tone="error">{error}</Banner>
    : !value ? <Centered />
    : view === 'flow' ? (
      <Split>
        <FlowCol>
          {viewSwitch}
          <ActionFlow
            actions={steps} onChange={(nx) => patch({ steps: nx })} path={path} onPathChange={setPath}
            inputs={params.map((p) => (typeof p.name === 'string' ? p.name : '')).filter(Boolean)}
          />
        </FlowCol>
        <Side>
          {path && path.length > 0 ? stepEditor : (
            <>
              <SubHead>{t('settings.studio.service')}</SubHead>
              {properties}
              <Sub>{t('settings.studio.flowHint')}</Sub>
            </>
          )}
        </Side>
      </Split>
    ) : (
      <Body>
        {viewSwitch}
        {properties}
        <div>
          <SubHead>{t('settings.actions.steps')}</SubHead>
          {stepEditor}
        </div>
      </Body>
    )

  return (
    <FrameworkEnumsContext.Provider value={enums}>
      <EditorModalShell
        variant="visual"
        title={(
          <>
            {t('settings.actions.designerTitle')} ·{' '}
            <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontWeight: 400 }}>[actions.{actionId}]</span>
          </>
        )}
        onClose={() => void cancel()}
        onSave={() => void save()}
        dirty={dirty}
        saveDisabled={!dirty}
        busy={busy}
        fullscreen={fullscreen}
        onToggleFullscreen={() => setFullscreen((v) => !v)}
        scrollBody={false}  /* the inner <Body> manages its own scroll */
      >
        {inner}
      </EditorModalShell>
      {editQuery && (
        <EditQueryModal
          connector={editQuery.connector}
          queryName={editQuery.queryName}
          onClose={() => setEditQuery(null)}
        />
      )}
    </FrameworkEnumsContext.Provider>
  )
}

export default ActionDesignerModal
