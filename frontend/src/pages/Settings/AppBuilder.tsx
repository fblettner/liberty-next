// Settings → App — the master settings file (``config/app.toml``). Edits a curated subset of
// AppSettings + AISettings; secrets (``${ENV_VAR}`` bound — jwt_secret, master_key, license
// key, OIDC client secret, AI api_key) are NEVER exposed here. Path / mount fields are
// out of scope too (operators don't move them from the UI). Saving rewrites only the
// ``[app]`` and ``[ai]`` tables on disk; other sections of app.toml are preserved verbatim.
// Changes don't take effect live — the file is loaded once at startup — so the save banner
// reminds the operator to restart.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Save, Undo2, Settings as SettingsIcon, Sparkles, AlertTriangle, X } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Card, Checkbox, Field, Input, SpinnerRing, Tag, Textarea } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

interface AppSection {
  name: string
  host: string
  port: number
  log_level: string
  hot_reload: boolean
  default_language: string
}
interface AiSection {
  enabled: boolean
  model: string
  max_tokens: number
  max_iterations: number
  system_prompt: string
  thinking: boolean
  effort: string
  request_timeout: number
  connector_tools: boolean
  api_tool: boolean
  allowed_connectors: string[]
  web_fetch_domains: string[]
  web_fetch_max_uses: number
}
interface Choices {
  log_levels: string[]
  effort_levels: string[]
  connectors: string[]
}
interface AppParsed { path: string; app: AppSection; ai: AiSection; choices: Choices }

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const Grid = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 14px; align-items: start;`
const Panel = styled(Card)`padding: 0; display: flex; flex-direction: column; overflow: hidden;`
const PanelHeader = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 12px 14px;
  border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600;
  color: ${colors.text.primary};
  text-transform: uppercase; letter-spacing: 0.06em;
  & svg { color: ${colors.blue.main}; }
`
const PanelBody = styled.div`padding: 14px; display: flex; flex-direction: column; gap: 12px;`
const Select = styled.select`
  height: 32px; padding: 0 8px; border-radius: ${radius.md}; border: 1px solid ${colors.border};
  background: ${colors.bg.input}; color: ${colors.text.primary}; font-family: ${fonts.sans}; font-size: ${fontSize.base};
`
const Hint = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-height: 32px;`
const RowFlex = styled.div`display: flex; gap: 10px; align-items: center; flex-wrap: wrap;`

const DEFAULTS: { app: AppSection; ai: AiSection } = {
  app: { name: 'Liberty Next', host: '0.0.0.0', port: 8000, log_level: 'info', hot_reload: false, default_language: 'en' },
  ai: {
    enabled: true, model: 'claude-opus-4-7', max_tokens: 8192, max_iterations: 8, system_prompt: '',
    thinking: false, effort: '', request_timeout: 120,
    connector_tools: true, api_tool: false, allowed_connectors: [],
    web_fetch_domains: [], web_fetch_max_uses: 5,
  },
}

export default function AppBuilder() {
  const { t } = useTranslation()
  const [app, setApp] = useState<AppSection>(DEFAULTS.app)
  const [ai, setAi] = useState<AiSection>(DEFAULTS.ai)
  const [choices, setChoices] = useState<Choices>({ log_levels: [], effort_levels: [], connectors: [] })
  const [path, setPath] = useState('')
  const [original, setOriginal] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [restartHint, setRestartHint] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setError(null); setStatus(null); setRestartHint(false)
    try {
      const r = await api.get<AppParsed>('/admin/config/app/parsed')
      setPath(r.path)
      setApp(r.app)
      setAi(r.ai)
      setChoices(r.choices)
      setOriginal(JSON.stringify({ app: r.app, ai: r.ai }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const dirty = useMemo(
    () => JSON.stringify({ app, ai }) !== original,
    [app, ai, original],
  )

  const discard = () => {
    if (!original) return
    try {
      const seed = JSON.parse(original) as { app: AppSection; ai: AiSection }
      setApp(seed.app); setAi(seed.ai); setStatus(null); setError(null); setRestartHint(false)
    } catch { /* ignore */ }
  }

  const save = async () => {
    setBusy(true); setError(null); setStatus(null)
    try {
      const r = await api.put<{ saved: boolean; requires_restart?: boolean }>('/admin/config/app/parsed', { app, ai })
      setOriginal(JSON.stringify({ app, ai }))
      setStatus(t('settings.app.saved', 'Saved to {{path}}.', { path }))
      if (r?.requires_restart) setRestartHint(true)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (loading) return <SpinnerRing size={16} thickness={2} />

  return (
    <Shell>
      <Toolbar>
        <ToolbarLeft>
          {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
          {status && !restartHint && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
        </ToolbarLeft>
        <ToolbarRight>
          <Button $variant="ghost" $size="sm" onClick={discard} disabled={busy || !dirty}>
            <Undo2 size={13} /> {t('common.discard', 'Discard')}
          </Button>
          <Button $variant="primary" $size="sm" onClick={() => void save()} disabled={busy || !dirty}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
          </Button>
        </ToolbarRight>
      </Toolbar>

      <Hint>
        {t(
          'settings.app.intro',
          'Master settings (app.toml). Secrets and storage paths stay env / disk only and aren\'t shown here. Changes need a server restart to take effect.',
        )}
        {path && <> · <span style={{ fontFamily: fonts.mono }}>{path}</span></>}
      </Hint>
      {error && <Banner $tone="error">{error}</Banner>}
      {restartHint && (
        <Banner $tone="info">
          <AlertTriangle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
          {t('settings.app.restart', 'Saved. Restart the server to apply the new settings.')}
        </Banner>
      )}

      <Grid>
        <Panel>
          <PanelHeader><SettingsIcon size={14} /> {t('settings.app.appSection', 'App')}</PanelHeader>
          <PanelBody>
            <Field label={t('settings.app.name', 'App name')}>
              <Input value={app.name} onChange={(e) => setApp({ ...app, name: e.target.value })} />
            </Field>
            <RowFlex>
              <Field label={t('settings.app.host', 'Bind host')}>
                <Input value={app.host} onChange={(e) => setApp({ ...app, host: e.target.value })} style={{ width: 200 }} />
              </Field>
              <Field label={t('settings.app.port', 'Port')}>
                <Input type="number" min={1} max={65535} value={app.port}
                  onChange={(e) => setApp({ ...app, port: Number(e.target.value) || 0 })} style={{ width: 110 }} />
              </Field>
            </RowFlex>
            <RowFlex>
              <Field label={t('settings.app.logLevel', 'Log level')}>
                <Select value={app.log_level} onChange={(e) => setApp({ ...app, log_level: e.target.value })}>
                  {choices.log_levels.map((l) => <option key={l} value={l}>{l}</option>)}
                </Select>
              </Field>
              <Field label={t('settings.app.defaultLanguage', 'Default language')}>
                <Input value={app.default_language}
                  onChange={(e) => setApp({ ...app, default_language: e.target.value })} style={{ width: 110 }} />
              </Field>
            </RowFlex>
            <Checkbox label={t('settings.app.hotReload', 'Hot-reload config TOML files on change')}
              checked={app.hot_reload} onChange={(c) => setApp({ ...app, hot_reload: c })} />
          </PanelBody>
        </Panel>

        <Panel>
          <PanelHeader><Sparkles size={14} /> {t('settings.app.aiSection', 'AI Assistant')}</PanelHeader>
          <PanelBody>
            <Checkbox label={t('settings.app.aiEnabled', 'Enabled')}
              checked={ai.enabled} onChange={(c) => setAi({ ...ai, enabled: c })} />
            <RowFlex>
              <Field label={t('settings.app.aiModel', 'Model')}>
                <Input value={ai.model} onChange={(e) => setAi({ ...ai, model: e.target.value })} style={{ width: 240 }} />
              </Field>
              <Field label={t('settings.app.aiEffort', 'Effort')}>
                <Select value={ai.effort} onChange={(e) => setAi({ ...ai, effort: e.target.value })}>
                  {choices.effort_levels.map((l) => <option key={l || '_'} value={l}>{l || '(default)'}</option>)}
                </Select>
              </Field>
            </RowFlex>
            <RowFlex>
              <Field label={t('settings.app.aiMaxTokens', 'Max tokens')}>
                <Input type="number" min={1} value={ai.max_tokens}
                  onChange={(e) => setAi({ ...ai, max_tokens: Number(e.target.value) || 0 })} style={{ width: 130 }} />
              </Field>
              <Field label={t('settings.app.aiMaxIter', 'Max tool iterations')}>
                <Input type="number" min={1} value={ai.max_iterations}
                  onChange={(e) => setAi({ ...ai, max_iterations: Number(e.target.value) || 0 })} style={{ width: 130 }} />
              </Field>
              <Field label={t('settings.app.aiTimeout', 'Request timeout (s)')}>
                <Input type="number" min={1} value={ai.request_timeout}
                  onChange={(e) => setAi({ ...ai, request_timeout: Number(e.target.value) || 0 })} style={{ width: 130 }} />
              </Field>
            </RowFlex>
            <Checkbox label={t('settings.app.aiThinking', 'Adaptive thinking')}
              checked={ai.thinking} onChange={(c) => setAi({ ...ai, thinking: c })} />
            <Field label={t('settings.app.aiSystemPrompt', 'System prompt (empty → built-in default)')}>
              <Textarea rows={5} value={ai.system_prompt}
                onChange={(e) => setAi({ ...ai, system_prompt: e.target.value })} />
            </Field>

            <Hint>{t('settings.app.aiTools', 'Tool exposure — what the assistant can call.')}</Hint>
            <Checkbox label={t('settings.app.aiConnectorTools', 'Connector tools (list_connectors, sql_query — read-only)')}
              checked={ai.connector_tools} onChange={(c) => setAi({ ...ai, connector_tools: c })} />
            <Checkbox label={t('settings.app.aiApiTool', 'API tool (api_call — endpoints may have side effects)')}
              checked={ai.api_tool} onChange={(c) => setAi({ ...ai, api_tool: c })} />

            <Field label={t('settings.app.aiAllowedConnectors', 'Allowed connectors (empty → all)')}>
              <ConnectorPicker
                value={ai.allowed_connectors}
                options={choices.connectors}
                onChange={(next) => setAi({ ...ai, allowed_connectors: next })}
              />
            </Field>

            <Hint>{t('settings.app.aiWebFetch', 'Server-side web fetch (Anthropic-hosted). Disabled until at least one domain is allowed.')}</Hint>
            <Field label={t('settings.app.aiWebDomains', 'Allowed domains')}>
              <ChipsInput
                value={ai.web_fetch_domains}
                onChange={(next) => setAi({ ...ai, web_fetch_domains: next })}
                placeholder="example.com"
              />
            </Field>
            <Field label={t('settings.app.aiWebMaxUses', 'Max fetches per turn')}>
              <Input type="number" min={1} value={ai.web_fetch_max_uses}
                onChange={(e) => setAi({ ...ai, web_fetch_max_uses: Number(e.target.value) || 0 })} style={{ width: 130 }} />
            </Field>
          </PanelBody>
        </Panel>
      </Grid>
    </Shell>
  )
}

// ── multi-select chips ────────────────────────────────────────────────────────────────────

const RemoveBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center;
  border: 0; background: transparent; color: ${colors.text.muted};
  cursor: pointer; padding: 0 0 0 4px; margin: 0;
  &:hover { color: ${colors.red.main}; }
`

