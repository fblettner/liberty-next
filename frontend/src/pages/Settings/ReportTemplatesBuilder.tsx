// Settings → Templates — operator-authored custom reports. Lists every
// [reports.<id>] block from config/reports.toml; lets the operator add /
// edit / delete templates and bind each to a connector query.
//
// On save (PUT /admin/config/reports/parsed), the backend validates every
// entry against the CustomReportTemplate Pydantic model, rewrites the file
// atomically via tomlkit, and rebuilds the in-memory report registry so the
// new templates surface in /api/reports immediately — no server restart.
//
// Phase 4b — Monaco editor for the Jinja markdown body, with a side-by-side
// "Test render" preview pane: hits POST /admin/config/reports/preview, which
// runs the bound query (capped at 200 rows) and renders the template in the
// same sandboxed Jinja env the runtime dispatcher uses. The preview shares
// the modal so operators can iterate template ↔ output without leaving the
// editor. Test params seed from the declared `default` values and stay
// editable in-place.
import '../../services/monaco' // side effect: register Monaco + the markdown language
import MonacoEditor, { type OnChange } from '@monaco-editor/react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import {
  FileText, Plus, Trash2, Save, X, Edit3, Database, Play, EyeOff,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Card, Field, Input, Select, Tag,
  Overlay, Modal, ModalHeader, ModalBody, ModalFooter, SpinnerRing, Checkbox,
  Centered, useIsLight,
} from '../../common'
import { Markdown } from '../../common/Markdown'
import { colors, fontSize, fonts, radius, EDITOR_FONT_PX } from '../../theme'

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

