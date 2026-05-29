// Dedicated editor for a single API connector — replaces the SchemaNavigator path for
// ``[connectors.<name>]`` where ``type = "api"``. Five top-level tabs:
//
//   * **Connection** — base URL, timeout, SSL verify, default headers.
//   * **Authentication** — method picker + only the fields that method actually uses (OAuth2's
//     token endpoint section appears only when method is OAuth2 / JWT, etc.).
//   * **Endpoints** — collapsible list, one editor per endpoint (method / path / headers /
//     body / query params / response field / response mappings / parameters table).
//   * **Webhooks** — placeholder for the inbound-webhook slice (Phase 9 follow-up).
//   * **Test** — pick an endpoint, fill the {{placeholder}} values, hit Run, see the result
//     (status / URL / body / extracted / mapped). Runs the *in-progress* config — does not
//     save first — through ``POST /admin/config/api/test``.
//
// Why not SchemaForm: the form for ApiConnectorConfig has 20+ fields, most of them only
// relevant for one auth method or one content type; SchemaForm renders them all flat in
// per-x_group tabs. nomaubl's editor scopes the fields per method + groups them
// semantically (Connection / Authentication / Endpoints) so the operator sees only what's
// useful. The Test tab here mirrors nomaubl's — pick the endpoint, parameters appear
// automatically with each declared name/label/default, click Run.
import { useContext, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import {
  Activity, ChevronDown, ChevronRight, Plus, Trash2,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Checkbox, FrameworkEnumsContext, Input, Row, SearchSelect, Stack, useModals,
} from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

// ── shape of a parsed [connectors.<name>] dict for type=api ────────────────────────────────
//
// Matches what ``ApiConnectorConfig`` in :mod:`liberty.connectors.config` emits with default
// keys dropped. Every field is optional at the editor surface — newly-added connectors start
// with just ``type`` + ``base_url``.

type Endpoint = {
  name: string
  label?: string | null
  description?: string | null
  method?: string
  path?: string
  headers?: Record<string, string>
  query_params?: Record<string, string>
  body?: string | null
  content_type?: string
  response_field?: string | null
  response_map?: Record<string, string>
  params?: Array<{ name: string; label?: string | null; default?: string | null }>
}

export type ApiConnector = {
  type: 'api'
  licensed?: boolean
  show_in_switcher?: boolean
  home?: string | null
  base_url?: string
  timeout?: number                                          // seconds (Pydantic side)
  verify_ssl?: boolean
  default_headers?: Record<string, string>
  auth_type?: 'none' | 'basic' | 'bearer' | 'api_key' | 'oauth2'
  auth_username?: string | null
  auth_password?: string | null
  auth_token?: string | null
  auth_api_key_header?: string
  auth_token_endpoint?: string | null
  auth_token_field?: string | null
  auth_token_body?: string | null
  auth_token_content_type?: string
  auth_token_headers?: Record<string, string>
  auth_token_ttl?: number                                   // seconds (Pydantic side)
  endpoints?: Endpoint[]
}

interface ApiConnectorEditorProps {
  name: string
  value: ApiConnector
  onChange: (next: ApiConnector) => void
}

// ── small styled atoms (matches the screenshot's borderless look) ──────────────────────────
const TabsBar = styled.div`
  display: flex; gap: 14px; border-bottom: 1px solid ${colors.border};
  margin-bottom: 18px; padding: 0 2px;
`
const TabBtn = styled.button<{ $active?: boolean }>`
  background: transparent; border: none; padding: 10px 4px 12px;
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  font-size: ${fontSize.sm}; cursor: pointer; font-family: ${fonts.sans};
  border-bottom: 2px solid ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  margin-bottom: -1px;
  &:hover { color: ${colors.blue.main}; }
`
const SectionHead = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.micro}; letter-spacing: 0.5px;
  text-transform: uppercase; padding: 14px 0 8px; border-bottom: 1px solid ${colors.border};
  margin-bottom: 12px;
`
const Hint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro}; padding: 4px 2px;`
// One field row: label on the left (fixed width), control on the right.
const FieldRow = styled.div`
  display: grid; grid-template-columns: 160px 1fr; align-items: center; gap: 12px;
  padding: 6px 0;
  & > label { color: ${colors.text.primary}; font-size: ${fontSize.sm}; }
`
// A wider control area used for textareas — drops the label-aligned center for top-align.
const FieldRowTop = styled(FieldRow)`align-items: start; & > label { padding-top: 8px; }`
const Textarea = styled.textarea`
  width: 100%; min-height: 80px; padding: 8px 10px; border: 1px solid ${colors.border};
  background: ${colors.bg.input}; color: ${colors.text.primary}; border-radius: ${radius.sm};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; resize: vertical;
  &:focus { outline: none; border-color: ${colors.blue.border}; }
`
// Endpoint card — collapsible. Header carries METHOD pill + name; click toggles body.
const EndpointCard = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  margin-bottom: 10px;
`
const EndpointHead = styled.button<{ $open?: boolean }>`
  display: flex; align-items: center; gap: 12px; width: 100%; padding: 10px 14px;
  text-align: left; cursor: pointer; background: transparent; border: none;
  color: ${colors.text.primary}; font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  border-bottom: 1px solid ${({ $open }) => ($open ? colors.border : 'transparent')};
  & .method {
    display: inline-block; padding: 2px 8px; border-radius: ${radius.sm};
    background: ${colors.blue.bg}; color: ${colors.blue.main};
    font-size: ${fontSize.micro}; font-weight: 600; min-width: 44px; text-align: center;
  }
  & .name { font-family: ${fonts.mono}; }
  & .grow { flex: 1; }
