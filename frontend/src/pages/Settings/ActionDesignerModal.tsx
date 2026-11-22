// Full-height editor for ONE shared action — the same modal shell as ScreenDesignerModal. Fetches
// actions.toml + schema on open, edits the action's label / description / params / steps (steps via
// the existing ActionTreeView), and PUTs the merged actions.toml + reloads on Save. The action id
// is the dict key and is read-only here (renaming needs the cross-ref cascade — a list-level op).
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Workflow, List } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Centered, Field, FrameworkEnumsContext, Input, ObjectTitle, SaveProblems, SchemaForm, SearchSelect, useModals,
  type FrameworkEnums, type JsonSchema, type SearchSelectOption,
} from '../../common'
import { failureOf, type SaveFailure } from '../../services/saveProblems'
import { EditorModalShell } from '../../common/EditorModalShell'
import styled from '@emotion/styled'
import ActionFlow from './ActionFlow'
import ActionTreeView from './ActionTreeView'
import { EditQueryModal } from './EditQueryModal'
import { builtinSourceOptions } from './actionCandidates'
import { withCapturedResults, type ActionPath } from './actionPath'
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
const ParamCard = styled.div`
  display: flex; align-items: flex-start; gap: 6px; padding: 10px; margin-bottom: 8px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
`
const ParamGrid = styled.div`flex: 1; min-width: 0; display: grid; grid-template-columns: 1fr 1fr; gap: 8px 10px;`
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
  // a save the server refused: shown above the designer, which stays as it is
  const [failure, setFailure] = useState<SaveFailure | null>(null)
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
    setBusy(true); setError(null); setFailure(null)
    try {
      const next = { ...doc, [actionId]: value }
      await api.put<{ saved: boolean }>('/admin/config/actions/parsed', { actions: next })
      await api.post('/admin/reload')
      closedRef.current = true
      onSaved?.()
      onClose()
    } catch (e) {
      // not ``setError``: that one replaces the designer by the message, and what was built could
      // no longer be corrected
      setFailure(failureOf(e))
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
  const serviceInputs: SearchSelectOption[] = [
    ...params.map((p) => ({ name: typeof p.name === 'string' ? p.name.trim() : '', label: typeof p.label === 'string' ? p.label : '', what: t('settings.actions.sourceInput') })),
    ...(Array.isArray(value?.prompt_fields) ? (value!.prompt_fields as Row[]) : []).map((p) => ({ name: typeof p.name === 'string' ? p.name.trim() : '', label: typeof p.label === 'string' ? p.label : '', what: t('settings.actions.sourceAnswer') })),
  ].filter((x) => x.name).map((x) => ({ value: `INPUT.${x.name}`, label: `${x.what} · ${x.label || x.name}`, mono: `INPUT.${x.name}`, group: 'inputs' }))
  // the form of ``prompt_fields`` alone, drawn from the service's own schema
  const promptProp = defs.SharedAction?.properties?.prompt_fields
  const promptSchema: JsonSchema | null = promptProp ? { type: 'object', properties: { prompt_fields: promptProp } } : null

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
          {/* an input: its name (what the steps read), the label and the purpose shown where the service is
              called, the value used when the caller gives none — each field under its own heading */}
          {params.map((p, i) => {
            const text = (k: string) => (typeof p[k] === 'string' ? (p[k] as string) : '')
            const setText = (k: string, v: string) => {
              const nx = params.slice(); const cur = { ...nx[i] }
              if (v || k === 'name') cur[k] = v; else delete cur[k]
              nx[i] = cur; setParams(nx)
            }
            return (
              <ParamCard key={i} data-service-input>
                <ParamGrid>
                  <Field label={t('settings.actions.paramNameLabel')}>
                    <Input value={text('name')} placeholder={t('settings.actions.paramNameExample')} onChange={(e) => setText('name', e.target.value)} />
                  </Field>
                  <Field label={t('settings.actions.paramLabel')}>
                    <Input data-input-label value={text('label')} placeholder={t('settings.actions.paramLabelExample')} onChange={(e) => setText('label', e.target.value)} />
                  </Field>
                  <Field label={t('settings.actions.paramDefaultLabel')}>
                    <SearchSelect value={text('default')} options={BUILTIN_VALUE_OPTIONS} allowCustom anyLabel={t('common.none')}
                      placeholder={t('settings.actions.paramDefaultExample')} onChange={(v) => setText('default', v)} />
                  </Field>
                  <Field label={t('settings.actions.paramWhat')}>
                    <Input data-input-what value={text('description')} placeholder={t('settings.actions.paramWhatExample')} onChange={(e) => setText('description', e.target.value)} />
                  </Field>
                </ParamGrid>
                <SmallRemove type="button" title={t('common.delete')} onClick={() => { const nx = params.slice(); nx.splice(i, 1); setParams(nx) }}><Trash2 size={14} /></SmallRemove>
              </ParamCard>
            )
          })}
          <Button $variant="ghost" $size="sm" onClick={() => setParams([...params, { name: '' }])}>
            <Plus size={13} /> {t('settings.actions.addParam')}
          </Button>
        </div>

        {/* the questions asked before the service runs: the same list of fields a screen's button has */}
        {promptSchema && (
          <div data-service-questions>
            <SchemaForm
              schema={promptSchema} defs={defs}
              value={{ prompt_fields: Array.isArray(value.prompt_fields) ? value.prompt_fields : [] }}
              onChange={(v) => patch({ prompt_fields: v.prompt_fields as Row[] | undefined })}
            />
          </div>
        )}
    </>
  )
  const stepEditor = (
          <ActionTreeView
            actions={steps}
            onChange={(nx) => patch({ steps: withCapturedResults(nx) })}
            path={path}
            onPathChange={setPath}
            defs={defs}
            effectiveConnector=""
            onEditQuery={(connector, queryName) => setEditQuery({ connector, queryName })}
            rootLabel={actionId}
            // Heading rendered by the outer <SubHead> above — don't let ActionTreeView paint a
            // second "Steps". Keep the hint (it has no outer equivalent).
            hint={t('settings.actions.stepsHint')}
            // what the service receives (its inputs, the answers to its questions) is offered where a step takes a value
            extraSourceOptions={serviceInputs}
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
            actions={steps} onChange={(nx) => patch({ steps: withCapturedResults(nx) })} path={path} onPathChange={setPath}
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
            <ObjectTitle label={typeof value?.label === 'string' ? value.label : null} id={actionId} />
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
        {failure && <div style={{ marginBottom: 12, flex: '0 0 auto' }}><SaveProblems failure={failure} /></div>}
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