interface PreviewResponse {
  markdown: string
  row_count: number
  rendered_row_count: number
  column_count: number
  truncated: boolean
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
// Side-by-side: Monaco on the left, preview pane on the right. Collapses to
// single column on narrower viewports — preview moves below the editor.
const Split = styled.div<{ $hasPreview: boolean }>`
  display: grid;
  grid-template-columns: ${({ $hasPreview }) => ($hasPreview ? '1fr 1fr' : '1fr')};
  gap: 12px;
  @media (max-width: 900px) { grid-template-columns: 1fr; }
`
const EditorFrame = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;
  background: ${colors.bg.input}; min-height: 320px; height: 360px;
`
const PreviewPane = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.card}; min-height: 320px; max-height: 360px;
  overflow: auto; padding: 10px 14px;
`
const PreviewHeader = styled.div`
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
  padding-bottom: 6px; margin-bottom: 8px;
  border-bottom: 1px solid ${colors.border};
  font-size: ${fontSize.sm}; color: ${colors.text.muted};
`
const PreviewToolbar = styled.div`
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
`
const ExamplesBar = styled.div`
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  margin-bottom: 6px;
`
const ExamplesLabel = styled.span`
  font-size: ${fontSize.sm}; color: ${colors.text.muted};
  margin-right: 4px;
`
const ParamRow = styled.div`
  display: grid; grid-template-columns: 1.2fr 1.2fr 0.7fr 0.5fr auto;
  gap: 8px; align-items: center; padding: 6px 0;
  border-bottom: 1px dashed ${colors.border};
  &:last-child { border-bottom: none; }
`
const TestParamRow = styled.div`
  display: grid; grid-template-columns: 0.8fr 1.5fr; gap: 8px;
  align-items: center; padding: 4px 0;
`
const ParamsBox = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  padding: 10px 12px; background: ${colors.bg.input};
`
const Hint = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const ParamLabel = styled.label`
  font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  font-family: ${fonts.mono};
`

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

// Starter templates — surfaced as one-click buttons above the editor. Each
// covers a common "how do I lay out N rows?" pattern. Default empty body uses
// `list-of-rows`, so the button is also the canonical "reset to blank state".
//
// The bodies stay short on purpose: operators copy-edit them to match their
// data shape, so what they need most is a working *skeleton* that already
// references `ctx.data.rows` / `ctx.data.columns` / `ctx.params` correctly.
interface TemplateExample {
  id: string
  labelKey: string
  body: string
}
const TEMPLATE_EXAMPLES: TemplateExample[] = [
  {
    id: 'list-of-rows',
    labelKey: 'settings.templates.examples.list',
    body:
      '## {{ ctx.params.title or "Report" }}\n\n'
      + '{% if ctx.data.rows %}\n'
      + '{% for row in ctx.data.rows %}- {{ row }}\n{% endfor %}\n'
      + '{% else %}_No data._\n'
      + '{% endif %}\n',
  },
  {
    id: 'markdown-table',
    labelKey: 'settings.templates.examples.table',
    body:
      '## Results\n\n'
      + '{% if ctx.data.rows %}\n'
      + '| {% for c in ctx.data.columns %}{{ c.label or c.name }} | {% endfor %}\n'
      + '|{% for c in ctx.data.columns %} --- |{% endfor %}\n'
      + '{% for row in ctx.data.rows %}'
      + '| {% for c in ctx.data.columns %}{{ row[c.name] }} | {% endfor %}\n'
      + '{% endfor %}\n'
      + '\n_{{ ctx.data.rows | length }} row(s)_\n'
      + '{% else %}_No data._\n'
      + '{% endif %}\n',
  },
  {
    id: 'grouped-sections',
    labelKey: 'settings.templates.examples.grouped',
    body:
      '## Grouped report\n\n'
      + '{# Change "category" to the field you want to group by #}\n'
      + '{% for group, items in ctx.data.rows | groupby("category") %}\n'
      + '### {{ group }}\n\n'
      + '{% for row in items %}- {{ row.name }} — {{ row.value }}\n{% endfor %}\n\n'
      + '{% endfor %}\n',
  },
  {
    id: 'summary-and-table',
    labelKey: 'settings.templates.examples.summary',
    body:
      '## Summary\n\n'
      + '- Total rows: **{{ ctx.data.rows | length }}**\n'
      + '- Generated for: {{ ctx.params.app_name or "—" }}\n\n'
      + '## Details\n\n'
      + '{% if ctx.data.rows %}\n'
      + '| {% for c in ctx.data.columns %}{{ c.label or c.name }} | {% endfor %}\n'
      + '|{% for c in ctx.data.columns %} --- |{% endfor %}\n'
      + '{% for row in ctx.data.rows %}'
      + '| {% for c in ctx.data.columns %}{{ row[c.name] }} | {% endfor %}\n'
      + '{% endfor %}\n'
      + '{% else %}_No rows returned._\n'
      + '{% endif %}\n',
  },
]

// Coerce a param's declared default to the test-form's input string. The form
// edits strings; we re-coerce per type before posting to /preview.
function defaultAsString(p: TemplateParam): string {
  const d = p.default
  if (d === null || d === undefined) return ''
  if (typeof d === 'boolean') return d ? 'true' : 'false'
  return String(d)
}

// Coerce the operator-typed test value back to the typed payload sent to the
// backend. Best-effort — bad input falls through as a string and the backend
// surfaces the type mismatch in its error.
function coerceTestValue(raw: string, type: ParamType): unknown {
  if (raw === '') return null
  if (type === 'int') {
    const n = Number.parseInt(raw, 10)
    return Number.isNaN(n) ? raw : n
  }
  if (type === 'float') {
    const n = Number.parseFloat(raw)
    return Number.isNaN(n) ? raw : n
  }
  if (type === 'bool') {
    const v = raw.trim().toLowerCase()
    if (v === 'true' || v === '1' || v === 'yes') return true
    if (v === 'false' || v === '0' || v === 'no') return false
    return raw
  }
  return raw
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
  const isLight = useIsLight()
  const [tpl, setTpl] = useState<CustomTemplate>(template)
  const [localError, setLocalError] = useState<string | null>(null)
  // Preview state — `null` means "no preview yet"; the pane only renders once
  // the operator clicks Test render. Errors are surfaced in the same slot.
  const [preview, setPreview] = useState<PreviewResponse | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [previewBusy, setPreviewBusy] = useState(false)
  // Test params live in their own state — strings that get coerced just before
  // posting. Seeded from the declared `default` on each template param; when
  // the operator adds or removes a param the form re-syncs.
  const [testParams, setTestParams] = useState<Record<string, string>>(() => {
    const out: Record<string, string> = {}
    for (const p of template.params) out[p.name] = defaultAsString(p)
    return out
  })

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

  // Keep the test-params form in lock-step with the declared params: when a
  // param is added, seed it from its default; when one is renamed/removed the
  // stale key is dropped. This runs on every keystroke in the params editor —
  // cheap (small list, string compare).
  useEffect(() => {
    setTestParams((prev) => {
      const next: Record<string, string> = {}
      for (const p of tpl.params) {
        if (!p.name) continue
        next[p.name] = p.name in prev ? prev[p.name] : defaultAsString(p)
      }
      return next
    })
  }, [tpl.params])

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

  // Build the typed params payload from the test-form. Each param picks the
  // value from `testParams`; unknown / blank entries are forwarded as null
  // (server-side, the param will only be passed to the query if it's declared).
  const buildPreviewPayload = useCallback(() => {
    const params: Record<string, unknown> = {}
    for (const p of tpl.params) {
      if (!p.name) continue
      params[p.name] = coerceTestValue(testParams[p.name] ?? '', p.type)
    }
    return {
      template_inline: tpl.template_inline,
      data: { connector: tpl.data.connector, query: tpl.data.query },
      params,
    }
  }, [tpl, testParams])

  const handleTestRender = async () => {
    setPreviewBusy(true)
    setPreviewError(null)
    try {
      const r = await api.post<PreviewResponse>(
        '/admin/config/reports/preview',
        buildPreviewPayload(),
      )
      setPreview(r)
    } catch (e) {
      setPreview(null)
      setPreviewError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setPreviewBusy(false)
    }
  }

  const handleEditorChange: OnChange = (v) => update('template_inline', v ?? '')
  const showPreview = preview !== null || previewError !== null || previewBusy

  return (
    <Overlay onClick={busy ? undefined : onCancel}>
      <Modal
        style={{ width: 'min(1100px, 96vw)', height: 'min(820px, 94vh)' }}
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

          {/* Template body + live preview */}
          <Field
            label={t('settings.templates.fields.templateBody') + ' *'}
            htmlFor="tpl-body"
          >
            <ExamplesBar>
              <ExamplesLabel>{t('settings.templates.examples.label')}</ExamplesLabel>
              {TEMPLATE_EXAMPLES.map((ex) => (
                <Button
                  key={ex.id}
                  $size="sm" $variant="ghost"
                  type="button"
                  onClick={() => {
                    // Only confirm when the operator already has work-in-progress
                    // — empty / unchanged-default bodies overwrite silently.
                    const current = tpl.template_inline.trim()
                    const isPristine =
                      current === ''
                      || current === EMPTY_TEMPLATE.template_inline.trim()
                      || TEMPLATE_EXAMPLES.some((e) => current === e.body.trim())
                    if (!isPristine && !window.confirm(t('settings.templates.examples.replaceConfirm'))) return
                    update('template_inline', ex.body)
                    setPreview(null)
                    setPreviewError(null)
                  }}
                  title={t('settings.templates.examples.insertTitle') as string}
                >
                  {t(ex.labelKey)}
                </Button>
              ))}
            </ExamplesBar>
            <Split $hasPreview={showPreview}>
              <EditorFrame>
                <MonacoEditor
                  height="100%"
                  language="markdown"
                  theme={isLight ? 'light' : 'vs-dark'}
                  value={tpl.template_inline}
                  loading={<Centered />}
                  onChange={handleEditorChange}
                  options={{
                    fontSize: EDITOR_FONT_PX,
                    fontFamily: fonts.mono,
                    minimap: { enabled: false },
                    scrollBeyondLastLine: false,
                    tabSize: 2,
                    renderWhitespace: 'boundary',
                    wordWrap: 'on',
                    automaticLayout: true,
                    lineNumbersMinChars: 3,
                    folding: false,
                    quickSuggestions: { other: 'on', strings: false, comments: false },
                    wordBasedSuggestions: 'off',
                  }}
                />
              </EditorFrame>
              {showPreview && (
                <PreviewPane>
                  <PreviewHeader>
                    <span>
                      {previewBusy && <SpinnerRing size={12} thickness={2} />}
                      {!previewBusy && preview && (
                        <Tag $tone="green">
                          {t('settings.templates.previewMeta', {
                            rendered: preview.rendered_row_count,
                            total: preview.row_count,
                          })}
                        </Tag>
                      )}
                      {!previewBusy && previewError && (
                        <Tag $tone="red">{t('settings.templates.previewFailed')}</Tag>
                      )}
                    </span>
                    <Button
                      $size="sm" $variant="ghost"
                      onClick={() => { setPreview(null); setPreviewError(null) }}
                      title={t('settings.templates.hidePreview')}
                    >
                      <EyeOff size={12} />
                    </Button>
                  </PreviewHeader>
                  {previewError && <Banner $tone="error">{previewError}</Banner>}
                  {preview && !previewError && (
                    <>
                      {preview.truncated && (
                        <Banner $tone="info">
                          {t('settings.templates.previewTruncated', {
                            rendered: preview.rendered_row_count,
                            total: preview.row_count,
                          })}
                        </Banner>
                      )}
                      <Markdown>{preview.markdown}</Markdown>
                    </>
                  )}
                </PreviewPane>
              )}
            </Split>
          </Field>
          <Hint>{t('settings.templates.templateHint')}</Hint>

          {/* Test params + Test render */}
          {tpl.params.length > 0 && (
            <Field label={t('settings.templates.testParams')}>
              <ParamsBox>
                {tpl.params.filter((p) => p.name).map((p) => (
                  <TestParamRow key={p.name}>
                    <ParamLabel htmlFor={`tpl-test-${p.name}`}>
                      {p.label || p.name} <span style={{ color: colors.text.muted }}>({p.type})</span>
                    </ParamLabel>
                    <Input
                      id={`tpl-test-${p.name}`}
                      type="text"
                      value={testParams[p.name] ?? ''}
                      placeholder={defaultAsString(p) || t('settings.templates.testParamPlaceholder') as string}
                      onChange={(e) =>
                        setTestParams((s) => ({ ...s, [p.name]: e.target.value }))
                      }
                    />
                  </TestParamRow>
                ))}
              </ParamsBox>
            </Field>
          )}

          <PreviewToolbar>
            <Button
              $size="sm" $variant="ghost"
              onClick={() => void handleTestRender()}
              disabled={previewBusy || !tpl.template_inline.trim()}
              title={t('settings.templates.testRenderTitle')}
            >
              <Play size={14} /> {previewBusy ? t('common.working') : t('settings.templates.testRender')}
            </Button>
            <Hint>{t('settings.templates.testRenderHint')}</Hint>
          </PreviewToolbar>
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
