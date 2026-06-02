// Settings → App — the master settings file (``config/app.toml``). Edits a curated subset of
// AppSettings + AISettings + LicenseSettings + OIDCSettings. Sensitive fields (license.key,
// ai.api_key, oidc.client_secret) round-trip masked: GET returns them empty with a
// ``<field>_set: bool`` indicator; PUT encrypts new plaintext with the install master key
// (``ENC:…`` on disk). The other secrets (jwt_secret, master_key) stay env-only. Saving
// rewrites only the edited tables on disk; other sections are preserved verbatim. Changes
// don't take effect live — app.toml is loaded once at startup — so a restart banner is shown.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import {
  Save, Undo2, Settings as SettingsIcon, Sparkles, AlertTriangle, X,
  ChevronDown, ChevronRight, KeyRound, ShieldCheck, Pencil, FileText, RefreshCw,
} from 'lucide-react'
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
  scaffold_tools: boolean
  allowed_connectors: string[]
  web_fetch_domains: string[]
  web_fetch_max_uses: number
  api_key: string
  api_key_set?: boolean
}
interface LicenseSection {
  key: string
  key_set?: boolean
}
interface OidcSection {
  enabled: boolean
  discovery_url: string
  client_id: string
  client_secret: string
  client_secret_set?: boolean
  scopes: string
  username_claim: string
  email_claim: string
  name_claim: string
  redirect_url: string
  frontend_redirect: string
}
interface ModelChoice { id: string; label: string }
interface Choices {
  log_levels: string[]
  effort_levels: string[]
  connectors: string[]
  models: ModelChoice[]
}
interface AppParsed {
  path: string
  app: AppSection
  ai: AiSection
  license: LicenseSection
  oidc: OidcSection
  choices: Choices
}
// PDF branding (``[reports.branding]`` in app.toml) — applied to every report
// rendered through the framework. Layered precedence at render time:
//   framework defaults  <  this section  <  per-report pdf_options
// Lives next to the rest of the app settings (it's in the same file), surfaced
// as its own collapsible panel below.
interface BrandingFields {
  author: string
  primary_color: string
  primary_color_light: string
  cover_eyebrow: string
  cover_ref: string
  footer_left: string
}
interface BrandingResponse {
  path: string
  branding: BrandingFields
  defaults: BrandingFields
}

const Shell = styled.div`display: flex; flex-direction: column; gap: 12px; flex: 1; min-height: 0;`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const Stack = styled.div`display: flex; flex-direction: column; gap: 10px; align-items: stretch;`
const Panel = styled(Card)`padding: 0; display: flex; flex-direction: column; overflow: hidden;`
const PanelHeader = styled.button`
  display: flex; align-items: center; gap: 8px; padding: 12px 14px;
  border: 0; border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input};
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600;
  color: ${colors.text.primary};
  text-transform: uppercase; letter-spacing: 0.06em;
  text-align: left; cursor: pointer; width: 100%;
  & > .chev { color: ${colors.text.muted}; display: inline-flex; }
  & > .icon { color: ${colors.blue.main}; display: inline-flex; }
  & > .title { flex: 1; }
  & > .badge { font-weight: 500; font-size: ${fontSize.sm}; text-transform: none; letter-spacing: 0; color: ${colors.text.muted}; }
  &:hover { background: ${colors.blue.bg}; }
`
const PanelBody = styled.div`padding: 14px; display: flex; flex-direction: column; gap: 12px;`
const Select = styled.select`
  height: 32px; padding: 0 8px; border-radius: ${radius.md}; border: 1px solid ${colors.border};
  background: ${colors.bg.input}; color: ${colors.text.primary}; font-family: ${fonts.sans}; font-size: ${fontSize.base};
`
const Hint = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-family: ${fonts.sans};`
const Chips = styled.div`display: flex; flex-wrap: wrap; gap: 6px; align-items: center; min-height: 32px;`
const RowFlex = styled.div`display: flex; gap: 10px; align-items: center; flex-wrap: wrap;`
const ColorRow = styled.div`display: flex; align-items: center; gap: 8px;`
const ColorSwatch = styled.span<{ $color: string }>`
  width: 28px; height: 28px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border};
  background: ${({ $color }) => $color || 'transparent'};
  flex-shrink: 0;
