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
  ChevronDown, ChevronRight, KeyRound, ShieldCheck, FileText, RefreshCw, Bell,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Card, Checkbox, Field, Input, PasswordInput, SearchSelect, SpinnerRing, Tag, Textarea,
} from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { AiStartersEditor, ModelsPicker } from './AiStartersEditor'

interface AppSection {
  name: string
  host: string
  port: number
  log_level: string
  hot_reload: boolean
  default_language: string
  oracle_thick: boolean
  default_period: string
  default_page_size: number
  dashboard_cache_seconds: number
  tabs_multi: boolean
  tabs_close_on_app_switch: boolean
  tabs_max: number
  drill_inline: boolean
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
  ops_tools: boolean
  allowed_connectors: string[]
  web_fetch_domains: string[]
  web_fetch_max_uses: number
  allowed_models: string[]
  starters: Record<string, string[]>
  /** Plaintext or ``ENC:…`` ciphertext. GET returns the on-disk value verbatim;
   *  PUT round-trips ``ENC:`` unchanged (encrypt() is idempotent), encrypts a
   *  plaintext edit, and writes empty as empty (operator clear). Same pattern
   *  pools / API connectors use for their password / auth_token fields. */
  api_key: string
}
interface LicenseSection {
  key: string
}
interface OidcSection {
  enabled: boolean
  discovery_url: string
  client_id: string
  client_secret: string
  scopes: string
  username_claim: string
  email_claim: string
  name_claim: string
  redirect_url: string
  frontend_redirect: string
}
interface NotifySection {
  enabled: boolean
  smtp_host: string
  smtp_port: number
  smtp_user: string
  smtp_password: string
  smtp_from: string
  smtp_tls: boolean
  smtp_ssl: boolean
  smtp_timeout: number
  retention_days: number
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
  notify?: NotifySection
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

const PERIOD_PRESETS = ['thisMonth', 'lastMonth', 'thisQuarter', 'thisYear', 'lastYear', 'last12', 'last30', 'last7', 'today', 'yesterday', 'all']

const DEFAULTS: {
  app: AppSection; ai: AiSection; license: LicenseSection; oidc: OidcSection; notify: NotifySection; branding: BrandingFields;
} = {
  app: { name: 'Liberty Next', host: '0.0.0.0', port: 8000, log_level: 'info', hot_reload: false, default_language: 'en', oracle_thick: false, default_period: 'thisMonth', default_page_size: 50, dashboard_cache_seconds: 60, tabs_multi: true, tabs_close_on_app_switch: false, tabs_max: 5, drill_inline: false },
  ai: {
    enabled: true, model: 'claude-opus-4-8', max_tokens: 8192, max_iterations: 8, system_prompt: '',
    thinking: false, effort: '', request_timeout: 120,
    connector_tools: true, api_tool: false, scaffold_tools: false, ops_tools: false, allowed_connectors: [],
    web_fetch_domains: [], web_fetch_max_uses: 5, allowed_models: [], starters: {}, api_key: '',
  },
  license: { key: '' },
  oidc: {
    enabled: false, discovery_url: '', client_id: '', client_secret: '',
    scopes: 'openid email profile', username_claim: 'preferred_username',
    email_claim: 'email', name_claim: 'name', redirect_url: '', frontend_redirect: '',
  },
  notify: {
    enabled: true, smtp_host: '', smtp_port: 587, smtp_user: '', smtp_password: '', smtp_from: '',
    smtp_tls: true, smtp_ssl: false, smtp_timeout: 20, retention_days: 90,
  },
  branding: {
    author: '', primary_color: '', primary_color_light: '',
    cover_eyebrow: '', cover_ref: '', footer_left: '',
  },
}

type SectionKey = 'app' | 'license' | 'ai' | 'oidc' | 'notify' | 'branding'
const OPEN_STORAGE_KEY = 'liberty:appbuilder:open'
const DEFAULT_OPEN: Record<SectionKey, boolean> = {
  app: true, license: false, ai: false, oidc: false, notify: false, branding: false,
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
  const [notify, setNotify] = useState<NotifySection>(DEFAULTS.notify)
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
      setNotify({ ...DEFAULTS.notify, ...(r.notify ?? {}) })
      setChoices(r.choices)
      const loadedBranding = b?.branding ?? DEFAULTS.branding
      setBranding(loadedBranding)
      setBrandingDefaults(b?.defaults ?? DEFAULTS.branding)
      setOriginal(JSON.stringify({
        app: r.app, ai: r.ai, license: r.license, oidc: r.oidc, notify: { ...DEFAULTS.notify, ...(r.notify ?? {}) }, branding: loadedBranding,
      }))
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
      setStatus(null); setError(null); setRestartHint(false)
    } catch { /* ignore */ }
  }

  const resetBrandingToDefaults = () => {
    setBranding({ ...brandingDefaults })
  }

  const save = async () => {
    setBusy(true); setError(null); setStatus(null)
    try {
      // Sensitive fields (ai.api_key, license.key, oidc.client_secret) round-trip
      // their on-disk value naturally: when the operator hasn't touched the field,
      // the ENC: ciphertext from GET sits in state and gets sent back verbatim;
      // backend's encrypt() is idempotent on ENC: values so it stays as-is. A new
      // plaintext gets encrypted on write. An empty input clears the on-disk value.
      // Same pattern as PoolsBuilder / ConnectorsBuilder.
      const r = await api.put<{ saved: boolean; requires_restart?: boolean }>(
        '/admin/config/app/parsed',
        { app, ai, license, oidc, notify },
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
      setStatus(t('settings.app.saved', { path }))
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
            <Undo2 size={13} /> {t('common.discard')}
          </Button>
          <Button $variant="primary" $size="sm" onClick={() => void save()} disabled={busy || !dirty}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
          </Button>
        </ToolbarRight>
      </Toolbar>

      <Hint>
        {t('settings.app.intro',
        )}
        {path && <> · <span style={{ fontFamily: fonts.mono }}>{path}</span></>}
      </Hint>
      {error && <Banner $tone="error">{error}</Banner>}
      {restartHint && (
        <Banner $tone="info">
          <AlertTriangle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
          {t('settings.app.restart')}
        </Banner>
      )}

      <Stack>
        {renderSection(
          'app',
          t('settings.app.appSection'),
          <SettingsIcon size={14} />,
          `${app.name} · :${app.port} · ${app.log_level}`,
          <>
            <Field label={t('settings.app.name')}>
              <Input value={app.name} onChange={(e) => setApp({ ...app, name: e.target.value })} />
            </Field>
            <RowFlex>
              <Field label={t('settings.app.host')}>
                <Input value={app.host} onChange={(e) => setApp({ ...app, host: e.target.value })} style={{ width: 200 }} />
              </Field>
              <Field label={t('settings.app.port')}>
                <Input type="number" min={1} max={65535} value={app.port}
                  onChange={(e) => setApp({ ...app, port: Number(e.target.value) || 0 })} style={{ width: 110 }} />
              </Field>
            </RowFlex>
            <RowFlex>
              <Field label={t('settings.app.logLevel')}>
                <div style={{ minWidth: 160 }}>
                  <SearchSelect value={app.log_level} options={choices.log_levels.map((l) => ({ value: l, label: l }))} onChange={(v) => setApp({ ...app, log_level: v })} />
                </div>
              </Field>
              <Field label={t('settings.app.defaultLanguage')}>
                <Input value={app.default_language}
                  onChange={(e) => setApp({ ...app, default_language: e.target.value })} style={{ width: 110 }} />
              </Field>
            </RowFlex>
            <RowFlex>
              <Field label={t('settings.app.defaultPeriod')}>
                <div style={{ width: 200 }}>
                  <SearchSelect value={app.default_period} onChange={(v) => setApp({ ...app, default_period: v })}
                    options={PERIOD_PRESETS.map((k) => ({ value: k, label: t(`period.${k}`) }))} />
                </div>
              </Field>
              <Field label={t('settings.app.defaultPageSize')}>
                <Input type="number" min={10} max={10000} value={app.default_page_size}
                  onChange={(e) => setApp({ ...app, default_page_size: Number(e.target.value) || 50 })} style={{ width: 110 }} />
              </Field>
              <Field label={t('settings.app.dashboardCache')}>
                <Input type="number" min={0} max={3600} value={app.dashboard_cache_seconds}
                  onChange={(e) => setApp({ ...app, dashboard_cache_seconds: Math.max(0, Number(e.target.value) || 0) })} style={{ width: 110 }} />
              </Field>
            </RowFlex>
            <RowFlex>
              <Checkbox label={t('settings.app.tabsMulti')}
                checked={app.tabs_multi} onChange={(c) => setApp({ ...app, tabs_multi: c })} />
              <Checkbox label={t('settings.app.tabsCloseOnSwitch')}
                checked={app.tabs_close_on_app_switch} onChange={(c) => setApp({ ...app, tabs_close_on_app_switch: c })} />
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                <span>{t('settings.app.tabsMax')}</span>
                <Input type="number" min={1} max={50} value={app.tabs_max} disabled={!app.tabs_multi}
                  onChange={(e) => setApp({ ...app, tabs_max: Math.min(50, Math.max(1, Number(e.target.value) || 5)) })} style={{ width: 72 }} />
              </label>
            </RowFlex>
            <Checkbox label={t('settings.app.drillInline')}
              checked={app.drill_inline} onChange={(c) => setApp({ ...app, drill_inline: c })} />
            <Checkbox label={t('settings.app.hotReload')}
              checked={app.hot_reload} onChange={(c) => setApp({ ...app, hot_reload: c })} />
            <Checkbox label={t('settings.app.oracleThick')}
              checked={app.oracle_thick} onChange={(c) => setApp({ ...app, oracle_thick: c })} />
          </>,
        )}

        {renderSection(
          'license',
          t('settings.app.licenseSection'),
          <ShieldCheck size={14} />,
          license.key
            ? t('settings.app.licenseConfigured')
            : t('settings.app.licenseNotSet'),
          <>
            <Hint>
              {t('settings.app.licenseHint',
              )}
            </Hint>
            <Field label={t('settings.app.licenseKey')}>
              <PasswordInput value={license.key}
                onChange={(e) => setLicense({ ...license, key: e.target.value })}
                placeholder="eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9…"
                style={{ fontFamily: fonts.mono }} />
            </Field>
          </>,
        )}

        {renderSection(
          'ai',
          t('settings.app.aiSection'),
          <Sparkles size={14} />,
          ai.enabled
            ? `${ai.model}${ai.api_key ? '' : ' · ' + t('settings.app.aiNoKey')}`
            : t('settings.app.aiDisabled'),
          <>
            <Checkbox label={t('settings.app.aiEnabled')}
              checked={ai.enabled} onChange={(c) => setAi({ ...ai, enabled: c })} />

            <Field label={t('settings.app.aiApiKey')}>
              <PasswordInput value={ai.api_key}
                onChange={(e) => setAi({ ...ai, api_key: e.target.value })}
                placeholder="sk-ant-…"
                style={{ fontFamily: fonts.mono }} />
            </Field>

            <RowFlex>
              <Field label={t('settings.app.aiModel')}>
                {/* The dropdown always includes the saved value, even when it isn't in the
                    canonical list — useful for experimental / legacy ids the operator may
                    have pinned in app.toml. The label tells them when they're off-list. */}
                {(() => {
                  const known = choices.models.some((m) => m.id === ai.model)
                  const options: ModelChoice[] = known
                    ? choices.models
                    : [{ id: ai.model, label: `${ai.model} (custom)` }, ...choices.models]
                  return (
                    <div style={{ minWidth: 260 }}>
                      <SearchSelect value={ai.model} options={options.map((m) => ({ value: m.id, label: m.label, mono: m.id }))} allowCustom
                        onChange={(v) => { if (v) setAi({ ...ai, model: v }) }} />
                    </div>
                  )
                })()}
              </Field>
              <Field label={t('settings.app.aiEffort')}>
                <div style={{ minWidth: 140 }}>
                  <SearchSelect value={ai.effort} options={choices.effort_levels.filter(Boolean).map((l) => ({ value: l, label: l }))}
                    anyLabel={t('settings.app.aiEffortDefault')} onChange={(v) => setAi({ ...ai, effort: v })} />
                </div>
              </Field>
            </RowFlex>
            <RowFlex>
              <Field label={t('settings.app.aiMaxTokens')}>
                <Input type="number" min={1} value={ai.max_tokens}
                  onChange={(e) => setAi({ ...ai, max_tokens: Number(e.target.value) || 0 })} style={{ width: 130 }} />
              </Field>
              <Field label={t('settings.app.aiMaxIter')}>
                <Input type="number" min={1} value={ai.max_iterations}
                  onChange={(e) => setAi({ ...ai, max_iterations: Number(e.target.value) || 0 })} style={{ width: 130 }} />
              </Field>
              <Field label={t('settings.app.aiTimeout')}>
                <Input type="number" min={1} value={ai.request_timeout}
                  onChange={(e) => setAi({ ...ai, request_timeout: Number(e.target.value) || 0 })} style={{ width: 130 }} />
              </Field>
            </RowFlex>
            <Checkbox label={t('settings.app.aiThinking')}
              checked={ai.thinking} onChange={(c) => setAi({ ...ai, thinking: c })} />
            <Field label={t('settings.app.aiSystemPrompt')}>
              <Textarea rows={5} value={ai.system_prompt}
                onChange={(e) => setAi({ ...ai, system_prompt: e.target.value })} />
            </Field>

            <Hint>{t('settings.app.aiTools')}</Hint>
            <Checkbox label={t('settings.app.aiConnectorTools')}
              checked={ai.connector_tools} onChange={(c) => setAi({ ...ai, connector_tools: c })} />
            <Checkbox label={t('settings.app.aiApiTool')}
              checked={ai.api_tool} onChange={(c) => setAi({ ...ai, api_tool: c })} />
            <Checkbox label={t('settings.app.aiScaffoldTools')}
              checked={ai.scaffold_tools} onChange={(c) => setAi({ ...ai, scaffold_tools: c })} />
            <Checkbox label={t('settings.app.aiOpsTools')}
              checked={ai.ops_tools} onChange={(c) => setAi({ ...ai, ops_tools: c })} />

            <Field label={t('settings.app.aiAllowedConnectors')}>
              <ConnectorPicker
                value={ai.allowed_connectors}
                options={choices.connectors}
                onChange={(next) => setAi({ ...ai, allowed_connectors: next })}
              />
            </Field>

            <Hint>{t('settings.app.aiChatHint')}</Hint>
            <Field label={t('settings.app.aiAllowedModels')}>
              <ModelsPicker value={ai.allowed_models} models={choices.models} onChange={(next) => setAi({ ...ai, allowed_models: next })} />
            </Field>
            <Field label={t('settings.app.aiStarters')}>
              <AiStartersEditor value={ai.starters} apps={choices.connectors} onChange={(next) => setAi({ ...ai, starters: next })} />
            </Field>

            <Hint>{t('settings.app.aiWebFetch')}</Hint>
            <Field label={t('settings.app.aiWebDomains')}>
              <ChipsInput
                value={ai.web_fetch_domains}
                onChange={(next) => setAi({ ...ai, web_fetch_domains: next })}
                placeholder="example.com"
              />
            </Field>
            <Field label={t('settings.app.aiWebMaxUses')}>
              <Input type="number" min={1} value={ai.web_fetch_max_uses}
                onChange={(e) => setAi({ ...ai, web_fetch_max_uses: Number(e.target.value) || 0 })} style={{ width: 130 }} />
            </Field>
          </>,
        )}

        {renderSection(
          'notify',
          t('settings.app.notifySection'),
          <Bell size={14} />,
          !notify.enabled ? t('settings.app.oidcDisabled') : notify.smtp_host ? `${notify.smtp_host}:${notify.smtp_port}` : t('settings.app.notifyNoSmtp'),
          <>
            <Checkbox label={t('settings.app.notifyEnabled')} checked={notify.enabled} onChange={(c) => setNotify({ ...notify, enabled: c })} />
            <Hint>{t('settings.app.smtpServer')}</Hint>
            <RowFlex>
              <Field label={t('settings.app.smtpHost')}>
                <Input value={notify.smtp_host} onChange={(e) => setNotify({ ...notify, smtp_host: e.target.value })} placeholder="smtp.example.com" style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.app.smtpPort')}>
                <Input type="number" min={1} max={65535} value={notify.smtp_port} onChange={(e) => setNotify({ ...notify, smtp_port: Number(e.target.value) || 587 })} style={{ width: 110 }} />
              </Field>
              <Field label={t('settings.app.smtpEncryption')}>
                <div style={{ width: 240 }}>
                  <SearchSelect
                    value={notify.smtp_ssl ? 'ssl' : notify.smtp_tls ? 'starttls' : 'none'}
                    onChange={(v) => setNotify({ ...notify, smtp_tls: v === 'starttls', smtp_ssl: v === 'ssl', smtp_port: v === 'ssl' && notify.smtp_port === 587 ? 465 : v === 'starttls' && notify.smtp_port === 465 ? 587 : notify.smtp_port })}
                    options={[
                      { value: 'starttls', label: t('settings.app.smtpStartTls') },
                      { value: 'ssl', label: t('settings.app.smtpImplicitTls') },
                      { value: 'none', label: t('settings.app.smtpNoTls') },
                    ]} />
                </div>
              </Field>
              <Field label={t('settings.app.smtpTimeout')}>
                <Input type="number" min={1} value={notify.smtp_timeout} onChange={(e) => setNotify({ ...notify, smtp_timeout: Number(e.target.value) || 20 })} style={{ width: 90 }} />
              </Field>
            </RowFlex>
            <Hint>{t('settings.app.smtpAccount')}</Hint>
            <RowFlex>
              <Field label={t('settings.app.smtpUser')}>
                <Input value={notify.smtp_user} onChange={(e) => setNotify({ ...notify, smtp_user: e.target.value })} style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.app.smtpPassword')}>
                <PasswordInput value={notify.smtp_password} onChange={(e) => setNotify({ ...notify, smtp_password: e.target.value })} placeholder="…" style={{ fontFamily: fonts.mono, width: 280 }} />
              </Field>
            </RowFlex>
            <Hint>{t('settings.app.smtpSending')}</Hint>
            <RowFlex>
              <Field label={t('settings.app.smtpFrom')}>
                <Input value={notify.smtp_from} onChange={(e) => setNotify({ ...notify, smtp_from: e.target.value })} placeholder="liberty@example.com" style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.app.notifyRetention')}>
                <Input type="number" min={0} value={notify.retention_days} onChange={(e) => setNotify({ ...notify, retention_days: Math.max(0, Number(e.target.value) || 0) })} style={{ width: 110 }} />
              </Field>
            </RowFlex>
          </>,
        )}

        {renderSection(
          'oidc',
          t('settings.app.oidcSection'),
          <KeyRound size={14} />,
          oidc.enabled
            ? (oidc.discovery_url || t('settings.app.oidcNoDiscovery'))
            : t('settings.app.oidcDisabled'),
          <>
            <Hint>
              {t('settings.app.oidcHint',
              )}
            </Hint>
            <Checkbox label={t('settings.app.oidcEnabled')}
              checked={oidc.enabled} onChange={(c) => setOidc({ ...oidc, enabled: c })} />
            <Field label={t('settings.app.oidcDiscoveryUrl')}>
              <Input value={oidc.discovery_url}
                onChange={(e) => setOidc({ ...oidc, discovery_url: e.target.value })}
                placeholder="https://keycloak.example/realms/liberty/.well-known/openid-configuration" />
            </Field>
            <RowFlex>
              <Field label={t('settings.app.oidcClientId')}>
                <Input value={oidc.client_id}
                  onChange={(e) => setOidc({ ...oidc, client_id: e.target.value })} style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.app.oidcScopes')}>
                <Input value={oidc.scopes}
                  onChange={(e) => setOidc({ ...oidc, scopes: e.target.value })} style={{ width: 240 }} />
              </Field>
            </RowFlex>
            <Field label={t('settings.app.oidcClientSecret')}>
              <PasswordInput value={oidc.client_secret}
                onChange={(e) => setOidc({ ...oidc, client_secret: e.target.value })}
                placeholder="…"
                style={{ fontFamily: fonts.mono }} />
            </Field>

            <Hint>{t('settings.app.oidcClaims')}</Hint>
            <RowFlex>
              <Field label={t('settings.app.oidcUsernameClaim')}>
                <Input value={oidc.username_claim}
                  onChange={(e) => setOidc({ ...oidc, username_claim: e.target.value })} style={{ width: 200 }} />
              </Field>
              <Field label={t('settings.app.oidcEmailClaim')}>
                <Input value={oidc.email_claim}
                  onChange={(e) => setOidc({ ...oidc, email_claim: e.target.value })} style={{ width: 160 }} />
              </Field>
              <Field label={t('settings.app.oidcNameClaim')}>
                <Input value={oidc.name_claim}
                  onChange={(e) => setOidc({ ...oidc, name_claim: e.target.value })} style={{ width: 160 }} />
              </Field>
            </RowFlex>

            <Hint>{t('settings.app.oidcRedirects')}</Hint>
            <Field label={t('settings.app.oidcRedirectUrl')}>
              <Input value={oidc.redirect_url}
                onChange={(e) => setOidc({ ...oidc, redirect_url: e.target.value })}
                placeholder="https://liberty.example.com/auth/oidc/callback" />
            </Field>
            <Field label={t('settings.app.oidcFrontendRedirect')}>
              <Input value={oidc.frontend_redirect}
                onChange={(e) => setOidc({ ...oidc, frontend_redirect: e.target.value })}
                placeholder="https://liberty.example.com/" />
            </Field>
          </>,
        )}

        {renderSection(
          'branding',
          t('settings.app.brandingSection'),
          <FileText size={14} />,
          branding.author || t('settings.app.brandingDefaults'),
          <>
            <Hint>
              {t('settings.app.brandingHint',
              )}
            </Hint>
            <InlineActions>
              <Button $size="sm" $variant="ghost" onClick={resetBrandingToDefaults} disabled={busy} type="button">
                <RefreshCw size={13} /> {t('settings.reports.resetDefaults')}
              </Button>
            </InlineActions>
            <RowFlex>
              <Field label={t('settings.reports.fields.author')}>
                <Input value={branding.author}
                  onChange={(e) => setBranding({ ...branding, author: e.target.value })}
                  placeholder={brandingDefaults.author || 'NOMANA-IT'} style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.reports.fields.coverEyebrow')}>
                <Input value={branding.cover_eyebrow}
                  onChange={(e) => setBranding({ ...branding, cover_eyebrow: e.target.value })}
                  placeholder={brandingDefaults.cover_eyebrow} style={{ width: 280 }} />
              </Field>
            </RowFlex>
            <RowFlex>
              <Field label={t('settings.reports.fields.primaryColor')}>
                <ColorRow>
                  <Input value={branding.primary_color}
                    onChange={(e) => setBranding({ ...branding, primary_color: e.target.value })}
                    placeholder={brandingDefaults.primary_color}
                    style={{ width: 130, fontFamily: fonts.mono }} />
                  <ColorSwatch $color={branding.primary_color} />
                </ColorRow>
              </Field>
              <Field label={t('settings.reports.fields.primaryColorLight')}>
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
              <Field label={t('settings.reports.fields.coverRef')}>
                <Input value={branding.cover_ref}
                  onChange={(e) => setBranding({ ...branding, cover_ref: e.target.value })}
                  placeholder={brandingDefaults.cover_ref} style={{ width: 280 }} />
              </Field>
              <Field label={t('settings.reports.fields.footerLeft')}>
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
        <div style={{ minWidth: 180 }}>
          <SearchSelect value={pick} options={remaining.map((o) => ({ value: o, label: o }))} placeholder="+ add…" onChange={(v) => add(v)} />
        </div>
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
