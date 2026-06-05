// Full-height editor for ONE shared action — the same modal shell as ScreenDesignerModal. Fetches
// actions.toml + schema on open, edits the action's label / description / params / steps (steps via
// the existing ActionTreeView), and PUTs the merged actions.toml + reloads on Save. The action id
// is the dict key and is read-only here (renaming needs the cross-ref cascade — a list-level op).
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Save, X, Maximize2, Minimize2, Plus, Trash2 } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Centered, Field, FrameworkEnumsContext, Input, ModalFooter, ModalHeader,
  Overlay, Row as FlexRow, SearchSelect, SpinnerRing, VisualBuilderModal, useModals,
  type FrameworkEnums, type JsonSchema,
} from '../../common'
import styled from '@emotion/styled'
import ActionTreeView from './ActionTreeView'
import { builtinSourceOptions } from './actionCandidates'
import type { ActionPath } from './actionPath'
import { colors, fontSize, fonts, radius } from '../../theme'

type Row = Record<string, unknown>

const BUILTIN_VALUE_OPTIONS = builtinSourceOptions()

const Body = styled.div`
  flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 20px;
  display: flex; flex-direction: column; gap: 14px;
`
const Two = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 10px;`
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
  const [original, setOriginal] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fullscreen, setFullscreen] = useState(true)
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
        else setError(t('settings.actions.notFound', 'Shared action "{{id}}" not found.', { id: actionId }))
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [actionId, t])

  const dirty = !!value && JSON.stringify(value) !== original

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
      title: t('settings.screens.designer.unsavedTitle', 'Unsaved changes'),
      message: t('settings.actions.unsavedMsg', 'You have unsaved changes in this action. Save them, discard them, or keep editing?'),
      options: [
        { value: 'discard', label: t('settings.screens.designer.discard', 'Discard'), variant: 'danger' },
        { value: 'save', label: t('common.save'), variant: 'primary' },
        { value: 'keep', label: t('settings.screens.designer.keepEditing', 'Keep editing'), variant: 'ghost', autoFocus: true },
      ],
      cancelValue: 'keep',
    })
    if (choice === 'keep' || choice == null) return
    if (choice === 'discard') onClose()
    else if (choice === 'save') await save()
  }

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') void cancel() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty])

  const params = Array.isArray(value?.params) ? (value!.params as Row[]) : []
  const setParams = (nx: Row[]) => patch({ params: nx })
  const steps = Array.isArray(value?.steps) ? (value!.steps as Row[]) : []

  const inner = (!doc) ? <Centered />
    : error ? <Banner $tone="error">{error}</Banner>
    : !value ? <Centered />
    : (
      <Body>
        <Two>
          <Field label={t('settings.actions.label', 'Label')}>
            <Input value={typeof value.label === 'string' ? value.label : ''} placeholder={actionId}
              onChange={(e) => patch({ label: e.target.value })} />
          </Field>
          <Field label={t('settings.actions.description', 'Description')}>
            <Input value={typeof value.description === 'string' ? value.description : ''}
              onChange={(e) => patch({ description: e.target.value })} />
          </Field>
        </Two>

        <div>
          <SubHead>{t('settings.actions.params', 'Inputs')}</SubHead>
          <Sub>{t('settings.actions.paramsHint',
            'The inputs this action needs. A step reads one as INPUT.<name>; a screen binds its source via call_action. The default is used when the caller doesn’t bind it — a literal (Y / QPRINT) or a built-in: #LOGIN_USER#, #SYSDATE#, #NOW#, #CURRENT_TIME#, #LANGUAGE#.')}</Sub>
          {params.map((p, i) => (
            <ParamRow key={i}>
              <Input value={typeof p.name === 'string' ? p.name : ''}
                placeholder={t('settings.actions.paramName', 'name')}
                onChange={(e) => { const nx = params.slice(); nx[i] = { ...nx[i], name: e.target.value }; setParams(nx) }} />
              <SearchSelect value={typeof p.default === 'string' ? p.default : ''}
                options={BUILTIN_VALUE_OPTIONS} allowCustom
                anyLabel={t('common.none', '(none)')}
                placeholder={t('settings.actions.paramDefault', 'default — literal or built-in')}
                onChange={(v) => { const nx = params.slice(); const cur = { ...nx[i] }; if (v) cur.default = v; else delete cur.default; nx[i] = cur; setParams(nx) }} />
              <SmallRemove type="button" onClick={() => { const nx = params.slice(); nx.splice(i, 1); setParams(nx) }}><Trash2 size={14} /></SmallRemove>
            </ParamRow>
          ))}
          <Button $variant="ghost" $size="sm" onClick={() => setParams([...params, { name: '' }])}>
            <Plus size={13} /> {t('settings.actions.addParam', 'Add input')}
          </Button>
        </div>

        <div>
          <SubHead>{t('settings.actions.steps', 'Steps')}</SubHead>
          <ActionTreeView
            actions={steps}
            onChange={(nx) => patch({ steps: nx })}
            path={path}
            onPathChange={setPath}
            defs={defs}
            effectiveConnector=""
            onEditQuery={() => { /* edit queries in the Connectors tab */ }}
            rootLabel={actionId}
            heading={t('settings.actions.steps', 'Steps')}
            hint={t('settings.actions.stepsHint', 'The ordered steps this action runs. Pin each query/plugin step’s connector explicitly (shared actions aren’t tied to one app).')}
          />
        </div>
      </Body>
    )

  return createPortal(
    <FrameworkEnumsContext.Provider value={enums}>
      <Overlay>
        <VisualBuilderModal $fullscreen={fullscreen} onClick={(e) => e.stopPropagation()}>
          <ModalHeader>
            <FlexRow gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
              <span>
                {t('settings.actions.designerTitle', 'Shared action')} ·{' '}
                <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontWeight: 400 }}>[actions.{actionId}]</span>
              </span>
              <FlexRow gap={8} style={{ alignItems: 'center' }}>
                {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved', 'Unsaved changes')}</span>}
                <Button $variant="ghost" $size="sm" onClick={() => setFullscreen((v) => !v)}
                  title={fullscreen ? t('common.restore', 'Restore') : t('common.maximize', 'Maximize')} aria-pressed={fullscreen}>
                  {fullscreen ? <Minimize2 size={13} /> : <Maximize2 size={13} />}
                </Button>
                <Button $variant="ghost" $size="sm" onClick={() => void cancel()}>
                  <X size={13} /> {t('common.cancel')}
                </Button>
              </FlexRow>
            </FlexRow>
          </ModalHeader>
          {inner}
          <ModalFooter>
            <FlexRow gap={8}>
              <Button onClick={() => void cancel()} $variant="ghost" $size="sm" disabled={busy}>
                <X size={13} /> {t('common.cancel')}
              </Button>
              <Button $variant="primary" $size="sm" disabled={busy || !dirty} onClick={() => void save()}>
                {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
              </Button>
            </FlexRow>
          </ModalFooter>
        </VisualBuilderModal>
      </Overlay>
    </FrameworkEnumsContext.Provider>,
    document.body,
  )
}

export default ActionDesignerModal