function Chip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <Tag>
      {label}
      <RemoveBtn onClick={onRemove} aria-label="Remove">
        <X size={11} />
      </RemoveBtn>
    </Tag>
  )
}

function ConnectorPicker({ value, options, onChange }: { value: string[]; options: string[]; onChange: (next: string[]) => void }) {
  const [pick, setPick] = useState('')
  const remaining = options.filter((o) => !value.includes(o))
  const add = (name: string) => { if (name && !value.includes(name)) onChange([...value, name]); setPick('') }
  const remove = (name: string) => onChange(value.filter((v) => v !== name))
  return (
    <Chips>
      {value.map((v) => <Chip key={v} label={v} onRemove={() => remove(v)} />)}
      {remaining.length > 0 && (
        <Select value={pick} onChange={(e) => add(e.target.value)}>
          <option value="">+ add…</option>
          {remaining.map((o) => <option key={o} value={o}>{o}</option>)}
        </Select>
      )}
    </Chips>
  )
}

function ChipsInput({ value, onChange, placeholder }: { value: string[]; onChange: (next: string[]) => void; placeholder?: string }) {
  const [draft, setDraft] = useState('')
  const commit = () => {
    const t = draft.trim()
    if (t && !value.includes(t)) onChange([...value, t])
    setDraft('')
  }
  const remove = (name: string) => onChange(value.filter((v) => v !== name))
  return (
    <Chips>
      {value.map((v) => <Chip key={v} label={v} onRemove={() => remove(v)} />)}
      <Input
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); commit() }
        }}
        onBlur={commit}
        style={{ width: 220 }}
      />
    </Chips>
  )
}
