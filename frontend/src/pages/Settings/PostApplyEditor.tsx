// Editor for the run-once post-apply steps (config/app.toml [changesets].post_apply) — rendered as a
// collapsible section in the Changes → Apply tab. Each step runs ONCE on the target after a bundle
// of its `application` connector applies (e.g. a JDE security remerge). Reads/writes via
// /admin/changesets/config/post-apply; saving live-updates the running settings (no restart).
import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { ChevronRight, ChevronDown, Plus, Trash2, Save, Settings2 } from 'lucide-react'
import { Banner, Button, Input, Select, SearchSelect, type SearchSelectOption } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'

type StepType = 'call_plugin' | 'call_api' | 'run_query'
type Step = { id: string; type: StepType; connector?: string | null; target: string; label?: string | null; params?: Record<string, unknown> }
type Config = { post_apply: Step[]; connectors: string[]; types: StepType[] }

// target field label per type — what `target` means.
const TARGET_LABEL: Record<StepType, string> = {
  call_plugin: 'module:function', call_api: 'endpoint', run_query: 'query',
}

const Panel = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; margin-top: 14px; background: ${colors.bg.input};
  & > .head { display: flex; align-items: center; gap: 8px; width: 100%; text-align: left; cursor: pointer;
    background: none; border: none; padding: 9px 11px; color: ${colors.text.secondary};
    font-size: ${fontSize.sm}; font-weight: 600; font-family: ${fonts.sans}; }
  & > .head:hover { color: ${colors.text.primary}; }
  & > .head .count { color: ${colors.text.muted}; font-weight: 400; }
  & .body { padding: 4px 11px 11px; border-top: 1px solid ${colors.border}; }
  & .hint { color: ${colors.text.muted}; font-size: ${fontSize.micro}; margin: 6px 0 10px; line-height: 1.5; }
`
const Row = styled.div`
  display: grid; grid-template-columns: 1.1fr 1fr 1fr 1.4fr 1.2fr auto; gap: 6px; align-items: center; margin-bottom: 6px;
  @media (max-width: 1100px) { grid-template-columns: 1fr 1fr; }
`
const Lbl = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; margin-bottom: 2px;`
const Del = styled.button`
  background: none; border: 1px solid ${colors.border}; border-radius: ${radius.sm}; cursor: pointer;
  color: ${colors.red.main}; display: inline-flex; align-items: center; padding: 5px; height: 30px;
  &:hover { border-color: ${colors.red.main}; }
`

export function PostApplyEditor({ onSaved }: { onSaved?: () => void }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [cfg, setCfg] = useState<Config | null>(null)
  const [steps, setSteps] = useState<Step[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  const load = useCallback(() =>
    api.get<Config>('/admin/changesets/config/post-apply')
      .then((d) => { setCfg(d); setSteps(d.post_apply ?? []) })
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e))), [])
  useEffect(() => { if (open && !cfg) void load() }, [open, cfg, load])

  // Plugin callables for the call_plugin target picker — same discovered j_* catalog the action /
  // job designers read (allowCustom lets you type one the walk didn't surface). Fetched on open.
  const [pluginOpts, setPluginOpts] = useState<SearchSelectOption[]>([])
  useEffect(() => {
    if (!open) return
    let cancelled = false
    api.get<{ callables: { callable: string; docstring: string | null }[] }>('/api/plugins/callables')
      .then((r) => { if (!cancelled) setPluginOpts((r.callables ?? []).map((c) => ({ value: c.callable, label: c.docstring || c.callable, mono: c.callable }))) })
      .catch(() => { /* leave empty; the picker stays freeform */ })
    return () => { cancelled = true }
  }, [open])

  const update = (i: number, patch: Partial<Step>) =>
    setSteps((s) => s.map((st, j) => (j === i ? { ...st, ...patch } : st)))
  const add = () => setSteps((s) => [...s, { id: '', type: 'call_plugin', target: '' }])
  const remove = (i: number) => setSteps((s) => s.filter((_, j) => j !== i))

  const save = async () => {
    setBusy(true); setError(null); setSaved(false)
    try {
      await api.put('/admin/changesets/config/post-apply', { post_apply: steps })
      setSaved(true)
      onSaved?.()
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) }
    finally { setBusy(false) }
  }

  const connectorOpts = (cfg?.connectors ?? []).map((c) => ({ value: c, label: c }))

  return (
    <Panel>
      <button type="button" className="head" onClick={() => setOpen((v) => !v)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <Settings2 size={14} />
        <span>{t('settings.changes.postApplyTitle', 'Post-apply steps')}</span>
        {steps.length > 0 && <span className="count">{steps.length}</span>}
      </button>
      {open && (
        <div className="body">
          <div className="hint">{t('settings.changes.postApplyHint',
            'Run-once steps appended to a connector’s exported bundle and executed once on the target after every change op applies — e.g. a JD Edwards security remerge. The batch alternative to a per-row change_replay action.')}</div>
          {error && <Banner $tone="error" style={{ marginBottom: 8 }}>{error}</Banner>}
          {saved && <Banner $tone="ok" style={{ marginBottom: 8 }}>{t('common.done', 'Done')}</Banner>}
          {steps.map((st, i) => (
            <Row key={i}>
              <div>
                <Lbl>{t('settings.changes.paId', 'Id')}</Lbl>
                <Input value={st.id} onChange={(e) => update(i, { id: e.target.value })} placeholder="remerge" />
              </div>
              <div>
                <Lbl>{t('settings.changes.paType', 'Type')}</Lbl>
                <Select value={st.type} onChange={(e) => update(i, { type: e.target.value as StepType })}>
                  {(cfg?.types ?? ['call_plugin', 'call_api', 'run_query']).map((ty) => <option key={ty} value={ty}>{ty}</option>)}
                </Select>
              </div>
              <div>
                <Lbl>{t('settings.changes.paConnector', 'Connector')}</Lbl>
                <Select value={st.connector ?? ''} onChange={(e) => update(i, { connector: e.target.value || null })}
                  disabled={st.type === 'call_plugin'}>
                  <option value="">{t('settings.changes.paSameAsApp', '(application)')}</option>
                  {connectorOpts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </Select>
              </div>
              <div>
                <Lbl>{TARGET_LABEL[st.type]}</Lbl>
                {st.type === 'call_plugin' ? (
                  <SearchSelect value={st.target} onChange={(v) => update(i, { target: v })}
                    options={pluginOpts} allowCustom placeholder={TARGET_LABEL.call_plugin} />
                ) : (
                  <Input value={st.target} onChange={(e) => update(i, { target: e.target.value })} placeholder={TARGET_LABEL[st.type]} />
                )}
              </div>
              <div>
                <Lbl>{t('settings.changes.paLabel', 'Label')}</Lbl>
                <Input value={st.label ?? ''} onChange={(e) => update(i, { label: e.target.value || null })} />
              </div>
              <Del type="button" title={t('common.delete', 'Delete')} onClick={() => remove(i)}><Trash2 size={13} /></Del>
            </Row>
          ))}
          <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
            <Button $size="sm" $variant="ghost" onClick={add} disabled={!cfg}>
              <Plus size={13} /> {t('settings.changes.paAdd', 'Add step')}
            </Button>
            <Button $size="sm" $variant="primary" onClick={() => void save()} disabled={busy || !cfg}>
              <Save size={13} /> {t('common.save', 'Save')}
            </Button>
          </div>
        </div>
      )}
    </Panel>
  )
}

export default PostApplyEditor