`
const InlineActions = styled.div`display: flex; align-items: center; gap: 6px;`
const MaskedRow = styled.div`
  display: flex; align-items: center; gap: 8px;
  padding: 6px 10px; border-radius: ${radius.md}; border: 1px solid ${colors.border};
  background: ${colors.bg.input}; font-family: ${fonts.mono}; font-size: ${fontSize.base};
  color: ${colors.text.muted};
  & .dots { letter-spacing: 2px; }
  & .status { flex: 1; }
`

const DEFAULTS: {
  app: AppSection; ai: AiSection; license: LicenseSection; oidc: OidcSection; branding: BrandingFields;
} = {
  app: { name: 'Liberty Next', host: '0.0.0.0', port: 8000, log_level: 'info', hot_reload: false, default_language: 'en' },
  ai: {
    enabled: true, model: 'claude-opus-4-8', max_tokens: 8192, max_iterations: 8, system_prompt: '',
    thinking: false, effort: '', request_timeout: 120,
    connector_tools: true, api_tool: false, scaffold_tools: false, allowed_connectors: [],
    web_fetch_domains: [], web_fetch_max_uses: 5, api_key: '',
  },
  license: { key: '' },
  oidc: {
    enabled: false, discovery_url: '', client_id: '', client_secret: '',
    scopes: 'openid email profile', username_claim: 'preferred_username',
    email_claim: 'email', name_claim: 'name', redirect_url: '', frontend_redirect: '',
  },
  branding: {
    author: '', primary_color: '', primary_color_light: '',
    cover_eyebrow: '', cover_ref: '', footer_left: '',
  },
}

type SectionKey = 'app' | 'license' | 'ai' | 'oidc' | 'branding'
const OPEN_STORAGE_KEY = 'liberty:appbuilder:open'
const DEFAULT_OPEN: Record<SectionKey, boolean> = {
  app: true, license: false, ai: false, oidc: false, branding: false,
}

function loadOpenState(): Record<SectionKey, boolean> {
  try {
    const raw = localStorage.getItem(OPEN_STORAGE_KEY)
    if (!raw) return DEFAULT_OPEN
    const parsed = JSON.parse(raw) as Partial<Record<SectionKey, boolean>>
    return { ...DEFAULT_OPEN, ...parsed }
  } catch { return DEFAULT_OPEN }
}

export default function AppBuilder() {
  const { t } = useTranslation()
  const [app, setApp] = useState<AppSection>(DEFAULTS.app)
  const [ai, setAi] = useState<AiSection>(DEFAULTS.ai)
  const [license, setLicense] = useState<LicenseSection>(DEFAULTS.license)
  const [oidc, setOidc] = useState<OidcSection>(DEFAULTS.oidc)
  const [branding, setBranding] = useState<BrandingFields>(DEFAULTS.branding)
  // The framework's hard-coded defaults — used as input placeholders + by the
  // "Reset to defaults" button. Loaded alongside the current values so the UI
  // knows what "empty" actually means.
  const [brandingDefaults, setBrandingDefaults] = useState<BrandingFields>(DEFAULTS.branding)
  const [choices, setChoices] = useState<Choices>({ log_levels: [], effort_levels: [], connectors: [], models: [] })
  const [path, setPath] = useState('')
  const [original, setOriginal] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [restartHint, setRestartHint] = useState(false)
  const [open, setOpen] = useState<Record<SectionKey, boolean>>(loadOpenState)
  // Per-field "Edit" toggle for masked secrets. While false, the field shows dots + an Edit
  // button (and the wire payload sends "" — the backend treats empty as "leave unchanged"
  // because the GET also sent ""). Flipping to true reveals an input.
  const [editingSecret, setEditingSecret] = useState<Record<string, boolean>>({})

  const toggleSection = (k: SectionKey) => {
    setOpen((prev) => {
      const next = { ...prev, [k]: !prev[k] }
      try { localStorage.setItem(OPEN_STORAGE_KEY, JSON.stringify(next)) } catch { /* ignore */ }
      return next
    })
  }

  const load = useCallback(async () => {
    setLoading(true); setError(null); setStatus(null); setRestartHint(false)
    try {
      // app/parsed + branding live in the same file (app.toml) but are exposed
      // by separate endpoints — fetch them concurrently, populate state once
      // both resolve. A branding fetch failure isn't fatal: the rest of the
      // App tab still works; we just seed branding from defaults and surface
      // the error.
      const [r, b] = await Promise.all([
        api.get<AppParsed>('/admin/config/app/parsed'),
        api.get<BrandingResponse>('/admin/config/reports/branding').catch(() => null),
      ])
      setPath(r.path)
      setApp(r.app)
      setAi(r.ai)
      setLicense(r.license)
      setOidc(r.oidc)
      setChoices(r.choices)
      const loadedBranding = b?.branding ?? DEFAULTS.branding
      setBranding(loadedBranding)
      setBrandingDefaults(b?.defaults ?? DEFAULTS.branding)
      setOriginal(JSON.stringify({
        app: r.app, ai: r.ai, license: r.license, oidc: r.oidc, branding: loadedBranding,
      }))
      setEditingSecret({})
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const dirty = useMemo(
    () => JSON.stringify({ app, ai, license, oidc, branding }) !== original,
    [app, ai, license, oidc, branding, original],
  )

  const discard = () => {
    if (!original) return
    try {
      const seed = JSON.parse(original) as {
        app: AppSection; ai: AiSection; license: LicenseSection; oidc: OidcSection; branding: BrandingFields;
      }
      setApp(seed.app); setAi(seed.ai); setLicense(seed.license); setOidc(seed.oidc); setBranding(seed.branding)
      setStatus(null); setError(null); setRestartHint(false); setEditingSecret({})
    } catch { /* ignore */ }
  }

  const resetBrandingToDefaults = () => {
    setBranding({ ...brandingDefaults })
  }

  const save = async () => {
    setBusy(true); setError(null); setStatus(null)
    try {
      // For each masked field: if the user didn't enable Edit, send "" so the backend's
      // _encrypt_sensitives() skips it (empty → passthrough, on-disk value preserved).
      // When Edit was enabled, send whatever's in the field — including "" to explicitly
      // clear (the on-disk value becomes "" and the connector reports unconfigured).
      const aiPayload = { ...ai, api_key: editingSecret['ai.api_key'] ? ai.api_key : '' }
      const licensePayload = { ...license, key: editingSecret['license.key'] ? license.key : '' }
      const oidcPayload = { ...oidc, client_secret: editingSecret['oidc.client_secret'] ? oidc.client_secret : '' }
      const r = await api.put<{ saved: boolean; requires_restart?: boolean }>(
        '/admin/config/app/parsed',
        { app, ai: aiPayload, license: licensePayload, oidc: oidcPayload },
      )
      // Branding lives in the same file but has its own endpoint — second PUT,
      // so a typo here doesn't poison the app/ai/license/oidc save above.
      // Live-applies: next /api/reports/.../run picks the new values up.
      await api.put<{ saved: boolean; path: string }>(
        '/admin/config/reports/branding',
        { branding },
      )
      // Reload — refreshes the _set flags from disk so the masked rows update from "not
      // configured" to dots immediately after a first-time Set.
      await load()
      setStatus(t('settings.app.saved', 'Saved to {{path}}.', { path }))
      if (r?.requires_restart) setRestartHint(true)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (loading) return <SpinnerRing size={16} thickness={2} />

  const renderSection = (
    key: SectionKey, title: string, icon: ReactNode, badge: string, body: ReactNode,
  ) => (
    <Panel>
      <PanelHeader type="button" onClick={() => toggleSection(key)} aria-expanded={open[key]}>
        <span className="chev">{open[key] ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
        <span className="icon">{icon}</span>
        <span className="title">{title}</span>
        <span className="badge">{badge}</span>
      </PanelHeader>
      {open[key] && <PanelBody>{body}</PanelBody>}
    </Panel>
  )

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
          'Master settings (app.toml). Secrets stored here are encrypted at rest with the install master key. License key, AI api_key and OIDC client_secret take effect immediately on save — only host / port / log_level changes still need a server restart.',
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

      <Stack>
        {renderSection(
          'app',
          t('settings.app.appSection', 'App'),
          <SettingsIcon size={14} />,
          `${app.name} · :${app.port} · ${app.log_level}`,
          <>
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
          </>,
        )}

        {renderSection(
          'license',
          t('settings.app.licenseSection', 'License'),
          <ShieldCheck size={14} />,
          license.key_set
            ? t('settings.app.licenseConfigured', 'configured')
            : t('settings.app.licenseNotSet', 'not set — restricted mode'),
          <>
            <Hint>
              {t(
                'settings.app.licenseHint',
                'Vendor-signed JWT that unlocks licensed connectors (nomasx1, nomajde, …). Empty → "restricted" mode: licensed connectors aren\'t loaded. Encrypted at rest with the install master key.',
              )}
            </Hint>
            <Field label={t('settings.app.licenseKey', 'License key')}>
              <MaskedSecret
                fieldId="license.key"
                value={license.key}
                isSet={!!license.key_set}
                editing={!!editingSecret['license.key']}
                onEdit={() => setEditingSecret({ ...editingSecret, 'license.key': true })}
                onChange={(v) => setLicense({ ...license, key: v })}
                placeholder="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9…"
                inputComponent="textarea"
              />
            </Field>
          </>,
        )}

        {renderSection(
          'ai',
          t('settings.app.aiSection', 'AI Assistant'),
          <Sparkles size={14} />,
          ai.enabled
            ? `${ai.model}${ai.api_key_set ? '' : ' · ' + t('settings.app.aiNoKey', 'no API key')}`
            : t('settings.app.aiDisabled', 'disabled'),
          <>
            <Checkbox label={t('settings.app.aiEnabled', 'Enabled')}
              checked={ai.enabled} onChange={(c) => setAi({ ...ai, enabled: c })} />

            <Field label={t('settings.app.aiApiKey', 'Anthropic API key')}>
              <MaskedSecret
                fieldId="ai.api_key"
                value={ai.api_key}
                isSet={!!ai.api_key_set}
                editing={!!editingSecret['ai.api_key']}
                onEdit={() => setEditingSecret({ ...editingSecret, 'ai.api_key': true })}
                onChange={(v) => setAi({ ...ai, api_key: v })}
                placeholder="sk-ant-…"
              />
            </Field>

            <RowFlex>
              <Field label={t('settings.app.aiModel', 'Model')}>
                {/* The dropdown always includes the saved value, even when it isn't in the
                    canonical list — useful for experimental / legacy ids the operator may
                    have pinned in app.toml. The label tells them when they're off-list. */}
                {(() => {
                  const known = choices.models.some((m) => m.id === ai.model)
                  const options: ModelChoice[] = known
                    ? choices.models
                    : [{ id: ai.model, label: `${ai.model} (custom)` }, ...choices.models]
                  return (
                    <Select value={ai.model} onChange={(e) => setAi({ ...ai, model: e.target.value })} style={{ minWidth: 260 }}>
                      {options.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                    </Select>
                  )
                })()}
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
            <Checkbox label={t('settings.app.aiScaffoldTools', 'Scaffold tools (propose new queries / dictionary / screens / menu items — propose-only, never writes directly)')}
              checked={ai.scaffold_tools} onChange={(c) => setAi({ ...ai, scaffold_tools: c })} />

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
          </>,
        )}

        {renderSection(
          'oidc',
          t('settings.app.oidcSection', 'OpenID Connect (SSO)'),
          <KeyRound size={14} />,
          oidc.enabled
            ? (oidc.discovery_url || t('settings.app.oidcNoDiscovery', 'no discovery URL'))
            : t('settings.app.oidcDisabled', 'disabled'),
          <>
            <Hint>
              {t(
                'settings.app.oidcHint',
                'Single sign-on via any OIDC-compliant provider (Keycloak, Okta, Auth0, Azure AD, …). Register https://<your-host>/auth/oidc/callback as the redirect URI. The client_secret is encrypted at rest with the install master key.',
              )}
            </Hint>
            <Checkbox label={t('settings.app.oidcEnabled', 'Enabled')}
              checked={oidc.enabled} onChange={(c) => setOidc({ ...oidc, enabled: c })} />
            <Field label={t('settings.app.oidcDiscoveryUrl', 'Discovery URL (.well-known/openid-configuration)')}>
              <Input value={oidc.discovery_url}
                onChange={(e) => setOidc({ ...oidc, discovery_url: e.target.value })}
                placeholder="https://keycloak.example/realms/liberty/.well-known/openid-configuration" />
            </Field>
            <RowFlex>
              <Field label={t('settings.app.oidcClientId', 'Client ID')}>
                <Input value={oidc.client_id}
                  onChange={(e) => setOidc({ ...oidc, client_id: e.target.value })} style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.app.oidcScopes', 'Scopes')}>
                <Input value={oidc.scopes}
                  onChange={(e) => setOidc({ ...oidc, scopes: e.target.value })} style={{ width: 240 }} />
              </Field>
            </RowFlex>
            <Field label={t('settings.app.oidcClientSecret', 'Client secret')}>
              <MaskedSecret
                fieldId="oidc.client_secret"
                value={oidc.client_secret}
                isSet={!!oidc.client_secret_set}
                editing={!!editingSecret['oidc.client_secret']}
                onEdit={() => setEditingSecret({ ...editingSecret, 'oidc.client_secret': true })}
                onChange={(v) => setOidc({ ...oidc, client_secret: v })}
                placeholder="…"
              />
            </Field>

            <Hint>{t('settings.app.oidcClaims', 'Which ID-token claims to read.')}</Hint>
            <RowFlex>
              <Field label={t('settings.app.oidcUsernameClaim', 'Username claim')}>
                <Input value={oidc.username_claim}
                  onChange={(e) => setOidc({ ...oidc, username_claim: e.target.value })} style={{ width: 200 }} />
              </Field>
              <Field label={t('settings.app.oidcEmailClaim', 'Email claim')}>
                <Input value={oidc.email_claim}
                  onChange={(e) => setOidc({ ...oidc, email_claim: e.target.value })} style={{ width: 160 }} />
              </Field>
              <Field label={t('settings.app.oidcNameClaim', 'Name claim')}>
                <Input value={oidc.name_claim}
                  onChange={(e) => setOidc({ ...oidc, name_claim: e.target.value })} style={{ width: 160 }} />
              </Field>
            </RowFlex>

            <Hint>{t('settings.app.oidcRedirects', 'Optional — override the redirect targets when running behind a proxy or for SPA flows.')}</Hint>
            <Field label={t('settings.app.oidcRedirectUrl', 'Redirect URL override (proxy)')}>
              <Input value={oidc.redirect_url}
                onChange={(e) => setOidc({ ...oidc, redirect_url: e.target.value })}
                placeholder="https://liberty.example.com/auth/oidc/callback" />
            </Field>
            <Field label={t('settings.app.oidcFrontendRedirect', 'Frontend redirect (SPA — JWTs in URL fragment)')}>
              <Input value={oidc.frontend_redirect}
                onChange={(e) => setOidc({ ...oidc, frontend_redirect: e.target.value })}
                placeholder="https://liberty.example.com/" />
            </Field>
          </>,
        )}

        {renderSection(
          'branding',
          t('settings.app.brandingSection', 'PDF branding'),
          <FileText size={14} />,
          branding.author || t('settings.app.brandingDefaults', 'framework defaults'),
          <>
            <Hint>
              {t(
                'settings.app.brandingHint',
                'Applied to every report generated through the framework. Per-report pdf_options still wins; anything a report doesn\'t set inherits from here.',
              )}
            </Hint>
            <InlineActions>
              <Button $size="sm" $variant="ghost" onClick={resetBrandingToDefaults} disabled={busy} type="button">
                <RefreshCw size={13} /> {t('settings.reports.resetDefaults', 'Reset to defaults')}
              </Button>
            </InlineActions>
            <RowFlex>
              <Field label={t('settings.reports.fields.author', 'Author / company name')}>
                <Input value={branding.author}
                  onChange={(e) => setBranding({ ...branding, author: e.target.value })}
                  placeholder={brandingDefaults.author || 'NOMANA-IT'} style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.reports.fields.coverEyebrow', 'Cover eyebrow text')}>
                <Input value={branding.cover_eyebrow}
                  onChange={(e) => setBranding({ ...branding, cover_eyebrow: e.target.value })}
                  placeholder={brandingDefaults.cover_eyebrow} style={{ width: 280 }} />
              </Field>
            </RowFlex>
            <RowFlex>
              <Field label={t('settings.reports.fields.primaryColor', 'Primary colour')}>
                <ColorRow>
                  <Input value={branding.primary_color}
                    onChange={(e) => setBranding({ ...branding, primary_color: e.target.value })}
                    placeholder={brandingDefaults.primary_color}
                    style={{ width: 130, fontFamily: fonts.mono }} />
                  <ColorSwatch $color={branding.primary_color} />
                </ColorRow>
              </Field>
              <Field label={t('settings.reports.fields.primaryColorLight', 'Primary colour (lighter)')}>
                <ColorRow>
                  <Input value={branding.primary_color_light}
                    onChange={(e) => setBranding({ ...branding, primary_color_light: e.target.value })}
                    placeholder={brandingDefaults.primary_color_light}
                    style={{ width: 130, fontFamily: fonts.mono }} />
                  <ColorSwatch $color={branding.primary_color_light} />
                </ColorRow>
              </Field>
            </RowFlex>
            <RowFlex>
              <Field label={t('settings.reports.fields.coverRef', 'Cover footer reference')}>
                <Input value={branding.cover_ref}
                  onChange={(e) => setBranding({ ...branding, cover_ref: e.target.value })}
                  placeholder={brandingDefaults.cover_ref} style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.reports.fields.footerLeft', 'Page footer (left)')}>
                <Input value={branding.footer_left}
                  onChange={(e) => setBranding({ ...branding, footer_left: e.target.value })}
                  placeholder={brandingDefaults.footer_left} style={{ width: 280 }} />
              </Field>
            </RowFlex>
          </>,
        )}
      </Stack>
    </Shell>
  )
}

// ── masked secret with reveal-to-edit ─────────────────────────────────────────────────────

function MaskedSecret({
  fieldId, value, isSet, editing, onEdit, onChange, placeholder, inputComponent = 'input',
}: {
  fieldId: string
  value: string
  isSet: boolean
  editing: boolean
  onEdit: () => void
  onChange: (v: string) => void
  placeholder?: string
  inputComponent?: 'input' | 'textarea'
}) {
  const { t } = useTranslation()
  if (!editing) {
    return (
      <MaskedRow>
        <span className="status">
          {isSet ? <span className="dots">••••••••••••</span> : <em>{t('settings.app.notConfigured', 'not configured')}</em>}
        </span>
        <Button $variant="ghost" $size="sm" onClick={onEdit} type="button">
          <Pencil size={12} /> {isSet ? t('common.replace', 'Replace') : t('common.set', 'Set')}
        </Button>
      </MaskedRow>
    )
  }
  if (inputComponent === 'textarea') {
    return (
      <Textarea
        autoFocus
        rows={3}
        id={fieldId}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        style={{ fontFamily: fonts.mono }}
      />
    )
  }
  return (
    <Input
      autoFocus
      id={fieldId}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      style={{ fontFamily: fonts.mono }}
    />
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
