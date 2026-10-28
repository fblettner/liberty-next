// The Developer page's data (C12): fetch + the filters, no React.
import { api } from '../api/client'
import type { ApiReference, ApiRoute, ConfigKey, ConfigReference, DataModel, DataTableRef } from '../types/devref'

export const getConfigReference = () => api.get<ConfigReference>('/admin/dev/config')

/** A key's status: edited in a screen, file-only on purpose, or a gap. */
export function keyStatus(k: ConfigKey): KeyStatus {
  return k.editor ? 'editor' : k.file_only ? 'file' : 'gap'
}

export type KeyStatus = 'editor' | 'file' | 'gap'

/** The keys matching *q* (key, label, description, a reader's file) in *section*, of *status* when given. */
export function filterKeys(keys: ConfigKey[], q: string, section: string, status: KeyStatus | '', label?: (k: ConfigKey) => string): ConfigKey[] {
  const n = q.trim().toLowerCase()
  return keys.filter((k) =>
    (!section || k.section === section)
    && (!status || keyStatus(k) === status)
    && (!n || k.key.toLowerCase().includes(n) || (k.description ?? '').toLowerCase().includes(n)
      || (label ? label(k).toLowerCase().includes(n) : false)
      || k.readers.some((r) => r.file.toLowerCase().includes(n))))
}

/** The modules that read *k*, from its readers' files: ``liberty/jobs/files.py`` → ``jobs``,
 *  ``liberty/main.py`` → ``main`` — in first-seen order, once each. */
export function readerModules(k: ConfigKey): string[] {
  const out: string[] = []
  for (const r of k.readers) {
    const parts = r.file.split('/')
    const mod = parts.length > 2 ? parts[1] : (parts[parts.length - 1] ?? '').replace(/\.py$/, '')
    if (mod && !out.includes(mod)) out.push(mod)
  }
  return out
}

/** How many keys of each status (edited in a screen / file or environment only / not in the UI). */
export function statusCounts(keys: ConfigKey[]): Record<KeyStatus, number> {
  const out: Record<KeyStatus, number> = { editor: 0, file: 0, gap: 0 }
  for (const k of keys) out[keyStatus(k)] += 1
  return out
}

/** Per section: how many keys, how many still not in the UI — the left list. */
export function sectionCounts(keys: ConfigKey[]): { section: string; total: number; gaps: number }[] {
  const by = new Map<string, { section: string; total: number; gaps: number }>()
  for (const k of keys) {
    const s = by.get(k.section) ?? { section: k.section, total: 0, gaps: 0 }
    s.total += 1
    if (keyStatus(k) === 'gap') s.gaps += 1
    by.set(k.section, s)
  }
  return [...by.values()]
}

export const getApiReference = () => api.get<ApiReference>('/admin/dev/api')

/** The left list of the API view: categories in first-seen order, each with its groups and counts. */
export function apiGroups(routes: ApiRoute[]): { category: string; groups: { group: string; n: number }[] }[] {
  const cats = new Map<string, Map<string, number>>()
  for (const r of routes) {
    const g = cats.get(r.category) ?? new Map<string, number>()
    g.set(r.group, (g.get(r.group) ?? 0) + 1)
    cats.set(r.category, g)
  }
  return [...cats].map(([category, g]) => ({
    category, groups: [...g].map(([group, n]) => ({ group, n })).sort((a, b) => a.group.localeCompare(b.group)),
  }))
}

/** Routes of *group* (all when blank) matching *q* in the path, summary, permission or handler file. */
export function filterRoutes(routes: ApiRoute[], q: string, group: string): ApiRoute[] {
  const n = q.trim().toLowerCase()
  return routes.filter((r) => (!group || r.group === group)
    && (!n || r.path.toLowerCase().includes(n) || r.summary.toLowerCase().includes(n)
      || r.permissions.some((p) => p.toLowerCase().includes(n)) || r.file.toLowerCase().includes(n)))
}

/** The label a setting has in its editor (an i18n key; Settings → App field labels), so the
 *  Configuration view reads like the screens. Keys edited elsewhere or file-only have their own. */
