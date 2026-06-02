// Settings → Templates — operator-authored custom reports. Lists every
// [reports.<id>] block from config/reports.toml; lets the operator add /
// edit / delete templates and bind each to a connector query.
//
// On save (PUT /admin/config/reports/parsed), the backend validates every
// entry against the CustomReportTemplate Pydantic model, rewrites the file
// atomically via tomlkit, and rebuilds the in-memory report registry so the
// new templates surface in /api/reports immediately — no server restart.
//
// Phase 4a — minimal viable UI: a textarea editor for the Jinja markdown
// template, two select dropdowns for the data source (connector + query),
// and a simple param list with add/remove rows. Phase 4b upgrades the
// editor to Monaco and adds a "Test render" preview pane.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import {
  FileText, Plus, Trash2, Save, X, Edit3, Database,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Card, Field, Input, Select, Tag, Textarea,
  Overlay, Modal, ModalHeader, ModalBody, ModalFooter, SpinnerRing, Checkbox,
} from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

// --------------------------------------------------------------------------- //
// Wire shapes — mirror liberty.reports.custom.CustomReportTemplate
// --------------------------------------------------------------------------- //
type ParamType = 'int' | 'float' | 'bool' | 'string'
type OutputFormat = 'pdf' | 'markdown'

interface TemplateParam {
  name: string
  label: string
  type: ParamType
  required: boolean
  default: unknown
  description: string
}

interface TemplateData {
  connector: string
  query: string
}

interface CustomTemplate {
  id: string
  title: string
  description: string
  formats: OutputFormat[]
  params: TemplateParam[]
  data: TemplateData
  template_inline: string
  pdf_options: Record<string, unknown>
}

interface TemplatesResponse {
  path: string
  templates: CustomTemplate[]
  connectors: Record<string, string[]>
}

// --------------------------------------------------------------------------- //
// Styled bits
// --------------------------------------------------------------------------- //
const Shell = styled.div`display: flex; flex-direction: column; gap: 14px; flex: 1; min-height: 0;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const Path = styled.code`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  color: ${colors.text.muted}; padding: 2px 6px; border-radius: ${radius.sm};
  background: ${colors.bg.input};
`
const List = styled.div`display: flex; flex-direction: column; gap: 10px;`
const TemplateCard = styled(Card)`display: flex; flex-direction: column; gap: 8px;`
const CardTop = styled.div`display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap;`
const TplTitle = styled.span`font-weight: 600; color: ${colors.text.primary}; font-size: ${fontSize.md};`
const TplId = styled.span`font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.muted};`
const TplDesc = styled.div`color: ${colors.text.secondary}; font-size: ${fontSize.sm};`
const Actions = styled.div`display: flex; align-items: center; gap: 6px;`
const Empty = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
  padding: 28px 4px; text-align: center;
`
const ToolbarSpacer = styled.div`flex: 1;`
const Grid = styled.div`
  display: grid; grid-template-columns: 1fr 1fr; gap: 12px 18px;
  @media (max-width: 700px) { grid-template-columns: 1fr; }
`
const TallTextarea = styled(Textarea)`
  min-height: 220px; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
`
const ParamRow = styled.div`
  display: grid; grid-template-columns: 1.2fr 1.2fr 0.7fr 0.5fr auto;
  gap: 8px; align-items: center; padding: 6px 0;
  border-bottom: 1px dashed ${colors.border};
  &:last-child { border-bottom: none; }
`
const ParamsBox = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  padding: 10px 12px; background: ${colors.bg.input};
`
const Hint = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm};`

// --------------------------------------------------------------------------- //
// Defaults
// --------------------------------------------------------------------------- //
const EMPTY_TEMPLATE: CustomTemplate = {
  id: '',
  title: '',
  description: '',
  formats: ['pdf', 'markdown'],
  params: [],
  data: { connector: '', query: '' },
  template_inline: '## Report\n\n{% for row in ctx.data.rows %}\n- {{ row }}\n{% endfor %}\n',
  pdf_options: {},
}

const EMPTY_PARAM: TemplateParam = {
  name: '', label: '', type: 'string', required: true, default: null, description: '',
}