`
const EndpointBody = styled.div`padding: 14px;`
const MethodPill = styled.span<{ $tone: string }>`
  display: inline-block; padding: 2px 8px; border-radius: ${radius.sm};
  background: ${colors.bg.card}; color: ${colors.blue.main};
  font-size: ${fontSize.micro}; font-weight: 600; min-width: 44px; text-align: center;
  margin-right: 8px;
`
// Parameters / Response mappings table. Three columns: name / label / default + delete.
const TableHead = styled.div`
  display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 10px; padding: 6px 4px;
  color: ${colors.text.muted}; font-size: ${fontSize.micro}; letter-spacing: 0.5px; text-transform: uppercase;
`
const TableRow = styled.div`
  display: grid; grid-template-columns: 1fr 1fr 1fr auto; gap: 10px; padding: 4px 0;
`
const MapRow = styled.div`
  display: grid; grid-template-columns: 1fr 2fr auto; gap: 10px; padding: 4px 0;
`
const TestParamRow = styled.div`
  display: grid; grid-template-columns: 1fr 2fr; gap: 16px; padding: 4px 0;
`
const ResultBox = styled.pre`
  background: ${colors.bg.input}; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  padding: 12px; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  white-space: pre-wrap; word-break: break-word; max-height: 360px; overflow-y: auto;
  margin: 0;
