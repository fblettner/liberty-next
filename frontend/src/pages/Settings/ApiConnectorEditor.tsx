// Dedicated editor for a single API connector — replaces the SchemaNavigator path for
// ``[connectors.<name>]`` where ``type = "api"``. Five top-level tabs:
//
//   * **Connection** — base URL, timeout, SSL verify, default headers.
//   * **Authentication** — method picker + only the fields that method actually uses (OAuth2's
//     token endpoint section appears only when method is OAuth2 / JWT, etc.).
//   * **Environments** — the pool this server serves + one server per other pool (AIS per JDE
//     environment; ApiEnvironmentsTab).
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
import { Fragment, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { FlowCanvas, FlowRows } from './flowKit'
import { Activity, ChevronDown, ChevronRight, Plus, RefreshCw, Trash2, Wand2, Tag, LogIn, LogOut, Send, Globe, KeyRound, Server, Layers, Bell, Play, Maximize2 } from 'lucide-react'
import { EditorModalShell } from '../../common/EditorModalShell'
import StageStrip from './StageStrip'
import { apiFigures } from '../../services/connectorFigures'
import { api, ApiError } from '../../api/client'
import {
  Banner, Button, Checkbox, FrameworkEnumsContext, Input, Row, SearchSelect, Stack, useModals,
} from '../../common'
import { EndpointGeneratorModal, type EndpointGeneratorDef } from './EndpointGeneratorModal'
import { mergeGenerated, type GeneratedEndpoint } from '../../services/endpointRefresh'
import { colors, fontSize, fonts, radius } from '../../theme'
import { EndpointBody, EndpointCard, EndpointHead, FieldRow, Hint, SectionHead } from './apiEditorAtoms'
import ApiEnvironmentsTab from './ApiEnvironmentsTab'
import type { ApiTarget, SqlPools } from '../../services/apiTargets'

// ── shape of a parsed [connectors.<name>] dict for type=api ────────────────────────────────
//
// Matches what ``ApiConnectorConfig`` in :mod:`liberty.connectors.config` emits with default
// keys dropped. Every field is optional at the editor surface — newly-added connectors start
// with just ``type`` + ``base_url``.

export type Endpoint = {
  name: string
  label?: string | null
  description?: string | null
  group?: string | null
  method?: string
  path?: string
  headers?: Record<string, string>
  query_params?: Record<string, string>
  body?: string | null
  content_type?: string
  /** The endpoint changes something on the server: the assistant calls it only through its write tool. */
  writable?: boolean
  /** Set by "Create from…": what the endpoint was built from, for "Refresh from the server". */
  source?: { generator: string; candidate: string; options?: Record<string, unknown> } | null
  response_field?: string | null
  /** Where the server puts its errors when it answers 200 all the same: something there = the call failed. */
  /** A screen can read the endpoint: where its rows are, how their keys become columns, the titles, the "more" flag. */
  rows_field?: string | null
  row_map?: Record<string, string> | null
  labels_field?: string | null
  more_field?: string | null
  limit_param?: string | null
  skip_blank_query?: boolean
  callable?: string | null
  action?: string | null
  error_fields?: string[] | null
  /** Where the server puts its warnings: reported with the answer, the call stands. */
  warning_fields?: string[] | null
  response_map?: Record<string, string>
  params?: Array<{ name: string; label?: string | null; default?: string | null; help?: string | null; example?: string | null; choices?: string[] | null; required?: boolean }>
}

export type ApiConnector = {
  type: 'api'
  licensed?: boolean
  show_in_switcher?: boolean
  home?: string | null
  base_url?: string
  timeout?: number                                          // seconds (Pydantic side)
  /** A boolean, or an environment reference (``${JDE_AIS_VERIFY_SSL:-true}``) resolved by the server. */
  verify_ssl?: boolean | string
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
  /** Ways to create an endpoint from what the server describes — the Endpoints tab's "Create from…". */
  generators?: EndpointGeneratorDef[]
  /** The pool (environment) this server serves; ``targets`` = one server per other pool. */
  pool?: string | null
  targets?: Record<string, ApiTarget>
}

interface ApiConnectorEditorProps {
  name: string
  value: ApiConnector
  /** Does this connector have a menu in menus.toml? An API connector only appears in the app
   *  switcher when (a) ``show_in_switcher`` isn't false AND (b) it has a menu — without a menu
   *  the workspace can't render anything when it's picked, so the runtime filters it out. The
   *  editor uses this to flag the misleading "enabled but invisible" state. */
  hasMenu?: boolean
  /** Every SQL connector's pools — the Environments tab lists the environments without a server. */
  sqlConnectors?: SqlPools[]
  onChange: (next: ApiConnector) => void
  /** A link asked for "Create from…" (services/deepLinks.ts): the endpoints open, the dialog on this generator
   *  and text; `n` changes with each ask. */
  openGenerator?: { generator?: string; query?: string; n: number } | null
}

// ── small styled atoms (matches the screenshot's borderless look) ──────────────────────────
// A wider control area used for textareas — drops the label-aligned center for top-align.
const FieldRowTop = FieldRow
const Textarea = styled.textarea`
  width: 100%; min-height: 80px; padding: 8px 10px; border: 1px solid ${colors.border};
  background: ${colors.bg.input}; color: ${colors.text.primary}; border-radius: ${radius.sm};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; resize: vertical;
  &:focus { outline: none; border-color: ${colors.blue.border}; }
`
const MethodPill = styled.span<{ $tone: string }>`
  display: inline-block; padding: 2px 8px; border-radius: ${radius.sm};
  background: ${colors.bg.card}; color: ${colors.blue.main};
  font-size: ${fontSize.micro}; font-weight: 600; min-width: 44px; text-align: center;
  margin-right: 8px;
`
const ParamHelp = styled.div`
  grid-column: 1 / -1; color: ${colors.text.muted}; font-size: ${fontSize.micro}; padding: 0 2px 6px; white-space: pre-wrap; word-break: break-word;
`
const GroupHead = styled.button`
  display: flex; align-items: center; gap: 8px; width: 100%; margin-top: 6px; padding: 4px 2px;
  background: none; border: none; border-bottom: 1px solid ${colors.border}; cursor: pointer; text-align: left;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};
  & .count { font-weight: 400; color: ${colors.text.muted}; }
`
const WritesTag = styled.span`
  font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.orange.main};
  border: 1px solid ${colors.orange.main}; border-radius: 999px; padding: 0 7px; margin-left: 4px;
`
const GeneratorRow = styled.div`
  display: grid; grid-template-columns: 1fr 1.4fr 2fr 2fr auto; gap: 10px; padding: 2px 0;
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
type TabKey = 'connection' | 'auth' | 'environments' | 'endpoints' | 'webhooks' | 'test'

export default function ApiConnectorEditor({ name, value, hasMenu, sqlConnectors, onChange, openGenerator }: ApiConnectorEditorProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<TabKey>('connection')
  const fig = apiFigures(value as unknown as Record<string, unknown>)
  // Reset to the Connection tab when the operator switches connectors — otherwise they may
  // be left looking at the Test tab of a different connector's data.
  const askedN = openGenerator?.n
  // eslint-disable-next-line react-hooks/exhaustive-deps -- on another connector; a link's ask is the effect below
  useEffect(() => { setTab(askedN ? 'endpoints' : 'connection') }, [name])
  useEffect(() => { if (askedN) setTab('endpoints') }, [askedN])

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
      {/* the connector as its stages, like a pool or a table: each card says what it holds, in orange what it lacks */}
      <StageStrip compact dense={tab === 'endpoints' || tab === 'test'} current={tab} onPick={(k) => setTab(k as TabKey)} stages={[
        { id: 'connection', tone: 'blue', icon: <Globe size={15} />, title: t('settings.api.tab.connection'),
          what: fig.host || t('settings.api.stage.noAddress'), warn: !fig.host },
        { id: 'auth', tone: 'purple', icon: <KeyRound size={15} />, title: t('settings.api.tab.authentication'),
          what: t(`settings.api.stage.auth.${fig.auth}`), more: fig.authMissing ? t(`settings.api.stage.missing.${fig.authMissing}`) : (value.auth_username ?? ''), warn: !!fig.authMissing },
        { id: 'environments', tone: 'green', icon: <Server size={15} />, title: t('settings.api.tab.environments'),
          what: fig.pool || t('settings.api.stage.anyEnvironment'), more: fig.targets ? t('settings.api.stage.targets', { n: fig.targets }) : '' },
        { id: 'endpoints', tone: 'green', icon: <Layers size={15} />, title: t('settings.api.tab.endpoints'),
          what: t('settings.connectors.fig.endpoints', { n: fig.endpoints }), more: fig.writable ? t('settings.api.stage.writable', { n: fig.writable }) : '' },
        { id: 'webhooks', tone: 'orange', icon: <Bell size={15} />, title: t('settings.api.tab.webhooks'), what: t('settings.api.stage.notYet') },
        { id: 'test', tone: 'orange', icon: <Play size={15} />, title: t('settings.api.tab.test'), what: t('settings.api.stage.tryIt') },
      ]} />
      {tab === 'connection' && <ConnectionTab value={value} patch={patch} hasMenu={hasMenu} />}
      {tab === 'auth' && <AuthTab value={value} patch={patch} />}
      {tab === 'environments' && <ApiEnvironmentsTab value={value} patch={patch} sqlConnectors={sqlConnectors ?? []} />}
      {tab === 'endpoints' && <EndpointsTab connectorName={name} value={value} patch={patch} openGenerator={openGenerator} />}
      {tab === 'webhooks' && <WebhooksTab />}
      {tab === 'test' && <TestTab connectorName={name} value={value} />}
    </Stack>
  )
}

// ── Connection tab ─────────────────────────────────────────────────────────────────────────
function ConnectionTab({
  value, patch, hasMenu,
}: { value: ApiConnector; patch: (p: Partial<ApiConnector>) => void; hasMenu?: boolean }) {
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
      <SectionHead>{t('settings.api.app.section')}</SectionHead>
      <FieldRow>
        <label>{t('settings.api.app.showInSwitcher')}</label>
        {/* Effective state, not stored state. A connector only shows in the switcher when it has
            BOTH show_in_switcher != false AND a menu in menus.toml. Without a menu the runtime
            filters it out regardless, so the checkbox would lie if it reflected the stored flag
            alone. When there's no menu we show unchecked + disabled — operator can't toggle a
            no-op. The stored value is preserved (we never patch on the disabled click). */}
        <Checkbox
          checked={value.show_in_switcher !== false && hasMenu !== false}
          disabled={hasMenu === false}
          onChange={(v: boolean) => patch({ show_in_switcher: v ? undefined : false })}
          label={hasMenu === false
            ? 'disabled (no menu)'
            : value.show_in_switcher !== false ? 'enabled' : 'disabled'}
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.app.home')}</label>
        <SearchSelect
          value={value.home ?? ''}
          options={homeOpts}
          onChange={(v) => patch({ home: v || null })}
          placeholder={t('common.pick')}
        />
      </FieldRow>
      <SectionHead>{t('settings.api.connection.section')}</SectionHead>
      <FieldRow>
        <label>{t('settings.api.connection.baseUrl')}</label>
        <Input
          value={value.base_url ?? ''}
          onChange={(e) => patch({ base_url: e.target.value || undefined })}
          placeholder="https://api.example.com"
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.connection.timeout')}</label>
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
        <label>{t('settings.api.connection.sslVerify')}</label>
        {/* On / off, or an environment reference typed in (``${VAR:-true}``) — a per-install
            choice the shipped config leaves to the deployment; the reference is kept as written. */}
        <SearchSelect
          allowCustom
          value={typeof value.verify_ssl === 'string' ? value.verify_ssl : value.verify_ssl === false ? 'false' : 'true'}
          options={[
            { value: 'true', label: t('settings.api.connection.sslOn') },
            { value: 'false', label: t('settings.api.connection.sslOff') },
            ...(typeof value.verify_ssl === 'string' ? [{ value: value.verify_ssl, label: value.verify_ssl, mono: value.verify_ssl }] : []),
          ]}
          onChange={(v) => patch({ verify_ssl: v === 'false' ? false : v === 'true' || v === '' ? undefined : v })}
        />
      </FieldRow>
      <SectionHead>{t('settings.api.connection.defaultHeaders')}</SectionHead>
      <FieldRowTop>
        <label>{t('settings.api.connection.headers')}</label>
        <div>
          <Textarea
            value={headersText}
            onChange={(e) => {
              setHeadersText(e.target.value)
              patch({ default_headers: headersFromString(e.target.value) })
            }}
            placeholder="Content-Type:application/json"
          />
          <Hint>{t('settings.api.connection.headersHint')}</Hint>
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
      <SectionHead>{t('settings.api.auth.section')}</SectionHead>
      <FieldRow>
        <label>{t('settings.api.auth.method')}</label>
        <SearchSelect
          value={authType}
          options={[
            { value: 'none', label: t('settings.api.auth.methodNone') },
            { value: 'basic', label: t('settings.api.auth.methodBasic') },
            { value: 'bearer', label: t('settings.api.auth.methodBearer') },
            { value: 'api_key', label: t('settings.api.auth.methodApiKey') },
            { value: 'oauth2', label: t('settings.api.auth.methodOauth2') },
          ]}
          onChange={(v) => patch({ auth_type: (v as ApiConnector['auth_type']) ?? 'none' })}
        />
      </FieldRow>

      {showCredentials && (
        <>
          <SectionHead>{t('settings.api.auth.credentials')}</SectionHead>
          <FieldRow>
            <label>{t('settings.api.auth.username')}</label>
            <Input
              value={value.auth_username ?? ''}
              onChange={(e) => patch({ auth_username: e.target.value || undefined })}
              placeholder={t('settings.api.auth.usernamePlaceholder')}
            />
          </FieldRow>
          <FieldRow>
            <label>{t('settings.api.auth.password')}</label>
            <Input
              type="password"
              value={value.auth_password ?? ''}
              onChange={(e) => patch({ auth_password: e.target.value || undefined })}
              placeholder={t('settings.api.auth.passwordPlaceholder')}
            />
          </FieldRow>
        </>
      )}

      {showToken && (
        <>
          <SectionHead>{t('settings.api.auth.token')}</SectionHead>
          <FieldRow>
            <label>{t('settings.api.auth.tokenLabel')}</label>
            <Input
              type="password"
              value={value.auth_token ?? ''}
              onChange={(e) => patch({ auth_token: e.target.value || undefined })}
              placeholder={t('settings.api.auth.tokenPlaceholder')}
            />
          </FieldRow>
        </>
      )}

      {showApiKeyHeader && (
        <FieldRow>
          <label>{t('settings.api.auth.apiKeyHeader')}</label>
          <Input
            value={value.auth_api_key_header ?? 'X-Api-Key'}
            onChange={(e) => patch({ auth_api_key_header: e.target.value || undefined })}
            placeholder="X-Api-Key"
          />
        </FieldRow>
      )}

      {showTokenEndpoint && (
        <>
          <SectionHead>{t('settings.api.auth.tokenEndpointSection')}</SectionHead>
          <FieldRow>
            <label>{t('settings.api.auth.tokenEndpoint')}</label>
            <Input
              value={value.auth_token_endpoint ?? ''}
              onChange={(e) => patch({ auth_token_endpoint: e.target.value || undefined })}
              placeholder="/v2/tokenrequest"
            />
          </FieldRow>
          <FieldRowTop>
            <label>{t('settings.api.auth.tokenField')}</label>
            <div>
              <Input
                value={value.auth_token_field ?? ''}
                onChange={(e) => patch({ auth_token_field: e.target.value || undefined })}
                placeholder="access_token"
              />
              <Hint>{t('settings.api.auth.tokenFieldHint')}</Hint>
            </div>
          </FieldRowTop>
          <FieldRowTop>
            <label>{t('settings.api.auth.tokenTtl')}</label>
            <div>
              <Input
                type="number"
                value={String(ttlMin)}
                onChange={(e) => {
                  const m = Number(e.target.value)
                  patch({ auth_token_ttl: Number.isFinite(m) && m > 0 ? m * 60 : undefined })
                }}
              />
              <Hint>{t('settings.api.auth.tokenTtlHint')}</Hint>
            </div>
          </FieldRowTop>
          <FieldRow>
            <label>{t('settings.api.auth.tokenContentType')}</label>
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
            <label>{t('settings.api.auth.tokenBody')}</label>
            <div>
              <Textarea
                value={value.auth_token_body ?? ''}
                onChange={(e) => patch({ auth_token_body: e.target.value || undefined })}
                placeholder='{"deviceName":"ais"}'
              />
              <Hint>{t('settings.api.auth.tokenBodyHint')}</Hint>
            </div>
          </FieldRowTop>
          <FieldRowTop>
            <label>{t('settings.api.auth.tokenHeaders')}</label>
            <div>
              <Textarea
                value={tokenHeadersText}
                onChange={(e) => {
                  setTokenHeadersText(e.target.value)
                  patch({ auth_token_headers: headersFromString(e.target.value) })
                }}
                placeholder="customer-id:CUST123;X-Tenant:acme"
              />
              <Hint>{t('settings.api.auth.tokenHeadersHint')}</Hint>
            </div>
          </FieldRowTop>
        </>
      )}
    </Stack>
  )
}

// ── Endpoints tab ──────────────────────────────────────────────────────────────────────────
function EndpointsTab({
  connectorName, value, patch, openGenerator,
}: { connectorName: string; value: ApiConnector; patch: (p: Partial<ApiConnector>) => void; openGenerator?: { generator?: string; query?: string; n: number } | null }) {
  const { t } = useTranslation()
  const modals = useModals()
  const endpoints = useMemo(() => value.endpoints ?? [], [value.endpoints])
  const setEndpoints = (next: Endpoint[]) => patch({ endpoints: next.length ? next : undefined })
  // Collapsed by default — large lists are easier to scan when only headers show. The operator
  // clicks a card to expand it; adding a new endpoint expands that one (see `addEndpoint`).
  const [openIdx, setOpenIdx] = useState<number | null>(null)
  // one endpoint edited over the whole window (many steps, a long body): the same editor on the same draft
  const [wideIdx, setWideIdx] = useState<number | null>(null)
  const openRef = useRef<HTMLDivElement | null>(null)
  useEffect(() => { openRef.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [openIdx])
  // Shown under their group's heading (the ungrouped ones first), in alphabetical order, narrowed by
  // the search box; each row keeps its place in the saved list (`i`), which is what the edits address.
  const [search, setSearch] = useState('')
  const [folded, setFolded] = useState<Set<string>>(new Set())
  const shown = useMemo(() => {
    const q = search.trim().toLowerCase()
    const by = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' })
    return endpoints
      .map((ep, i) => ({ ep, i, group: (ep.group ?? '').trim() }))
      .filter(({ ep }) => !q || [ep.name, ep.label, ep.path, ep.method, ep.description, ep.group].some((x) => (x ?? '').toLowerCase().includes(q)))
      .sort((a, b) => by(a.group, b.group) || by(a.ep.name, b.ep.name))
  }, [endpoints, search])
  const groupSizes = useMemo(() => {
    const sizes = new Map<string, number>()
    for (const s of shown) sizes.set(s.group, (sizes.get(s.group) ?? 0) + 1)
    return sizes
  }, [shown])
  const toggleGroup = (group: string) => setFolded((was) => {
    const next = new Set(was)
    if (next.has(group)) next.delete(group); else next.add(group)
    return next
  })

  const addEndpoint = async () => {
    const name = (await modals.prompt({
      title: t('settings.api.endpoint.add'),
      message: t('settings.api.endpoint.namePrompt'),
      validate: (v) => {
        if (!v) return null
        if (endpoints.some((e) => e.name === v)) return t('settings.api.endpoint.nameExists', { name: v })
        if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(v)) return t('settings.api.endpoint.invalidName')
        return null
      },
    }))?.trim()
    if (!name) return
    setEndpoints([...endpoints, { name, method: 'GET', path: '', content_type: 'application/json' }])
    setSearch('')                      // the new one must not be hidden by a search
    setOpenIdx(endpoints.length)
  }
  const removeEndpoint = async (idx: number) => {
    const ep = endpoints[idx]
    const ok = await modals.confirm({
      title: t('settings.api.endpoint.delete'),
      message: t('settings.api.endpoint.confirmDelete', { name: ep.name }),
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
  // "Create from…": an endpoint a generator of the connector built from the server's own
  // description. Added unsaved and opened, under a free name, for the operator to review.
  const generators = value.generators ?? []
  const [generating, setGenerating] = useState(!!openGenerator)
  const askedN = openGenerator?.n
  useEffect(() => { if (askedN) setGenerating(true) }, [askedN])
  const [showGenerators, setShowGenerators] = useState(false)
  const addGenerated = (raws: Record<string, unknown>[], saved: boolean, replaced = false) => {
    const next = [...endpoints]
    for (const raw of raws) {
      const ep = raw as unknown as Endpoint
      let name = ep.name || 'endpoint'
      // a screen's endpoints were saved with it under their own names: one the list already shows is
      // that one — kept as it was, or written again when the operator chose to replace it
      const at = saved ? next.findIndex((e) => e.name === name) : -1
      if (at >= 0) { if (replaced) next[at] = ep; continue }
      for (let n = 2; next.some((e) => e.name === name); n++) name = `${ep.name}_${n}`
      next.push({ ...ep, name })
    }
    setEndpoints(next)
    setSearch('')
    setOpenIdx(next.length > endpoints.length ? endpoints.length : null)
    setGenerating(false)
  }
  const setGenerators = (next: EndpointGeneratorDef[]) => patch({ generators: next.length ? next : undefined })
  // "Refresh from the server": a generated endpoint is built again from what it was created from
  // and merged into the operator's (services/endpointRefresh). Nothing is saved: the list changes,
  // the builder's Save writes it.
  const [checking, setChecking] = useState(false)
  const rebuild = async (ep: Endpoint): Promise<GeneratedEndpoint> => {
    const r = await api.post<{ endpoint: GeneratedEndpoint }>('/admin/config/api/generate', {
      connector: connectorName, generator: ep.source?.generator, step: 'build', candidate: ep.source?.candidate, options: ep.source?.options ?? {},
    })
    return r.endpoint
  }
  const changeList = (changes: string[]) => <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>{changes.map((c) => <li key={c}>{c}</li>)}</ul>
  const refreshEndpoint = async (idx: number) => {
    const ep = endpoints[idx]
    if (!ep?.source) return
    setChecking(true)
    try {
      const { next, changes } = mergeGenerated(ep as unknown as GeneratedEndpoint, await rebuild(ep))
      if (!changes.length) {
        await modals.alert({ title: t('settings.api.refresh.title', { name: ep.name }), message: t('settings.api.refresh.upToDate') })
        return
      }
      const ok = await modals.confirm({
        title: t('settings.api.refresh.title', { name: ep.name }),
        message: <>{t('settings.api.refresh.changes')}{changeList(changes)}</>,
        confirmLabel: t('settings.api.refresh.apply'),
      })
      if (ok) updateEndpoint(idx, next as unknown as Endpoint)
    } catch (e) {
      await modals.alert({ title: t('settings.api.refresh.title', { name: ep.name }), message: e instanceof ApiError ? e.message : String(e), variant: 'danger' })
    } finally {
      setChecking(false)
    }
  }
  const generated = endpoints.filter((e) => e.source)
  const checkGenerated = async () => {
    setChecking(true)
    const lines: { name: string; text: string }[] = []
    try {
      for (const ep of generated) {
        try {
          const { changes } = mergeGenerated(ep as unknown as GeneratedEndpoint, await rebuild(ep))
          if (changes.length) lines.push({ name: ep.name, text: changes.join(' · ') })
        } catch (e) {
          lines.push({ name: ep.name, text: e instanceof ApiError ? e.message : String(e) })
        }
      }
    } finally {
      setChecking(false)
    }
    await modals.alert({
      title: t('settings.api.refresh.checkTitle'),
      message: lines.length === 0
        ? t('settings.api.refresh.allUpToDate', { count: generated.length })
        : <>{t('settings.api.refresh.someChanged', { count: lines.length })}
            <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>{lines.map((l) => <li key={l.name}><b>{l.name}</b> — {l.text}</li>)}</ul></>,
    })
  }
  return (
    <Stack gap={6}>
      {endpoints.length === 0 && (
        <Hint>{t('settings.api.endpoint.empty')}</Hint>
      )}
      <Row gap={8}>
        <Button $variant="ghost" $size="sm" onClick={addEndpoint}>
          <Plus size={13} /> {t('settings.api.endpoint.add')}
        </Button>
        {generators.length > 0 && (
          <Button $variant="ghost" $size="sm" onClick={() => setGenerating(true)} title={t('settings.api.generate.hint')}>
            <Wand2 size={13} /> {t('settings.api.generate.button')}
          </Button>
        )}
        {generated.length > 0 && (
          <Button $variant="ghost" $size="sm" onClick={() => void checkGenerated()} disabled={checking} title={t('settings.api.refresh.checkHint')}>
            <RefreshCw size={13} /> {t('settings.api.refresh.check', { count: generated.length })}
          </Button>
        )}
        <Button $variant="ghost" $size="sm" onClick={() => setShowGenerators((v) => !v)}>
          {showGenerators ? <ChevronDown size={13} /> : <ChevronRight size={13} />} {t('settings.api.generate.declared', { count: generators.length })}
        </Button>
      </Row>
      {showGenerators && (
        <Stack gap={4}>
          <Hint>{t('settings.api.generate.declaredHint')}</Hint>
          {generators.map((g, i) => (
            <GeneratorRow key={i}>
              <Input value={g.id} placeholder={t('settings.api.generate.id')}
                onChange={(e) => setGenerators(generators.map((x, j) => (j === i ? { ...x, id: e.target.value } : x)))} />
              <Input value={g.label} placeholder={t('settings.api.generate.label')}
                onChange={(e) => setGenerators(generators.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))} />
              <Input value={g.callable} placeholder="app.module:j_function"
                onChange={(e) => setGenerators(generators.map((x, j) => (j === i ? { ...x, callable: e.target.value } : x)))} />
              <Input value={g.description ?? ''} placeholder={t('settings.api.generate.description')}
                onChange={(e) => setGenerators(generators.map((x, j) => (j === i ? { ...x, description: e.target.value || undefined } : x)))} />
              <Button $variant="ghost" $size="sm" onClick={() => setGenerators(generators.filter((_, j) => j !== i))} title={t('common.remove')}>
                <Trash2 size={13} />
              </Button>
            </GeneratorRow>
          ))}
          <Button $variant="ghost" $size="sm" style={{ alignSelf: 'flex-start' }}
            onClick={() => setGenerators([...generators, { id: '', label: '', callable: '' }])}>
            <Plus size={13} /> {t('settings.api.generate.add')}
          </Button>
        </Stack>
      )}
      {endpoints.length > 5 && (
        <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder={t('table.search')} />
      )}
      {endpoints.length > 0 && shown.length === 0 && <Hint>{t('settings.api.endpoint.noMatch')}</Hint>}
      {shown.map(({ ep, i, group }, at) => {
        const open = openIdx === i
        // a heading before the first endpoint of each group; a search opens every group
        const heading = group && (at === 0 || shown[at - 1].group !== group)
        const hidden = group !== '' && folded.has(group) && !search.trim() && !open
        return (
          <Fragment key={`${ep.name}-${i}`}>
          {heading && (
            <GroupHead type="button" onClick={() => toggleGroup(group)}>
              {folded.has(group) && !search.trim() ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
              <span>{group}</span>
              <span className="count">{groupSizes.get(group)}</span>
            </GroupHead>
          )}
          {!hidden && (
          <EndpointCard ref={open ? openRef : undefined}>
            <EndpointHead $open={open} onClick={() => setOpenIdx(open ? null : i)} type="button">
              {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              {/* served by a plugin function: no HTTP method */}
              <MethodPill $tone={ep.callable || ep.action ? 'POST' : (ep.method ?? 'GET')}>{ep.action ? 'ACT' : ep.callable ? 'FN' : (ep.method ?? 'GET')}</MethodPill>
              <span className="name">{ep.name || '(unnamed)'}</span>
              {ep.label && <span style={{ color: colors.text.muted, fontFamily: fonts.sans }}>· {ep.label}</span>}
              {ep.writable && <WritesTag title={t('settings.api.endpoint.writableHint')}>{t('settings.api.endpoint.writableTag')}</WritesTag>}
              <span className="grow" />
              <Maximize2 size={13} data-endpoint-wide={ep.name} style={{ color: colors.text.muted }} onClick={(e) => { e.stopPropagation(); setWideIdx(i) }}><title>{t('settings.api.endpoint.wide')}</title></Maximize2>
              <Trash2 size={13} style={{ color: colors.text.muted }} onClick={(e) => { e.stopPropagation(); void removeEndpoint(i) }} />
            </EndpointHead>
            {open && wideIdx !== i && (
              <EndpointBody>
                <EndpointEditor
                  value={ep} onChange={(v) => updateEndpoint(i, v)}
                  sourceLabel={ep.source ? `${generators.find((g) => g.id === ep.source?.generator)?.label ?? ep.source.generator} — ${ep.source.candidate}` : undefined}
                  onRefresh={ep.source ? () => void refreshEndpoint(i) : undefined}
                  refreshing={checking}
                />
              </EndpointBody>
            )}
          </EndpointCard>
          )}
          </Fragment>
        )
      })}
      {wideIdx !== null && endpoints[wideIdx] && (() => {
        const ep = endpoints[wideIdx]
        const close = () => setWideIdx(null)
        return (
          <EditorModalShell variant="visual" defaultFullscreen onClose={close}
            title={<>{t('studio.endpoint')} · <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontSize: fontSize.sm, fontWeight: 400 }}>{connectorName}.{ep.name}</span></>}
            footer={<><span style={{ flex: 1, color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.api.endpoint.wideNote')}</span><Button $size="sm" $variant="primary" onClick={close}>{t('common.close')}</Button></>}>
            <EndpointEditor
              value={ep} onChange={(v) => updateEndpoint(wideIdx, v)}
              sourceLabel={ep.source ? `${generators.find((g) => g.id === ep.source?.generator)?.label ?? ep.source.generator} — ${ep.source.candidate}` : undefined}
              onRefresh={ep.source ? () => void refreshEndpoint(wideIdx) : undefined}
              refreshing={checking}
            />
          </EditorModalShell>
        )
      })()}
      {generating && (
        <EndpointGeneratorModal key={askedN ?? 0} connector={connectorName} generators={generators} existingNames={endpoints.map((e) => e.name)} onCreate={addGenerated} onClose={() => setGenerating(false)}
          initial={openGenerator ?? undefined} />
      )}
    </Stack>
  )
}

// ── per-endpoint editor ────────────────────────────────────────────────────────────────────
// The column → record key map of an endpoint a screen reads, typed as ``AN8=z_AN8_19, ALPH=z_ALPH_20``.
// Kept as text while it is typed (a half-written pair must not vanish), parsed on every change.
function RowMapInput({ value, onChange, hint }: { value?: Record<string, string>; onChange: (m: Record<string, string> | undefined) => void; hint: string }) {
  const asText = (m?: Record<string, string>) => Object.entries(m ?? {}).map(([k, v]) => `${k}=${v}`).join(', ')
  const [text, setText] = useState(asText(value))
  const shown = asText(value)
  useEffect(() => { setText((cur) => (asText(parse(cur)) === shown ? cur : shown)) }, [shown])
  return (
    <Input
      value={text}
      onChange={(e) => { setText(e.target.value); onChange(parse(e.target.value)) }}
      placeholder="ID=id, NAME=name"
      title={hint}
    />
  )
}
function parse(text: string): Record<string, string> | undefined {
  const out: Record<string, string> = {}
  for (const part of text.split(',')) {
    const eq = part.indexOf('=')
    const k = (eq < 0 ? '' : part.slice(0, eq)).trim()
    const v = (eq < 0 ? '' : part.slice(eq + 1)).trim()
    if (k && v) out[k] = v
  }
  return Object.keys(out).length ? out : undefined
}

type Stage = 'general' | 'inputs' | 'call' | 'answer'
// a parameter: a line that says what it is, opened to edit its labelled fields
const ParamCard = styled.div<{ $open: boolean }>`
  border: 1px solid ${(p) => (p.$open ? colors.blue.border : colors.border)}; border-radius: ${radius.md}; margin-bottom: 6px; background: var(--bg-modal);
`
const ParamHead = styled.button`
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 8px 10px; background: transparent; border: none; cursor: pointer; text-align: left;
  color: ${colors.text.muted}; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  & .name { font-family: ${fonts.mono}; font-weight: 600; color: ${colors.text.primary}; }
  & .label { color: ${colors.text.secondary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .grow { flex: 1; }
  & .meta { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; }
  & .req { font-size: ${fontSize.micro}; font-weight: 600; color: ${colors.orange.main}; background: ${colors.orange.bg}; border: 1px solid ${colors.orange.border}; border-radius: 999px; padding: 1px 8px; }
  & .del { display: inline-flex; padding: 2px; }
  & .del:hover { color: ${colors.red.main}; }
`
const ParamBody = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 2px 12px; padding: 4px 12px 12px; border-top: 1px solid ${colors.border};`
const Part = styled.div<{ $on: boolean }>`display: ${(p) => (p.$on ? 'flex' : 'none')}; flex-direction: column; gap: 4px;`
// the endpoint as a workspace, like a service: its flow on the left, the picked stage's fields beside it
const EpSplit = styled.div`display: flex; gap: 14px; height: min(700px, 74vh); min-height: 460px;`
const EpSide = styled.div`
  flex: 0 0 min(640px, 58%); min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding: 14px 16px;
  border: 1px solid ${colors.border}; border-radius: ${radius.lg};
`
const StageWire = styled.div`
  flex: 0 0 26px; align-self: center; height: 2px; background: ${colors.border}; position: relative;
  &::after { content: ''; position: absolute; right: 0; top: -3px; border-left: 6px solid ${colors.border}; border-top: 4px solid transparent; border-bottom: 4px solid transparent; }
`
const StageCard = styled.button<{ $tone: 'blue' | 'purple' | 'orange' | 'green'; $on: boolean }>`
  flex: 0 0 auto; width: 210px; text-align: left; cursor: pointer; padding: 9px 11px; display: flex; flex-direction: column; gap: 4px;
  font-family: ${fonts.sans}; background: ${(p) => (p.$on ? colors[p.$tone].bg : 'var(--bg-modal)')}; border-radius: ${radius.lg};
  border: 1px solid ${(p) => (p.$on ? colors[p.$tone].main : colors.border)}; transition: border-color 0.15s;
  &:hover { border-color: ${(p) => colors[p.$tone].border}; }
  & .kind { display: flex; align-items: center; gap: 6px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: ${(p) => colors[p.$tone].main}; }
  & .chip { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: ${radius.md}; background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border}; }
  & .what { font-size: ${fontSize.sm}; font-family: ${fonts.mono}; color: ${colors.text.secondary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .item { font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`

export function EndpointEditor({ value, onChange, sourceLabel, onRefresh, refreshing }: {
  value: Endpoint
  onChange: (v: Endpoint) => void
  /** A generated endpoint: what it was created from, and the refresh from the server. */
  sourceLabel?: string
  onRefresh?: () => void
  refreshing?: boolean
}) {
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
  // the plugin functions an endpoint can be served by (the same catalogue as a call_plugin action)
  const [callables, setCallables] = useState<Array<{ callable: string; docstring: string | null }>>([])
  const { sharedActions } = useWorkspace()
  const actionIds = useMemo(() => Object.keys(sharedActions ?? {}).sort(), [sharedActions])
  useEffect(() => {
    api.get<{ callables: Array<{ callable: string; docstring: string | null }> }>('/api/plugins/callables')
      .then((r) => setCallables(r.callables)).catch(() => setCallables([]))
  }, [])
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
  const [stage, setStage] = useState<Stage>('general')
  const [openParam, setOpenParam] = useState<number | null>(null)
  const setParams = (next: NonNullable<Endpoint['params']>) =>
    set({ params: next.length ? next : undefined })
  const respMap = value.response_map ?? {}
  const respMapEntries = Object.entries(respMap)

  // the endpoint as a small flow: what it is → what it is given → the call → what comes back. A stage's
  // fields show when it is picked; each stage says in a line what it holds.
  const served = value.action ? t('settings.api.stage.byAction', { name: value.action }) : value.callable ? t('settings.api.stage.byFunction', { name: value.callable.split(':').pop() })
    : `${value.method ?? 'GET'} ${value.path ?? ''}`
  const columns = Object.keys(value.row_map ?? {}).length
  const answers = value.rows_field
    ? t('settings.api.stage.rows', { where: value.rows_field }) + (columns ? ` · ${t('settings.api.stage.columns', { n: columns })}` : '')
    : value.response_field ? value.response_field : t('settings.api.stage.whole')
  const stages: { id: Stage; tone: 'blue' | 'purple' | 'orange' | 'green'; icon: ReactNode; what: string }[] = [
    { id: 'general', tone: 'blue', icon: <Tag size={15} />, what: value.label || value.name || '' },
    { id: 'inputs', tone: 'purple', icon: <LogIn size={15} />, what: t('settings.api.stage.params', { n: params.length }) },
    { id: 'call', tone: 'orange', icon: <Send size={15} />, what: served },
    { id: 'answer', tone: 'green', icon: <LogOut size={15} />, what: answers },
  ]

  return (
    <EpSplit>
      <FlowCanvas zoomKey="endpoint">
        <FlowRows>
          {stages.map((st, i) => (
            <Fragment key={st.id}>
              {i > 0 && <StageWire />}
              <StageCard type="button" $tone={st.tone} $on={stage === st.id} onClick={() => setStage(st.id)}>
                <span className="kind"><span className="chip">{st.icon}</span>{t(`settings.api.stage.${st.id}`)}</span>
                <span className="what">{st.what}</span>
              </StageCard>
            </Fragment>
          ))}
        </FlowRows>
      </FlowCanvas>
      <EpSide>
      <Part $on={stage === 'general'}>
      {sourceLabel && (
        <FieldRow>
          <label>{t('settings.api.refresh.source')}</label>
          <Row gap={10}>
            <span style={{ color: colors.text.secondary, fontSize: fontSize.sm }}>{sourceLabel}</span>
            <Button $variant="ghost" $size="sm" onClick={onRefresh} disabled={refreshing} title={t('settings.api.refresh.hint')}>
              <RefreshCw size={13} /> {t('settings.api.refresh.button')}
            </Button>
          </Row>
        </FieldRow>
      )}
      <FieldRow>
        <label>{t('settings.api.endpoint.name')}</label>
        <Row gap={10}>
          <Input value={value.name} onChange={(e) => set({ name: e.target.value })} />
          <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.api.endpoint.label')}</span>
          <Input
            value={value.label ?? ''}
            onChange={(e) => set({ label: e.target.value || undefined })}
            placeholder={t('settings.api.endpoint.labelPlaceholder')}
          />
        </Row>
      </FieldRow>
      </Part>
      <Part $on={stage === 'call'}>
      <FieldRow>
        <label>{t('settings.api.endpoint.method')}</label>
        <Row gap={10}>
          <div style={{ width: 160 }}>
            <SearchSelect
              value={value.method ?? 'GET'}
              options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'].map((m) => ({ value: m, label: m }))}
              onChange={(v) => set({ method: v ?? 'GET' })}
            />
          </div>
          <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.api.endpoint.urlPath')}</span>
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
        <label title={t('settings.api.endpoint.callableHint')}>{t('settings.api.endpoint.callable')}</label>
        <SearchSelect
          value={value.callable ?? ''}
          onChange={(v) => set({ callable: v || undefined })}
          options={[{ value: '', label: t('settings.api.endpoint.callableNone') },
            ...callables.map((c) => ({ value: c.callable, label: c.callable })),
            ...(value.callable && !callables.some((c) => c.callable === value.callable) ? [{ value: value.callable, label: value.callable }] : [])]}
        />
      </FieldRow>
      {value.callable && <Banner $tone="info">{t('settings.api.endpoint.callableHint')}</Banner>}
      {/* …or by a shared action: steps composed in the action designer, exposed as this endpoint */}
      {!value.callable && (
        <FieldRow>
          <label title={t('settings.api.endpoint.actionHint')}>{t('settings.api.endpoint.action')}</label>
          <SearchSelect
            value={value.action ?? ''}
            onChange={(v) => set({ action: v || undefined })}
            options={[{ value: '', label: t('settings.api.endpoint.callableNone') },
              ...actionIds.map((id) => ({ value: id, label: sharedActions?.[id]?.label ? `${id} — ${sharedActions[id].label}` : id })),
              ...(value.action && !actionIds.includes(value.action) ? [{ value: value.action, label: value.action }] : [])]}
          />
        </FieldRow>
      )}
      {value.action && <Banner $tone="info">{t('settings.api.endpoint.actionHint')}</Banner>}
      <FieldRow>
        <label>{t('settings.api.endpoint.headers')}</label>
        <Input
          value={headersText}
          onChange={(e) => {
            setHeadersText(e.target.value)
            set({ headers: headersFromString(e.target.value) })
          }}
          placeholder="X-Custom:value;Authorization:Bearer {{token}}"
        />
      </FieldRow>
      </Part>
      <Part $on={stage === 'general'}>
      <FieldRow>
        <label>{t('settings.api.endpoint.writable')}</label>
        <Checkbox
          checked={value.writable === true}
          onChange={(v: boolean) => set({ writable: v ? true : undefined })}
          label={t('settings.api.endpoint.writableHint')}
        />
      </FieldRow>
      </Part>
      <Part $on={stage === 'call'}>
      <FieldRow>
        <label>{t('settings.api.endpoint.contentType')}</label>
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
        <label>{t('settings.api.endpoint.body')}</label>
        <div>
          <Textarea
            style={{ minHeight: 160 }}
            value={value.body ?? ''}
            onChange={(e) => set({ body: e.target.value || undefined })}
            placeholder='{"reportName":"{{reportName}}"}'
          />
          <Row gap={10} style={{ marginTop: 4 }}>
            <Button $variant="ghost" $size="sm" onClick={formatBody}>{t('settings.api.endpoint.formatJson')}</Button>
            <Hint>{t('settings.api.endpoint.bodyHint')}</Hint>
          </Row>
        </div>
      </FieldRowTop>
      <FieldRow>
        <label>{t('settings.api.endpoint.queryParams')}</label>
        <Input
          value={queryText}
          onChange={(e) => {
            setQueryText(e.target.value)
            set({ query_params: queryFromString(e.target.value) })
          }}
          placeholder="pageSize={{pageSize}}&page={{page}}"
        />
      </FieldRow>
      {Object.keys(value.query_params ?? {}).length > 0 && (
        <FieldRow>
          <span />
          <Checkbox
            checked={value.skip_blank_query === true}
            onChange={(v: boolean) => set({ skip_blank_query: v ? true : undefined })}
            label={t('settings.api.endpoint.skipBlankQuery')}
          />
        </FieldRow>
      )}
      </Part>
      <Part $on={stage === 'answer'}>
      <FieldRow>
        <label title={t('settings.api.endpoint.rowsFieldHint')}>{t('settings.api.endpoint.rowsField')}</label>
        <Input
          value={value.rows_field ?? ''}
          onChange={(e) => set({ rows_field: e.target.value || undefined })}
          placeholder="data.items"
          title={t('settings.api.endpoint.rowsFieldHint')}
        />
      </FieldRow>
      {value.rows_field && (
        <>
          <FieldRow>
            <label title={t('settings.api.endpoint.rowMapHint')}>{t('settings.api.endpoint.rowMap')}</label>
            <RowMapInput value={value.row_map ?? undefined} onChange={(m) => set({ row_map: m })} hint={t('settings.api.endpoint.rowMapHint')} />
          </FieldRow>
          <FieldRow>
            <label title={t('settings.api.endpoint.labelsFieldHint')}>{t('settings.api.endpoint.labelsField')}</label>
            <Input
              value={value.labels_field ?? ''}
              onChange={(e) => set({ labels_field: e.target.value || undefined })}
              placeholder="data.columns"
              title={t('settings.api.endpoint.labelsFieldHint')}
            />
          </FieldRow>
          <FieldRow>
            <label title={t('settings.api.endpoint.limitParamHint')}>{t('settings.api.endpoint.limitParam')}</label>
            <SearchSelect
              value={value.limit_param ?? ''}
              onChange={(v) => set({ limit_param: v || undefined })}
              options={[{ value: '', label: t('settings.api.endpoint.limitParamNone') }, ...(value.params ?? []).map((p) => ({ value: p.name, label: p.label ? `${p.name} — ${p.label}` : p.name }))]}
            />
          </FieldRow>
          <FieldRow>
            <label title={t('settings.api.endpoint.moreFieldHint')}>{t('settings.api.endpoint.moreField')}</label>
            <Input
              value={value.more_field ?? ''}
              onChange={(e) => set({ more_field: e.target.value || undefined })}
              placeholder="data.hasMore"
              title={t('settings.api.endpoint.moreFieldHint')}
            />
          </FieldRow>
        </>
      )}
      <FieldRow>
        <label title={t('settings.api.endpoint.errorFieldsHint')}>{t('settings.api.endpoint.errorFields')}</label>
        <Input
          value={(value.error_fields ?? []).join(', ')}
          onChange={(e) => {
            const list = e.target.value.split(',').map((x) => x.trim())
            set({ error_fields: list.some((x) => x) ? list.filter((x, k) => x || k === list.length - 1) : undefined })
          }}
          onBlur={() => {
            const clean = (value.error_fields ?? []).filter((x) => x)
            if (clean.length !== (value.error_fields ?? []).length) set({ error_fields: clean.length ? clean : undefined })
          }}
          placeholder="errors, result.messages"
          title={t('settings.api.endpoint.errorFieldsHint')}
        />
      </FieldRow>
      <FieldRow>
        <label title={t('settings.api.endpoint.warningFieldsHint')}>{t('settings.api.endpoint.warningFields')}</label>
        <Input
          value={(value.warning_fields ?? []).join(', ')}
          onChange={(e) => {
            const list = e.target.value.split(',').map((x) => x.trim())
            set({ warning_fields: list.some((x) => x) ? list.filter((x, k) => x || k === list.length - 1) : undefined })
          }}
          onBlur={() => {
            const clean = (value.warning_fields ?? []).filter((x) => x)
            if (clean.length !== (value.warning_fields ?? []).length) set({ warning_fields: clean.length ? clean : undefined })
          }}
          placeholder="warnings, result.errors[errorLevel=2]"
          title={t('settings.api.endpoint.warningFieldsHint')}
        />
      </FieldRow>
      <FieldRow>
        <label>{t('settings.api.endpoint.responseField')}</label>
        <Input
          value={value.response_field ?? ''}
          onChange={(e) => set({ response_field: e.target.value || undefined })}
          placeholder="data.items"
        />
      </FieldRow>
      </Part>
      <Part $on={stage === 'general'}>
      <FieldRow>
        <label>{t('settings.api.endpoint.description')}</label>
        <Input
          value={value.description ?? ''}
          onChange={(e) => set({ description: e.target.value || undefined })}
          placeholder={t('settings.api.endpoint.descriptionPlaceholder')}
        />
      </FieldRow>
      <FieldRow>
        <label title={t('settings.api.endpoint.groupHint')}>{t('settings.api.endpoint.group')}</label>
        <Input
          value={value.group ?? ''}
          onChange={(e) => set({ group: e.target.value || undefined })}
          placeholder={t('settings.api.endpoint.groupPlaceholder')}
          title={t('settings.api.endpoint.groupHint')}
        />
      </FieldRow>

      </Part>
      <Part $on={stage === 'answer'}>
      <SectionHead>
        {t('settings.api.endpoint.responseMappings')}
        {' — '}
        <span style={{ textTransform: 'none', letterSpacing: 0 }}>
          {t('settings.api.endpoint.responseMappingsHint')}
        </span>
      </SectionHead>
      {respMapEntries.length === 0 && (
        <Hint>{t('settings.api.endpoint.responseMappingsEmpty')}</Hint>
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
            title={t('common.remove')}
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
        <Plus size={13} /> {t('settings.api.endpoint.addMapping')}
      </Button>

      </Part>
      <Part $on={stage === 'inputs'}>
      <SectionHead>
        {t('settings.api.endpoint.parameters')}
        {' — '}
        <span style={{ textTransform: 'none', letterSpacing: 0 }}>
          {t('settings.api.endpoint.parametersHint')}
        </span>
      </SectionHead>
      {params.length === 0 && (
        <Hint>{t('settings.api.endpoint.parametersEmpty')}</Hint>
      )}
      {params.map((p, i) => {
        const open = openParam === i
        const change = (q: Partial<typeof p>) => { const next = params.slice(); next[i] = { ...p, ...q }; setParams(next) }
        return (
          <ParamCard key={i} $open={open}>
            <ParamHead type="button" onClick={() => setOpenParam(open ? null : i)}>
              {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span className="name">{p.name || '—'}</span>
              {p.label && <span className="label">{p.label}</span>}
              <span className="grow" />
              {p.default != null && p.default !== '' && <span className="meta">= {p.default}</span>}
              {p.required && <span className="req">{t('settings.api.endpoint.paramRequired')}</span>}
              <span className="del" role="button" tabIndex={-1} title={t('common.remove')}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); setParams(params.filter((_, k) => k !== i)); setOpenParam(null) } }}
                onClick={(e) => { e.stopPropagation(); setParams(params.filter((_, k) => k !== i)); setOpenParam(null) }}><Trash2 size={13} /></span>
            </ParamHead>
            {open && (
              <ParamBody>
                <FieldRow><label>{t('settings.api.endpoint.paramName')}</label>
                  <Input value={p.name} placeholder="reportName" onChange={(e) => change({ name: e.target.value })} /></FieldRow>
                <FieldRow><label>{t('settings.api.endpoint.paramLabel')}</label>
                  <Input value={p.label ?? ''} placeholder="Report Name" onChange={(e) => change({ label: e.target.value || undefined })} /></FieldRow>
                <FieldRow><label>{t('settings.api.endpoint.paramDefault')}</label>
                  <Input value={p.default ?? ''} onChange={(e) => change({ default: e.target.value || undefined })} /></FieldRow>
                <FieldRow><label>{t('settings.api.endpoint.paramExample')}</label>
                  <Input value={p.example ?? ''} onChange={(e) => change({ example: e.target.value || undefined })} /></FieldRow>
                <FieldRow style={{ gridColumn: '1 / -1' }}><label>{t('settings.api.endpoint.paramHelp')}</label>
                  <Input value={p.help ?? ''} onChange={(e) => change({ help: e.target.value || undefined })} /></FieldRow>
                <FieldRow style={{ gridColumn: '1 / -1' }}><label>{t('settings.api.endpoint.paramChoices')}</label>
                  <Input
                    value={(p.choices ?? []).join(', ')}
                    onChange={(e) => {
                      const list = e.target.value.split(',').map((x) => x.trim())
                      // keep a trailing blank while the operator is typing the next value; saved without it
                      change({ choices: list.some((x) => x) ? list.filter((x, k) => x || k === list.length - 1) : undefined })
                    }}
                    onBlur={() => {
                      const clean = (p.choices ?? []).filter((x) => x)
                      if (clean.length !== (p.choices ?? []).length) change({ choices: clean.length ? clean : undefined })
                    }}
                  /></FieldRow>
                <Checkbox checked={p.required === true} onChange={(v: boolean) => change({ required: v ? true : undefined })} label={t('settings.api.endpoint.paramRequired')} />
              </ParamBody>
            )}
          </ParamCard>
        )
      })}
      <Button
        $variant="ghost" $size="sm" style={{ alignSelf: 'flex-start' }}
        onClick={() => setParams([...params, { name: '' }])}
      >
        <Plus size={13} /> {t('settings.api.endpoint.addParam')}
      </Button>
      </Part>
      </EpSide>
    </EpSplit>
  )
}

// ── Webhooks tab — placeholder for the inbound-webhook slice ──────────────────────────────
function WebhooksTab() {
  const { t } = useTranslation()
  return (
    <Stack gap={10}>
      <Hint>{t('settings.api.webhooks.coming')}</Hint>
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
      <SectionHead>{t('settings.api.test.section')}</SectionHead>
      {endpoints.length === 0 ? (
        <>
          <Hint>{t('settings.api.test.noEndpoints')}</Hint>
          <Row gap={10} style={{ marginTop: 8 }}>
            <Button $variant="primary" $size="sm" disabled={busy} onClick={runConnectorOnly}>
              <Activity size={13} /> {t('settings.api.test.runConnector')}
            </Button>
            {busy && <Hint>{t('common.loading')}</Hint>}
          </Row>
        </>
      ) : (
        <>
          <FieldRow>
            <label>{t('settings.api.test.endpoint')}</label>
            <SearchSelect
              value={selEndpoint}
              options={[...endpoints].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' })).map((e) => ({
                value: e.name,
                label: e.label ?? e.name,
                mono: e.name,
              }))}
              onChange={(v) => setSelEndpoint(v ?? '')}
            />
          </FieldRow>
          <SectionHead>{t('settings.api.test.parameters')}</SectionHead>
          <Hint>{t('settings.api.test.paramsHint')}</Hint>
          {(ep?.params ?? []).map((p) => (
            <TestParamRow key={p.name}>
              <Input value={p.name} readOnly />
              {p.choices?.length ? (
                <SearchSelect
                  value={paramValues[p.name] ?? ''}
                  options={p.choices.map((c) => ({ value: c, label: c }))}
                  onChange={(v) => setParamValues((prev) => ({ ...prev, [p.name]: v ?? '' }))}
                  placeholder={p.default ?? ''}
                  clearable
                />
              ) : (
                <Input
                  value={paramValues[p.name] ?? ''}
                  onChange={(e) => setParamValues((prev) => ({ ...prev, [p.name]: e.target.value }))}
                  placeholder={p.default ?? p.example ?? ''}
                />
              )}
              {(p.help || p.example) && (
                <ParamHelp>
                  {p.help}{p.help && p.example ? ' — ' : ''}{p.example ? t('settings.api.endpoint.paramExampleIs', { v: p.example }) : ''}
                </ParamHelp>
              )}
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
            <Plus size={13} /> {t('settings.api.test.addParam')}
          </Button>
          <SectionHead>{t('settings.api.test.result')}</SectionHead>
          <Row gap={10}>
            <Button $variant="primary" $size="sm" disabled={busy || !ep} onClick={run}>
              <Activity size={13} /> {t('settings.api.test.run')}
            </Button>
            {busy && <Hint>{t('common.loading')}</Hint>}
          </Row>
        </>
      )}
      {error && <Banner $tone="error">{error}</Banner>}
      {result != null && (
        <ResultBox>{JSON.stringify(result, null, 2)}</ResultBox>
      )}
      {/* Use `connectorName` so it isn't an unused prop; surfaces in the panel header. */}
      <Hint style={{ marginTop: 12 }}>{t('settings.api.test.runsAgainst', { name: connectorName })}</Hint>
    </Stack>
  )
}