// --------------------------------------------------------------------------- //
// Page
// --------------------------------------------------------------------------- //
export default function ReportTemplatesBuilder() {
  const { t } = useTranslation()
  const [path, setPath] = useState('')
  const [templates, setTemplates] = useState<CustomTemplate[] | null>(null)
  const [connectors, setConnectors] = useState<Record<string, string[]>>({})
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [editing, setEditing] = useState<{ tpl: CustomTemplate; isNew: boolean } | null>(null)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setStatus(null)
    try {
      const r = await api.get<TemplatesResponse>('/admin/config/reports/parsed')
      setPath(r.path)
      setTemplates(r.templates)
      setConnectors(r.connectors)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const save = async (next: CustomTemplate[]) => {
    setBusy(true); setError(null); setStatus(null)
    try {
      const r = await api.put<{ saved: boolean; count: number; path: string }>(
        '/admin/config/reports/parsed',
        { templates: next },
      )
      setTemplates(next)
      setStatus(t('settings.templates.saved', { count: r.count }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const handleSaveEdit = async (edited: CustomTemplate) => {
    if (!templates) return
    let next: CustomTemplate[]
    if (editing?.isNew) {
      if (templates.some((t) => t.id === edited.id)) {
        setError(t('settings.templates.idExists', { id: edited.id }))
        return
      }
      next = [...templates, edited]
    } else {
      next = templates.map((t) => (t.id === edited.id ? edited : t))
    }
    await save(next)
    setEditing(null)
  }

  const handleDelete = async (id: string) => {
    if (!templates) return
    if (!window.confirm(t('settings.templates.confirmDelete', { id }))) return
    await save(templates.filter((t) => t.id !== id))
  }

  if (loading) return <SpinnerRing size={16} thickness={2} />

  return (
    <Shell>
      <Toolbar>
        <ToolbarLeft>
          <FileText size={16} />
          <strong>{t('settings.templates.title')}</strong>
          <Path>{path}</Path>
          {templates && <Tag>{t('settings.templates.count', { count: templates.length })}</Tag>}
        </ToolbarLeft>
        <Button
          $size="sm" $variant="primary"
          onClick={() => setEditing({ tpl: { ...EMPTY_TEMPLATE }, isNew: true })}
          disabled={busy}
        >
          <Plus size={14} /> {t('settings.templates.add')}
        </Button>
      </Toolbar>

      <Hint>{t('settings.templates.description')}</Hint>

      {error && <Banner $tone="error">{error}</Banner>}
      {status && <Banner $tone="ok">{status}</Banner>}

      {templates && templates.length === 0 && (
        <Empty>{t('settings.templates.empty')}</Empty>
      )}

      <List>
        {(templates ?? []).map((tpl) => (
          <TemplateCard key={tpl.id}>
            <CardTop>
              <TplTitle>{tpl.title || tpl.id}</TplTitle>
              <TplId>custom:{tpl.id}</TplId>
              <Tag $tone="blue">
                <Database size={12} /> {tpl.data.connector}.{tpl.data.query}
              </Tag>
              <ToolbarSpacer />
              <Actions>
                <Button
                  $size="sm" $variant="ghost"
                  onClick={() => setEditing({ tpl, isNew: false })}
                  disabled={busy}
                >
                  <Edit3 size={14} /> {t('common.edit')}
                </Button>
                <Button
                  $size="sm" $variant="ghost"
                  onClick={() => void handleDelete(tpl.id)}
                  disabled={busy}
                >
                  <Trash2 size={14} /> {t('common.delete')}
                </Button>
              </Actions>
            </CardTop>
            {tpl.description && <TplDesc>{tpl.description}</TplDesc>}
          </TemplateCard>
        ))}
      </List>

      {editing && (
        <TemplateEditDialog
          template={editing.tpl}
          isNew={editing.isNew}
          connectors={connectors}
          onCancel={() => setEditing(null)}
          onSave={handleSaveEdit}
          busy={busy}
        />
      )}
    </Shell>
  )
}

// --------------------------------------------------------------------------- //
// Edit dialog — covers add + edit
// --------------------------------------------------------------------------- //
function TemplateEditDialog({
  template, isNew, connectors, onCancel, onSave, busy,
}: {
  template: CustomTemplate
  isNew: boolean
  connectors: Record<string, string[]>
  onCancel: () => void
  onSave: (next: CustomTemplate) => void | Promise<void>
  busy: boolean
}) {
  const { t } = useTranslation()
  const [tpl, setTpl] = useState<CustomTemplate>(template)
  const [localError, setLocalError] = useState<string | null>(null)

  const update = <K extends keyof CustomTemplate>(k: K, v: CustomTemplate[K]) => {
    setTpl((p) => ({ ...p, [k]: v }))
  }
  const updateData = <K extends keyof TemplateData>(k: K, v: TemplateData[K]) => {
    setTpl((p) => ({ ...p, data: { ...p.data, [k]: v } }))
  }

  const connectorNames = useMemo(() => Object.keys(connectors).sort(), [connectors])
  const queryChoices = useMemo(
    () => (tpl.data.connector ? (connectors[tpl.data.connector] ?? []) : []),
    [tpl.data.connector, connectors],
  )

  const validate = (): string | null => {
    if (!tpl.id) return t('settings.templates.errors.idRequired')
    if (!/^[a-z0-9][a-z0-9-_]*$/.test(tpl.id)) {
      return t('settings.templates.errors.idShape')
    }
    if (!tpl.title) return t('settings.templates.errors.titleRequired')
    if (!tpl.data.connector || !tpl.data.query) {
      return t('settings.templates.errors.dataRequired')
    }
    if (!tpl.template_inline.trim()) return t('settings.templates.errors.templateRequired')
    // No duplicate param names
    const names = tpl.params.map((p) => p.name)
    if (new Set(names).size !== names.length) {
      return t('settings.templates.errors.dupParam')
    }
    return null
  }

  const handleSave = () => {
    const err = validate()
    if (err) { setLocalError(err); return }
    setLocalError(null)
    void onSave(tpl)
  }

  return (
    <Overlay onClick={busy ? undefined : onCancel}>
      <Modal
        style={{ width: 'min(820px, 95vw)', height: 'min(720px, 92vh)' }}
        onClick={(e) => e.stopPropagation()}
      >
        <ModalHeader>
          {isNew ? t('settings.templates.addTitle') : t('settings.templates.editTitle')}
        </ModalHeader>
        <ModalBody>
          {localError && <Banner $tone="error">{localError}</Banner>}

          <Grid>
            <Field label={t('settings.templates.fields.id') + ' *'} htmlFor="tpl-id">
              <Input
                id="tpl-id"
                type="text"
                value={tpl.id}
                onChange={(e) => update('id', e.target.value)}
                disabled={!isNew}
                placeholder="audit-summary"
              />
            </Field>
            <Field label={t('settings.templates.fields.title') + ' *'} htmlFor="tpl-title">
              <Input
                id="tpl-title"
                type="text"
                value={tpl.title}
                onChange={(e) => update('title', e.target.value)}
              />
            </Field>
            <Field label={t('settings.templates.fields.connector') + ' *'} htmlFor="tpl-conn">
              <Select
                id="tpl-conn"
                value={tpl.data.connector}
                onChange={(e) => {
                  updateData('connector', e.target.value)
                  updateData('query', '') // reset cascading query
                }}
              >
                <option value="">{t('common.pick')}</option>
                {connectorNames.map((n) => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </Select>
            </Field>
            <Field label={t('settings.templates.fields.query') + ' *'} htmlFor="tpl-query">
              <Select
                id="tpl-query"
                value={tpl.data.query}
                onChange={(e) => updateData('query', e.target.value)}
                disabled={!tpl.data.connector}
              >
                <option value="">{t('common.pick')}</option>
                {queryChoices.map((q) => (
                  <option key={q} value={q}>{q}</option>
                ))}
              </Select>
            </Field>
          </Grid>

          <Field label={t('settings.templates.fields.description')} htmlFor="tpl-desc">
            <Input
              id="tpl-desc"
              type="text"
              value={tpl.description}
              onChange={(e) => update('description', e.target.value)}
            />
          </Field>

          {/* Params */}
          <Field label={t('settings.templates.fields.params')}>
            <ParamsBox>
              {tpl.params.length === 0 && <Hint>{t('settings.templates.noParams')}</Hint>}
              {tpl.params.map((p, i) => (
                <ParamRow key={i}>
                  <Input
                    type="text" placeholder="name" value={p.name}
                    onChange={(e) => {
                      const next = [...tpl.params]
                      next[i] = { ...next[i], name: e.target.value }
                      update('params', next)
                    }}
                  />
                  <Input
                    type="text" placeholder="label" value={p.label}
                    onChange={(e) => {
                      const next = [...tpl.params]
                      next[i] = { ...next[i], label: e.target.value }
                      update('params', next)
                    }}
                  />
                  <Select
                    value={p.type}
                    onChange={(e) => {
                      const next = [...tpl.params]
                      next[i] = { ...next[i], type: e.target.value as ParamType }
                      update('params', next)
                    }}
                  >
                    <option value="string">string</option>
                    <option value="int">int</option>
                    <option value="float">float</option>
                    <option value="bool">bool</option>
                  </Select>
                  <Checkbox
                    checked={p.required}
                    onChange={(checked) => {
                      const next = [...tpl.params]
                      next[i] = { ...next[i], required: checked }
                      update('params', next)
                    }}
                    label={t('settings.templates.fields.required') as string}
                  />
                  <Button
                    $size="sm" $variant="ghost"
                    onClick={() => update('params', tpl.params.filter((_, j) => j !== i))}
                  >
                    <X size={14} />
                  </Button>
                </ParamRow>
              ))}
              <div style={{ marginTop: 8 }}>
                <Button
                  $size="sm" $variant="ghost"
                  onClick={() => update('params', [...tpl.params, { ...EMPTY_PARAM }])}
                >
                  <Plus size={14} /> {t('settings.templates.addParam')}
                </Button>
              </div>
            </ParamsBox>
          </Field>

          <Field
            label={t('settings.templates.fields.templateBody') + ' *'}
            htmlFor="tpl-body"
          >
            <TallTextarea
              id="tpl-body"
              value={tpl.template_inline}
              onChange={(e) => update('template_inline', e.target.value)}
              spellCheck={false}
            />
          </Field>
          <Hint>{t('settings.templates.templateHint')}</Hint>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel} disabled={busy}>
            {t('common.cancel')}
          </Button>
          <Button $size="sm" $variant="primary" onClick={handleSave} disabled={busy}>
            <Save size={14} /> {busy ? t('common.working') : t('common.save')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