export const CONFIG_LABELS: Record<string, string> = {
  'app.name': 'settings.app.name', 'app.host': 'settings.app.host', 'app.port': 'settings.app.port',
  'app.log_level': 'settings.app.logLevel', 'app.log_format': 'settings.app.logFormat', 'app.log_file': 'settings.app.logFile',
  'app.log_file_max_mb': 'settings.app.logMaxMb', 'app.log_file_backups': 'settings.app.logBackups',
  'app.log_levels': 'developer.labels.logLevels', 'app.static_dir': 'developer.labels.staticDir', 'app.base_path': 'settings.app.basePath',
  'app.hot_reload': 'settings.app.hotReload', 'app.default_language': 'settings.app.defaultLanguage', 'app.oracle_thick': 'settings.app.oracleThick',
  'app.default_period': 'settings.app.defaultPeriod', 'app.default_page_size': 'settings.app.defaultPageSize',
  'app.dashboard_cache_seconds': 'settings.app.dashboardCache', 'app.tabs_multi': 'settings.app.tabsMulti',
  'app.tabs_close_on_app_switch': 'settings.app.tabsCloseOnSwitch', 'app.tabs_max': 'settings.app.tabsMax', 'app.drill_inline': 'settings.app.drillInline',
  'auth.backend': 'settings.app.authBackend', 'auth.pool': 'settings.app.authPool', 'auth.jwt_issuer': 'settings.app.authIssuer',
  'auth.access_token_ttl': 'settings.app.authAccessTtl', 'auth.refresh_token_ttl': 'settings.app.authRefreshTtl',
  'auth.refresh_reuse_grace': 'settings.app.authGrace', 'auth.login_max_failures': 'settings.app.authMaxFailures',
  'auth.login_window_seconds': 'settings.app.authWindow', 'auth.jwt_secret': 'developer.labels.jwtSecret',
  'auth.jwt_algorithm': 'developer.labels.jwtAlgorithm', 'auth.toml_path': 'developer.labels.authFile',
  'oidc.enabled': 'settings.app.oidcEnabled', 'oidc.discovery_url': 'settings.app.oidcDiscoveryUrl', 'oidc.client_id': 'settings.app.oidcClientId',
  'oidc.client_secret': 'settings.app.oidcClientSecret', 'oidc.scopes': 'settings.app.oidcScopes', 'oidc.username_claim': 'settings.app.oidcUsernameClaim',
  'oidc.email_claim': 'settings.app.oidcEmailClaim', 'oidc.name_claim': 'settings.app.oidcNameClaim', 'oidc.attribute_claims': 'settings.app.oidcAttributeClaims',
  'oidc.redirect_url': 'settings.app.oidcRedirectUrl', 'oidc.frontend_redirect': 'settings.app.oidcFrontendRedirect', 'oidc.session_secret': 'settings.app.oidcSessionSecret',
  'ai.enabled': 'settings.app.aiEnabled', 'ai.api_key': 'settings.app.aiApiKey', 'ai.model': 'settings.app.aiModel', 'ai.max_tokens': 'settings.app.aiMaxTokens',
  'ai.max_iterations': 'settings.app.aiMaxIter', 'ai.system_prompt': 'settings.app.aiSystemPrompt', 'ai.thinking': 'settings.app.aiThinking',
  'ai.effort': 'settings.app.aiEffort', 'ai.request_timeout': 'settings.app.aiTimeout', 'ai.connector_tools': 'settings.app.aiConnectorTools',
  'ai.api_tool': 'settings.app.aiApiTool', 'ai.scaffold_tools': 'settings.app.aiScaffoldTools', 'ai.ops_tools': 'settings.app.aiOpsTools',
  'ai.explorer_tools': 'settings.app.aiExplorerTools', 'ai.allowed_connectors': 'settings.app.aiAllowedConnectors', 'ai.web_fetch_domains': 'settings.app.aiWebDomains',
  'ai.web_fetch_max_uses': 'settings.app.aiWebMaxUses', 'ai.allowed_models': 'settings.app.aiAllowedModels', 'ai.starters': 'settings.app.aiStarters',
  'license.key': 'settings.app.licenseKey', 'crypto.master_key': 'developer.labels.masterKey',
  'notify.enabled': 'settings.app.notifyEnabled', 'notify.pool': 'settings.app.notifyPool', 'notify.smtp_host': 'settings.app.smtpHost',
  'notify.smtp_port': 'settings.app.smtpPort', 'notify.smtp_user': 'settings.app.smtpUser', 'notify.smtp_password': 'settings.app.smtpPassword',
  'notify.smtp_from': 'settings.app.smtpFrom', 'notify.smtp_tls': 'settings.app.smtpStartTls', 'notify.smtp_ssl': 'settings.app.smtpImplicitTls',
  'notify.smtp_timeout': 'settings.app.smtpTimeout', 'notify.retention_days': 'settings.app.notifyRetention',
  'jobs.enabled': 'settings.app.jobsEnabled', 'jobs.pool': 'settings.app.jobsPool', 'jobs.run_log_max_lines': 'settings.app.jobsLogLines',
  'jobs.files_dir': 'settings.app.jobsFilesDir', 'jobs.max_upload_mb': 'settings.app.jobsMaxUpload',
  'changesets.enabled': 'settings.app.changesetsEnabled', 'changesets.pool': 'settings.app.changesetsPool',
  'changesets.retention_days': 'settings.app.changesetsRetention', 'changesets.post_apply': 'developer.labels.postApply',
  'reports.branding': 'developer.labels.branding', 'promotion.targets': 'developer.labels.promotionTargets',
}