`

// ── headers / query-params helpers ────────────────────────────────────────────────────────
// nomaubl-style display: ``Key:Value;Key:Value`` for headers, ``key=val&key=val`` for query
// params. Pydantic side stores plain dicts — we convert at the editing surface so the
// operator types the compact form they're used to.
const headersToString = (h: Record<string, string> | undefined): string =>
  Object.entries(h ?? {}).map(([k, v]) => `${k}:${v}`).join(';')
const headersFromString = (s: string): Record<string, string> => {
  const out: Record<string, string> = {}
  for (const part of (s || '').split(';')) {
    const trimmed = part.trim()
    if (!trimmed) continue
    const colon = trimmed.indexOf(':')
    if (colon < 0) continue                                  // skip malformed pairs (operator's still typing)
    const k = trimmed.slice(0, colon).trim()
    const v = trimmed.slice(colon + 1).trim()
    if (k) out[k] = v
  }
  return out
}
const queryToString = (q: Record<string, string> | undefined): string =>
  Object.entries(q ?? {}).map(([k, v]) => `${k}=${v}`).join('&')
const queryFromString = (s: string): Record<string, string> => {
  const out: Record<string, string> = {}
  for (const part of (s || '').split('&')) {
    const trimmed = part.trim()
    if (!trimmed) continue
    const eq = trimmed.indexOf('=')
    if (eq < 0) { out[trimmed] = ''; continue }
    const k = trimmed.slice(0, eq).trim()
    const v = trimmed.slice(eq + 1)
    if (k) out[k] = v
  }
  return out
}

// ── main component ─────────────────────────────────────────────────────────────────────────
type TabKey = 'connection' | 'auth' | 'endpoints' | 'webhooks' | 'test'

export default function ApiConnectorEditor({ name, value, onChange }: ApiConnectorEditorProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<TabKey>('connection')
  // Reset to the Connection tab when the operator switches connectors — otherwise they may
  // be left looking at the Test tab of a different connector's data.
  useEffect(() => { setTab('connection') }, [name])

  // Generic per-field patch — preserves every other field on the connector. Drops the key
  // when the new value is null/undefined/empty so the saved TOML stays terse (matches the
  // /admin/config/connectors/parsed PUT's exclude-defaults convention).
  const patch = (p: Partial<ApiConnector>): void => {
    const next: Record<string, unknown> = { ...value }
    for (const [k, v] of Object.entries(p)) {
      if (v === undefined || v === null || v === '') delete next[k]
      else next[k] = v
    }
    onChange(next as ApiConnector)
  }

  return (
    <Stack gap={0}>
      <TabsBar>
        <TabBtn type="button" $active={tab === 'connection'} onClick={() => setTab('connection')}>
          {t('settings.api.tab.connection', 'Connection')}
        </TabBtn>
        <TabBtn type="button" $active={tab === 'auth'} onClick={() => setTab('auth')}>
          {t('settings.api.tab.authentication', 'Authentication')}
        </TabBtn>
        <TabBtn type="button" $active={tab === 'endpoints'} onClick={() => setTab('endpoints')}>
          {t('settings.api.tab.endpoints', 'Endpoints')}
        </TabBtn>
        <TabBtn type="button" $active={tab === 'webhooks'} onClick={() => setTab('webhooks')}>
          {t('settings.api.tab.webhooks', 'Webhooks')}
        </TabBtn>
        <TabBtn type="button" $active={tab === 'test'} onClick={() => setTab('test')}>
          {t('settings.api.tab.test', 'Test')}
        </TabBtn>
      </TabsBar>
      {tab === 'connection' && <ConnectionTab value={value} patch={patch} />}
      {tab === 'auth' && <AuthTab value={value} patch={patch} />}
      {tab === 'endpoints' && <EndpointsTab value={value} patch={patch} />}
      {tab === 'webhooks' && <WebhooksTab />}
      {tab === 'test' && <TestTab connectorName={name} value={value} />}
    </Stack>
  )
}

// ── Connection tab ─────────────────────────────────────────────────────────────────────────
function ConnectionTab({
  value, patch,
}: { value: ApiConnector; patch: (p: Partial<ApiConnector>) => void }) {
  const { t } = useTranslation()
  // Timeout displayed in milliseconds (nomaubl convention) — Pydantic stores seconds. Round
  // on display so a fractional second doesn't surprise the operator.
  const timeoutMs = value.timeout != null ? Math.round((value.timeout as number) * 1000) : 30000
  const [headersText, setHeadersText] = useState(headersToString(value.default_headers))
  // Re-sync the display string when the parent value changes (e.g. switching connectors).
  useEffect(() => { setHeadersText(headersToString(value.default_headers)) }, [value.default_headers])
  // App-level settings (show_in_switcher / home) — same as SQL connectors. Home options come
  // from the surrounding FrameworkEnumsContext (ConnectorsBuilder augments MENU_HOME_ITEMS for
  // the currently-selected connector).
  const enums = useContext(FrameworkEnumsContext)
  const homeOpts = ((enums?.MENU_HOME_ITEMS?.values ?? []) as Array<{ value: string; label: string; mono?: string }>)
  return (
    <Stack gap={4}>
      <SectionHead>{t('settings.api.app.section', 'App')}</SectionHead>
      <FieldRow>
        <label>{t('settings.api.app.showInSwitcher', 'Show in switcher')}</label>
        <Checkbox
          checked={value.show_in_switcher !== false}
          onChange={(v: boolean) => patch({ show_in_switcher: v ? undefined : false })}
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.app.home', 'Home')}</label>
        <SearchSelect
          value={value.home ?? ''}
          options={homeOpts}
          onChange={(v) => patch({ home: v || null })}
          placeholder={t('common.pick', 'Pick…')}
        />
      </FieldRow>
      <SectionHead>{t('settings.api.connection.section', 'Connection')}</SectionHead>
      <FieldRow>
        <label>{t('settings.api.connection.baseUrl', 'Base URL')}</label>
        <Input
          value={value.base_url ?? ''}
          onChange={(e) => patch({ base_url: e.target.value || undefined })}
          placeholder="https://api.example.com"
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.connection.timeout', 'Timeout (ms)')}</label>
        <Input
          type="number"
          value={String(timeoutMs)}
          onChange={(e) => {
            const ms = Number(e.target.value)
            patch({ timeout: Number.isFinite(ms) && ms > 0 ? ms / 1000 : undefined })
          }}
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.connection.sslVerify', 'SSL Verify')}</label>
        <SearchSelect
          value={value.verify_ssl === false ? 'false' : 'true'}
          options={[
            { value: 'true', label: t('settings.api.connection.sslOn', 'true (verify cert)') },
            { value: 'false', label: t('settings.api.connection.sslOff', 'false (disable cert check)') },
          ]}
          onChange={(v) => patch({ verify_ssl: v === 'false' ? false : undefined })}
        />
      </FieldRow>
      <SectionHead>{t('settings.api.connection.defaultHeaders', 'Default headers')}</SectionHead>
      <FieldRowTop>
        <label>{t('settings.api.connection.headers', 'Headers')}</label>
        <div>
          <Textarea
            value={headersText}
            onChange={(e) => {
              setHeadersText(e.target.value)
              patch({ default_headers: headersFromString(e.target.value) })
            }}
            placeholder="Content-Type:application/json"
          />
          <Hint>{t('settings.api.connection.headersHint',
            'Semicolon-separated Key:Value pairs applied to every request. Endpoint headers can override these.')}</Hint>
        </div>
      </FieldRowTop>
    </Stack>
  )
}

// ── Authentication tab ─────────────────────────────────────────────────────────────────────
function AuthTab({
  value, patch,
}: { value: ApiConnector; patch: (p: Partial<ApiConnector>) => void }) {
  const { t } = useTranslation()
  const authType = value.auth_type ?? 'none'
  // Token TTL displayed in minutes (matches the screenshot's nomaubl convention). Pydantic
  // stores seconds. Convert on read + write.
  const ttlMin = value.auth_token_ttl != null ? Math.round((value.auth_token_ttl as number) / 60) : 55
  const [tokenHeadersText, setTokenHeadersText] = useState(headersToString(value.auth_token_headers))
  useEffect(() => { setTokenHeadersText(headersToString(value.auth_token_headers)) }, [value.auth_token_headers])
  const showCredentials = authType === 'basic' || authType === 'oauth2'
  const showToken = authType === 'bearer' || authType === 'api_key'
  const showApiKeyHeader = authType === 'api_key'
  const showTokenEndpoint = authType === 'oauth2'
  return (
    <Stack gap={4}>
      <SectionHead>{t('settings.api.auth.section', 'Auth type')}</SectionHead>
      <FieldRow>
        <label>{t('settings.api.auth.method', 'Method')}</label>
        <SearchSelect
          value={authType}
          options={[
            { value: 'none', label: t('settings.api.auth.methodNone', 'None') },
            { value: 'basic', label: t('settings.api.auth.methodBasic', 'Basic (username + password)') },
            { value: 'bearer', label: t('settings.api.auth.methodBearer', 'Bearer (static token)') },
            { value: 'api_key', label: t('settings.api.auth.methodApiKey', 'API Key (header)') },
            { value: 'oauth2', label: t('settings.api.auth.methodOauth2', 'OAuth2 / JWT (token endpoint)') },
          ]}
          onChange={(v) => patch({ auth_type: (v as ApiConnector['auth_type']) ?? 'none' })}
        />
      </FieldRow>

      {showCredentials && (
        <>
          <SectionHead>{t('settings.api.auth.credentials', 'Credentials')}</SectionHead>
          <FieldRow>
            <label>{t('settings.api.auth.username', 'Username')}</label>
            <Input
              value={value.auth_username ?? ''}
              onChange={(e) => patch({ auth_username: e.target.value || undefined })}
              placeholder={t('settings.api.auth.usernamePlaceholder', 'used in {{username}} placeholder + basic auth')}
            />
          </FieldRow>
          <FieldRow>
            <label>{t('settings.api.auth.password', 'Password')}</label>
            <Input
              type="password"
              value={value.auth_password ?? ''}
              onChange={(e) => patch({ auth_password: e.target.value || undefined })}
              placeholder={t('settings.api.auth.passwordPlaceholder', 'used in {{password}} placeholder + basic auth (may be an ENC: value)')}
            />
          </FieldRow>
        </>
      )}

      {showToken && (
        <>
          <SectionHead>{t('settings.api.auth.token', 'Token')}</SectionHead>
          <FieldRow>
            <label>{t('settings.api.auth.tokenLabel', 'Token')}</label>
            <Input
              type="password"
              value={value.auth_token ?? ''}
              onChange={(e) => patch({ auth_token: e.target.value || undefined })}
              placeholder={t('settings.api.auth.tokenPlaceholder', 'static bearer / API key — also available as {{token}}')}
            />
          </FieldRow>
        </>
      )}

      {showApiKeyHeader && (
        <FieldRow>
          <label>{t('settings.api.auth.apiKeyHeader', 'Header name')}</label>
          <Input
            value={value.auth_api_key_header ?? 'X-Api-Key'}
            onChange={(e) => patch({ auth_api_key_header: e.target.value || undefined })}
            placeholder="X-Api-Key"
          />
        </FieldRow>
      )}

      {showTokenEndpoint && (
        <>
          <SectionHead>{t('settings.api.auth.tokenEndpointSection', 'Token endpoint')}</SectionHead>
          <FieldRow>
            <label>{t('settings.api.auth.tokenEndpoint', 'Endpoint path')}</label>
            <Input
              value={value.auth_token_endpoint ?? ''}
              onChange={(e) => patch({ auth_token_endpoint: e.target.value || undefined })}
              placeholder="/v2/tokenrequest"
            />
          </FieldRow>
          <FieldRowTop>
            <label>{t('settings.api.auth.tokenField', 'Token field')}</label>
            <div>
              <Input
                value={value.auth_token_field ?? ''}
                onChange={(e) => patch({ auth_token_field: e.target.value || undefined })}
                placeholder="access_token"
              />
              <Hint>{t('settings.api.auth.tokenFieldHint',
                'Dot-notation JSON path to extract the token from the response (e.g. userInfo.token for JD Edwards AIS). Leave empty to auto-detect access_token then token.')}</Hint>
            </div>
          </FieldRowTop>
          <FieldRowTop>
            <label>{t('settings.api.auth.tokenTtl', 'Token TTL (minutes)')}</label>
            <div>
              <Input
                type="number"
                value={String(ttlMin)}
                onChange={(e) => {
                  const m = Number(e.target.value)
                  patch({ auth_token_ttl: Number.isFinite(m) && m > 0 ? m * 60 : undefined })
                }}
              />
              <Hint>{t('settings.api.auth.tokenTtlHint',
                'How long the token is cached before a new one is fetched. Default: 55 minutes.')}</Hint>
            </div>
          </FieldRowTop>
          <FieldRow>
            <label>{t('settings.api.auth.tokenContentType', 'Body Content-Type')}</label>
            <SearchSelect
              value={value.auth_token_content_type ?? 'application/json'}
              options={[
                { value: 'application/json', label: 'application/json' },
                { value: 'application/x-www-form-urlencoded', label: 'application/x-www-form-urlencoded' },
              ]}
              allowCustom
              onChange={(v) => patch({ auth_token_content_type: v || undefined })}
            />
          </FieldRow>
          <FieldRowTop>
            <label>{t('settings.api.auth.tokenBody', 'Body template')}</label>
            <div>
              <Textarea
                value={value.auth_token_body ?? ''}
                onChange={(e) => patch({ auth_token_body: e.target.value || undefined })}
                placeholder='{"deviceName":"ais"}'
              />
              <Hint>{t('settings.api.auth.tokenBodyHint',
                'JSON: leave empty to use the default { username, password, deviceName } payload (JD Edwards AIS). Form-urlencoded: leave empty for grant_type=client_credentials with {{username}} / {{password}}.')}</Hint>
            </div>
          </FieldRowTop>
          <FieldRowTop>
            <label>{t('settings.api.auth.tokenHeaders', 'Token request headers')}</label>
            <div>
              <Textarea
                value={tokenHeadersText}
                onChange={(e) => {
                  setTokenHeadersText(e.target.value)
                  patch({ auth_token_headers: headersFromString(e.target.value) })
                }}
                placeholder="customer-id:CUST123;X-Tenant:acme"
              />
              <Hint>{t('settings.api.auth.tokenHeadersHint',
                'Extra headers for the token request (e.g. a client-id header). Same Semicolon-separated Key:Value pairs.')}</Hint>
            </div>
          </FieldRowTop>
        </>
      )}
    </Stack>
  )
}

// ── Endpoints tab ──────────────────────────────────────────────────────────────────────────
function EndpointsTab({
  value, patch,
}: { value: ApiConnector; patch: (p: Partial<ApiConnector>) => void }) {
  const { t } = useTranslation()
  const modals = useModals()
  const endpoints = value.endpoints ?? []
  const setEndpoints = (next: Endpoint[]) => patch({ endpoints: next.length ? next : undefined })
  const [openIdx, setOpenIdx] = useState<number | null>(endpoints.length ? 0 : null)

  const addEndpoint = async () => {
    const name = (await modals.prompt({
      title: t('settings.api.endpoint.add', 'Add endpoint'),
      message: t('settings.api.endpoint.namePrompt', 'Endpoint name (used by actions):'),
      validate: (v) => {
        if (!v) return null
        if (endpoints.some((e) => e.name === v)) return t('settings.api.endpoint.nameExists', { defaultValue: '"{{name}}" already exists.', name: v })
        if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(v)) return t('settings.api.endpoint.invalidName', 'Letters / digits / underscores only; start with a letter.')
        return null
      },
    }))?.trim()
    if (!name) return
    setEndpoints([...endpoints, { name, method: 'GET', path: '', content_type: 'application/json' }])
    setOpenIdx(endpoints.length)
  }
  const removeEndpoint = async (idx: number) => {
    const ep = endpoints[idx]
    const ok = await modals.confirm({
      title: t('settings.api.endpoint.delete', 'Delete endpoint'),
      message: t('settings.api.endpoint.confirmDelete', { defaultValue: 'Delete endpoint "{{name}}"?', name: ep.name }),
      variant: 'danger',
    })
    if (!ok) return
    setEndpoints(endpoints.filter((_, i) => i !== idx))
    setOpenIdx(null)
  }
  const updateEndpoint = (idx: number, ep: Endpoint) => {
    const next = endpoints.slice()
    next[idx] = ep
    setEndpoints(next)
  }
  return (
    <Stack gap={6}>
      {endpoints.length === 0 && (
        <Hint>{t('settings.api.endpoint.empty', 'No endpoints yet — add one to expose a callable HTTP request.')}</Hint>
      )}
      {endpoints.map((ep, i) => {
        const open = openIdx === i
        return (
          <EndpointCard key={`${ep.name}-${i}`}>
            <EndpointHead $open={open} onClick={() => setOpenIdx(open ? null : i)} type="button">
              {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <MethodPill $tone={ep.method ?? 'GET'}>{ep.method ?? 'GET'}</MethodPill>
              <span className="name">{ep.name || '(unnamed)'}</span>
              {ep.label && <span style={{ color: colors.text.muted, fontFamily: fonts.sans }}>· {ep.label}</span>}
              <span className="grow" />
              <Trash2 size={13} style={{ color: colors.text.muted }} onClick={(e) => { e.stopPropagation(); void removeEndpoint(i) }} />
            </EndpointHead>
            {open && (
              <EndpointBody>
                <EndpointEditor value={ep} onChange={(v) => updateEndpoint(i, v)} />
              </EndpointBody>
            )}
          </EndpointCard>
        )
      })}
      <Button $variant="ghost" $size="sm" onClick={addEndpoint} style={{ alignSelf: 'flex-start' }}>
        <Plus size={13} /> {t('settings.api.endpoint.add', 'Add endpoint')}
      </Button>
    </Stack>
  )
}

// ── per-endpoint editor ────────────────────────────────────────────────────────────────────
function EndpointEditor({ value, onChange }: { value: Endpoint; onChange: (v: Endpoint) => void }) {
  const { t } = useTranslation()
  const set = (p: Partial<Endpoint>): void => {
    const next: Record<string, unknown> = { ...value }
    for (const [k, v] of Object.entries(p)) {
      if (v === undefined || v === null || v === '') delete next[k]
      else next[k] = v
    }
    onChange(next as Endpoint)
  }
  const [headersText, setHeadersText] = useState(headersToString(value.headers))
  const [queryText, setQueryText] = useState(queryToString(value.query_params))
  useEffect(() => { setHeadersText(headersToString(value.headers)) }, [value.headers])
  useEffect(() => { setQueryText(queryToString(value.query_params)) }, [value.query_params])

  const formatBody = () => {
    if (!value.body) return
    try {
      const obj = JSON.parse(value.body)
      set({ body: JSON.stringify(obj, null, 2) })
    } catch {
      // Not JSON or invalid — leave untouched. (We could show a tiny banner, but the
      // operator usually notices: the button just didn't reformat anything.)
    }
  }
  const params = value.params ?? []
  const setParams = (next: NonNullable<Endpoint['params']>) =>
    set({ params: next.length ? next : undefined })
  const respMap = value.response_map ?? {}
  const respMapEntries = Object.entries(respMap)

  return (
    <Stack gap={4}>
      <FieldRow>
        <label>{t('settings.api.endpoint.name', 'Name')}</label>
        <Row gap={10}>
          <Input value={value.name} onChange={(e) => set({ name: e.target.value })} />
          <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.api.endpoint.label', 'Label')}</span>
          <Input
            value={value.label ?? ''}
            onChange={(e) => set({ label: e.target.value || undefined })}
            placeholder={t('settings.api.endpoint.labelPlaceholder', 'Short label shown in listings')}
          />
        </Row>
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.endpoint.method', 'Method')}</label>
        <Row gap={10}>
          <div style={{ width: 160 }}>
            <SearchSelect
              value={value.method ?? 'GET'}
              options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].map((m) => ({ value: m, label: m }))}
              onChange={(v) => set({ method: v ?? 'GET' })}
            />
          </div>
          <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.api.endpoint.urlPath', 'URL path')}</span>
          <div style={{ flex: 1 }}>
            <Input
              value={value.path ?? ''}
              onChange={(e) => set({ path: e.target.value })}
              placeholder="/v2/report/execute"
            />
          </div>
        </Row>
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.endpoint.headers', 'Extra headers')}</label>
        <Input
          value={headersText}
          onChange={(e) => {
            setHeadersText(e.target.value)
            set({ headers: headersFromString(e.target.value) })
          }}
          placeholder="X-Custom:value;Authorization:Bearer {{token}}"
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.endpoint.contentType', 'Content-Type')}</label>
        <SearchSelect
          value={value.content_type ?? 'application/json'}
          options={[
            { value: 'application/json', label: 'application/json' },
            { value: 'application/x-www-form-urlencoded', label: 'application/x-www-form-urlencoded' },
            { value: 'multipart/form-data', label: 'multipart/form-data' },
            { value: 'text/plain', label: 'text/plain' },
            { value: 'application/xml', label: 'application/xml' },
          ]}
          allowCustom
          onChange={(v) => set({ content_type: v || undefined })}
        />
      </FieldRow>
      <FieldRowTop>
        <label>{t('settings.api.endpoint.body', 'Body')}</label>
        <div>
          <Textarea
            style={{ minHeight: 160 }}
            value={value.body ?? ''}
            onChange={(e) => set({ body: e.target.value || undefined })}
            placeholder='{"reportName":"{{reportName}}"}'
          />
          <Row gap={10} style={{ marginTop: 4 }}>
            <Button $variant="ghost" $size="sm" onClick={formatBody}>{t('settings.api.endpoint.formatJson', 'Format JSON')}</Button>
            <Hint>{t('settings.api.endpoint.bodyHint', 'Use {{param}} placeholders for variables')}</Hint>
          </Row>
        </div>
      </FieldRowTop>
      <FieldRow>
        <label>{t('settings.api.endpoint.queryParams', 'Query params')}</label>
        <Input
          value={queryText}
          onChange={(e) => {
            setQueryText(e.target.value)
            set({ query_params: queryFromString(e.target.value) })
          }}
          placeholder="pageSize={{pageSize}}&page={{page}}"
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.endpoint.responseField', 'Response field')}</label>
        <Input
          value={value.response_field ?? ''}
          onChange={(e) => set({ response_field: e.target.value || undefined })}
          placeholder="data.items"
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.endpoint.description', 'Description')}</label>
        <Input
          value={value.description ?? ''}
          onChange={(e) => set({ description: e.target.value || undefined })}
          placeholder={t('settings.api.endpoint.descriptionPlaceholder', 'Short description')}
        />
      </FieldRow>

      <SectionHead>
        {t('settings.api.endpoint.responseMappings', 'Response mappings')}
        {' — '}
        <span style={{ textTransform: 'none', letterSpacing: 0 }}>
          {t('settings.api.endpoint.responseMappingsHint', 'map logical names to JSON dot-notation paths in the response')}
        </span>
      </SectionHead>
      {respMapEntries.length === 0 && (
        <Hint>{t('settings.api.endpoint.responseMappingsEmpty', 'No mappings — the whole response (or the response_field path) is returned as-is.')}</Hint>
      )}
      {respMapEntries.map(([key, val], i) => (
        <MapRow key={`${key}-${i}`}>
          <Input
            value={key}
            onChange={(e) => {
              const nk = e.target.value
              const copy = { ...respMap }
              delete copy[key]
              if (nk) copy[nk] = val
              set({ response_map: Object.keys(copy).length ? copy : undefined })
            }}
            placeholder="logical_name"
          />
          <Input
            value={val}
            onChange={(e) => set({ response_map: { ...respMap, [key]: e.target.value } })}
            placeholder="data.0.field"
          />
          <Button
            $variant="ghost" $size="sm"
            onClick={() => {
              const copy = { ...respMap }
              delete copy[key]
              set({ response_map: Object.keys(copy).length ? copy : undefined })
            }}
            title={t('common.remove', 'Remove')}
          >
            <Trash2 size={13} />
          </Button>
        </MapRow>
      ))}
      <Button
        $variant="ghost" $size="sm" style={{ alignSelf: 'flex-start' }}
        onClick={() => {
          // Find a fresh placeholder key.
          let k = 'new_mapping'; let n = 1
          while (k in respMap) { n += 1; k = `new_mapping_${n}` }
          set({ response_map: { ...respMap, [k]: '' } })
        }}
      >
        <Plus size={13} /> {t('settings.api.endpoint.addMapping', 'Add mapping')}
      </Button>

      <SectionHead>
        {t('settings.api.endpoint.parameters', 'Parameters')}
        {' — '}
        <span style={{ textTransform: 'none', letterSpacing: 0 }}>
          {t('settings.api.endpoint.parametersHint', 'declare {{placeholder}} variables used in URL / headers / body')}
        </span>
      </SectionHead>
      <TableHead>
        <span>{t('settings.api.endpoint.paramName', 'Name')}</span>
        <span>{t('settings.api.endpoint.paramLabel', 'Label')}</span>
        <span>{t('settings.api.endpoint.paramDefault', 'Default value')}</span>
        <span />
      </TableHead>
      {params.length === 0 && (
        <Hint>{t('settings.api.endpoint.parametersEmpty', 'No parameters declared.')}</Hint>
      )}
      {params.map((p, i) => (
        <TableRow key={i}>
          <Input
            value={p.name}
            onChange={(e) => {
              const next = params.slice()
              next[i] = { ...p, name: e.target.value }
              setParams(next)
            }}
            placeholder="reportName"
          />
          <Input
            value={p.label ?? ''}
            onChange={(e) => {
              const next = params.slice()
              next[i] = { ...p, label: e.target.value || undefined }
              setParams(next)
            }}
            placeholder="Report Name"
          />
          <Input
            value={p.default ?? ''}
            onChange={(e) => {
              const next = params.slice()
              next[i] = { ...p, default: e.target.value || undefined }
              setParams(next)
            }}
            placeholder="R0010P"
          />
          <Button
            $variant="ghost" $size="sm"
            onClick={() => setParams(params.filter((_, j) => j !== i))}
            title={t('common.remove', 'Remove')}
          >
            <Trash2 size={13} />
          </Button>
        </TableRow>
      ))}
      <Button
        $variant="ghost" $size="sm" style={{ alignSelf: 'flex-start' }}
        onClick={() => setParams([...params, { name: '' }])}
      >
        <Plus size={13} /> {t('settings.api.endpoint.addParam', 'Add parameter')}
      </Button>
    </Stack>
  )
}

// ── Webhooks tab — placeholder for the inbound-webhook slice ──────────────────────────────
function WebhooksTab() {
  const { t } = useTranslation()
  return (
    <Stack gap={10}>
      <Hint>{t('settings.api.webhooks.coming', 'Inbound webhooks (HMAC signature + dedup + status mapping) — coming in a follow-up slice. Today, external systems can call ``POST /api/http/...`` against API connectors but cannot push events INTO Liberty.')}</Hint>
    </Stack>
  )
}

// ── Test tab ───────────────────────────────────────────────────────────────────────────────
function TestTab({ connectorName, value }: { connectorName: string; value: ApiConnector }) {
  const { t } = useTranslation()
  const endpoints = useMemo(() => value.endpoints ?? [], [value.endpoints])
  const [selEndpoint, setSelEndpoint] = useState<string>(endpoints[0]?.name ?? '')
  const ep = useMemo(() => endpoints.find((e) => e.name === selEndpoint), [endpoints, selEndpoint])
  // The parameter values the operator overrides for this run. Initialised from each
  // parameter's ``default`` so the operator only has to change what they want to vary.
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  // When the endpoint changes, re-seed the inputs from its declared parameters.
  useEffect(() => {
    if (!ep) { setParamValues({}); return }
    const seeded: Record<string, string> = {}
    for (const p of ep.params ?? []) seeded[p.name] = p.default ?? ''
    setParamValues(seeded)
  }, [ep])
  const [extraParams, setExtraParams] = useState<Array<{ name: string; value: string }>>([])
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<unknown>(null)
  const [error, setError] = useState<string | null>(null)

  const run = async () => {
    if (!ep) return
    setBusy(true); setError(null); setResult(null)
    try {
      const params: Record<string, string> = { ...paramValues }
      for (const x of extraParams) if (x.name) params[x.name] = x.value
      const r = await api.post<unknown>('/admin/config/api/test', {
        config: value,
        test_endpoint: ep.name,
        params,
      })
      setResult(r)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  // Connector-only test (no endpoint): just validate the config + try OAuth2 token fetch.
  const runConnectorOnly = async () => {
    setBusy(true); setError(null); setResult(null)
    try {
      const r = await api.post<unknown>('/admin/config/api/test', { config: value })
      setResult(r)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Stack gap={4}>
      <SectionHead>{t('settings.api.test.section', 'Select endpoint')}</SectionHead>
      {endpoints.length === 0 ? (
        <>
          <Hint>{t('settings.api.test.noEndpoints', 'No endpoints declared yet. You can still validate the connector\'s connection / authentication.')}</Hint>
          <Row gap={10} style={{ marginTop: 8 }}>
            <Button $variant="primary" $size="sm" disabled={busy} onClick={runConnectorOnly}>
              <Activity size={13} /> {t('settings.api.test.runConnector', 'Test connection')}
            </Button>
            {busy && <Hint>{t('common.loading', 'Loading…')}</Hint>}
          </Row>
        </>
      ) : (
        <>
          <FieldRow>
            <label>{t('settings.api.test.endpoint', 'Endpoint')}</label>
            <SearchSelect
              value={selEndpoint}
              options={endpoints.map((e) => ({
                value: e.name,
                label: e.label ? `${e.name} — ${e.label}` : e.name,
                mono: e.name,
              }))}
              onChange={(v) => setSelEndpoint(v ?? '')}
            />
          </FieldRow>
          <SectionHead>{t('settings.api.test.parameters', 'Parameters')}</SectionHead>
          <Hint>{t('settings.api.test.paramsHint', 'Override or supply {{placeholder}} values at runtime.')}</Hint>
          {(ep?.params ?? []).map((p) => (
            <TestParamRow key={p.name}>
              <Input value={p.name} readOnly />
              <Input
                value={paramValues[p.name] ?? ''}
                onChange={(e) => setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))}
                placeholder={p.default ?? ''}
              />
            </TestParamRow>
          ))}
          {extraParams.map((x, i) => (
            <TestParamRow key={`extra-${i}`}>
              <Input
                value={x.name}
                onChange={(e) => {
                  const next = extraParams.slice()
                  next[i] = { ...x, name: e.target.value }
                  setExtraParams(next)
                }}
                placeholder="param name"
              />
              <Row gap={6}>
                <Input
                  value={x.value}
                  onChange={(e) => {
                    const next = extraParams.slice()
                    next[i] = { ...x, value: e.target.value }
                    setExtraParams(next)
                  }}
                  placeholder="value"
                />
                <Button $variant="ghost" $size="sm" onClick={() => setExtraParams(extraParams.filter((_, j) => j !== i))}>
                  <Trash2 size={13} />
                </Button>
              </Row>
            </TestParamRow>
          ))}
          <Button
            $variant="ghost" $size="sm" style={{ alignSelf: 'flex-start' }}
            onClick={() => setExtraParams([...extraParams, { name: '', value: '' }])}
          >
            <Plus size={13} /> {t('settings.api.test.addParam', 'Add param')}
          </Button>
          <SectionHead>{t('settings.api.test.result', 'Result')}</SectionHead>
          <Row gap={10}>
            <Button $variant="primary" $size="sm" disabled={busy || !ep} onClick={run}>
              <Activity size={13} /> {t('settings.api.test.run', 'Run')}
            </Button>
            {busy && <Hint>{t('common.loading', 'Loading…')}</Hint>}
          </Row>
        </>
      )}
      {error && <Banner $tone="error">{error}</Banner>}
      {result != null && (
        <ResultBox>{JSON.stringify(result, null, 2)}</ResultBox>
      )}
      {/* Use `connectorName` so it isn't an unused prop; surfaces in the panel header. */}
      <Hint style={{ marginTop: 12 }}>{t('settings.api.test.runsAgainst', { defaultValue: 'Runs against connector "{{name}}" without saving.', name: connectorName })}</Hint>
    </Stack>
  )
}