/** A setting's label: its editor's (``t`` resolves the i18n key), a configuration file's name for
 *  the ``*.config_path`` keys, else its description's first sentence, else empty. */
export function keyLabel(k: ConfigKey, t: (key: string, opts?: Record<string, unknown>) => string): string {
  const i18n = CONFIG_LABELS[k.key]
  if (i18n) return t(i18n)
  if (k.name === 'config_path' || k.name === 'templates_config_path' || k.name === 'dictionary_path') {
    return t('developer.labels.fileOf', { section: k.name === 'dictionary_path' ? 'dictionary' : k.section })
  }
  return (k.description ?? '').split('. ')[0]
}

/** One block of a docstring as the views render it. */
export type DocBlock = { kind: 'p'; parts: DocPart[] } | { kind: 'list'; items: DocPart[][] }
export type DocPart = { text: string; code?: boolean; em?: boolean }

/** A Python docstring (reST-ish: hard-wrapped lines, ````code````, ``*emphasis*``, ``-`` / ``*`` lists)
 *  as paragraphs and lists of inline parts — the wrapping undone, the marks turned into styles. */
export function docBlocks(doc: string): DocBlock[] {
  const blocks: DocBlock[] = []
  for (const chunk of doc.replace(/\r/g, '').split(/\n\s*\n/)) {
    const lines = chunk.split('\n').map((l) => l.trim()).filter(Boolean)
    if (!lines.length) continue
    if (/^[-*•] /.test(lines[0])) {
      // a list: a new item per marker line, continuation lines joined to it
      const items: string[] = []
      for (const l of lines) {
        if (/^[-*•] /.test(l)) items.push(l.slice(2))
        else if (items.length) items[items.length - 1] += ` ${l}`
      }
      blocks.push({ kind: 'list', items: items.map(inlineParts) })
    } else {
      blocks.push({ kind: 'p', parts: inlineParts(lines.join(' ')) })
    }
  }
  return blocks
}

function inlineParts(text: string): DocPart[] {
  const out: DocPart[] = []
  const re = /``([^`]+)``|`([^`]+)`|\*([^*\s][^*]*?)\*/g
  let last = 0
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) out.push({ text: text.slice(last, m.index) })
    if (m[1] !== undefined || m[2] !== undefined) out.push({ text: m[1] ?? m[2], code: true })
    else out.push({ text: m[3], em: true })
    last = re.lastIndex
  }
  if (last < text.length) out.push({ text: text.slice(last) })
  return out
}

export const getDataModel = () => api.get<DataModel>('/admin/dev/data')

/** The tables matching *q* in their name, label, physical name or one of their columns. */
export function filterTables(tables: DataTableRef[], q: string): DataTableRef[] {
  const n = q.trim().toLowerCase()
  if (!n) return tables
  return tables.filter((t) => t.name.toLowerCase().includes(n) || t.label.toLowerCase().includes(n)
    || t.physical.toLowerCase().includes(n) || t.columns.some((c) => c.name.toLowerCase().includes(n) || c.label.toLowerCase().includes(n)))
}

/** The left list: framework areas first, then each connector, each with its tables in name order. */
export function tableGroups(tables: DataTableRef[]): { owner: string; group: string; tables: DataTableRef[] }[] {
  const by = new Map<string, { owner: string; group: string; tables: DataTableRef[] }>()
  for (const t of tables) {
    const k = `${t.owner}:${t.group}`
    const g = by.get(k) ?? { owner: t.owner, group: t.group, tables: [] }
    g.tables.push(t)
    by.set(k, g)
  }
  return [...by.values()]
    .map((g) => ({ ...g, tables: [...g.tables].sort((a, b) => a.name.localeCompare(b.name)) }))
    .sort((a, b) => (a.owner === b.owner ? a.group.localeCompare(b.group) : a.owner === 'framework' ? -1 : 1))
}
