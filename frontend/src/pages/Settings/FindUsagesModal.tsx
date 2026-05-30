// Shared "Find usages" modal — every Settings editor surfaces a button that opens this with
// {kind, name, scope?}. Hits POST /admin/find-usages, groups results by type, renders each
// usage as a clickable row that deep-links to the editor that owns the referencing config
// (the URL pattern is the same one Connectors → Open in Screens already uses — ?tab=...&app=...).
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { ArrowUpRight, Search, AlertCircle } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SpinnerRing, Mono } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

export interface Usage {
  type: string
  label: string
  deep_link: Record<string, unknown>
}

interface UsagesResponse {
  kind: string
  name: string
  scope: string | null
  usages: Usage[]
  count: number
}

export interface FindUsagesTarget {
  kind: string                 // e.g. 'dictionary_entry' | 'query' | 'connector' | 'screen' | 'menu_item' | 'pool' | 'chart' | 'lookup' | 'sequence' | 'enum' | 'api_endpoint'
  name: string
  scope?: string               // 'shared' for dictionary, <connector> for query / api_endpoint / chart, <app> for screen / menu_item; ignored otherwise
  /** What to call the entity in the modal title — defaults to ``<kind> <name>`` (e.g. ``dictionary entry USR_ID``). */
  label?: string
}

const Header = styled(ModalHeader)`
  display: flex; align-items: center; gap: 8px;
  & .count { color: ${colors.text.muted}; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; }
`
const Body = styled(ModalBody)`max-height: min(70vh, 640px); overflow-y: auto;`
const Empty = styled.div`color: ${colors.text.muted}; padding: 16px; text-align: center;`
const Group = styled.div`margin-bottom: 14px; &:last-child { margin-bottom: 0; }`
const GroupHead = styled.div`
  font-size: ${fontSize.micro}; font-weight: 700; text-transform: uppercase; letter-spacing: 0.07em;
  color: ${colors.text.muted}; padding: 6px 4px;
`
const Row = styled.button`
  display: flex; align-items: center; gap: 10px; width: 100%;
  padding: 8px 10px; margin: 2px 0; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input};
  color: ${colors.text.secondary}; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  text-align: left; cursor: pointer;
  & .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & svg { flex-shrink: 0; opacity: 0.55; }
  &:hover { background: ${colors.blue.bg}; border-color: ${colors.blue.border}; color: ${colors.text.primary}; }
  &:hover svg { opacity: 1; color: ${colors.blue.main}; }
`

// Friendly group titles. Falls back to the raw type name (snake_case) when missing — better
// than dropping the row entirely if the backend grows a new usage type before this map.
const TYPE_TITLES: Record<string, string> = {
  screen_column_dd: 'Screen columns',
  connector_column_dd: 'Connector query columns',
  prompt_field_dd: 'Prompt fields',
  sequence_dd_id: 'Sequences (dd_id)',
  lookup_return_param: 'Lookup return params',
  entry_rules_values: 'Dictionary entries (rules_values)',
  screen_read_query: 'Screens (read query)',
  screen_update_query: 'Screens (update query)',
  screen_insert_query: 'Screens (insert query)',
  screen_delete_query: 'Screens (delete query)',
  nested_form_read_query: 'Nested-form tabs (read query)',
  nested_form_update_query: 'Nested-form tabs (update query)',
  nested_form_insert_query: 'Nested-form tabs (insert query)',
  action_run_query: 'Actions (run_query)',
  action_navigate_to: 'Actions (navigate)',
  lookup_query: 'Lookups (query)',
  sequence_query: 'Sequences (query)',
  sequence_previous_query: 'Sequences (previous_query)',
  export_sheet_query: 'Excel export sheets',
  chart_query: 'Charts',
  dashboard_widget_query: 'Dashboard widgets',
  nomaflow_step_query: 'Nomaflow SQL steps',
  screen_connector: 'Screens',
  nested_tab_connector: 'Nested tabs',
  action_connector: 'Actions',
  lookup_connector: 'Lookups',
  sequence_connector: 'Sequences',
  menu_connector: 'Menu items',
  chart_connector: 'Charts',
  dashboard_widget_connector: 'Dashboard widgets',
  dashboard_connector: 'Dashboards',
  export_sheet_connector: 'Excel export sheets',
  nomaflow_step_connector: 'Nomaflow steps',
  menu_target_screen: 'Menu items',
  nested_table_screen: 'Parent screens (nested_table)',
  action_navigate_screen: 'Actions (navigate)',
  action_api_call: 'Actions (call_api)',
  connector_pool: 'Connectors (pool)',
  connector_home: 'Connector home',
  screen_chart_id: 'Screens (chart_id)',
  dashboard_widget_chart: 'Dashboard widgets (chart ref)',
}

function groupUsages(usages: Usage[]): Array<{ type: string; title: string; rows: Usage[] }> {
  const buckets = new Map<string, Usage[]>()
  for (const u of usages) {
    if (!buckets.has(u.type)) buckets.set(u.type, [])
    buckets.get(u.type)!.push(u)
  }
  return [...buckets.entries()]
    .sort(([a], [b]) => (TYPE_TITLES[a] ?? a).localeCompare(TYPE_TITLES[b] ?? b))
    .map(([type, rows]) => ({ type, title: TYPE_TITLES[type] ?? type, rows }))
}

// Translate a deep_link payload into a Settings URL the navigate hook can push. Mirrors the
// cross-link patterns the editors already understand (ScreensBuilder consumes ?app= + ?screen=,
// ConnectorsBuilder consumes ?connector= + ?query=, …). Returns null when we don't know how
// to route (the row falls back to a no-op click).
export function deepLinkToUrl(link: Record<string, unknown>): string | null {
  const editor = String(link.editor || '')
  const qp = new URLSearchParams()
  if (editor === 'screens') {
    qp.set('tab', 'screens')
    if (link.app) qp.set('app', String(link.app))
    if (link.screen) qp.set('screen', String(link.screen))
    return `/settings?${qp.toString()}`
  }
  if (editor === 'connectors') {
    qp.set('tab', 'connectors')
    if (link.connector) qp.set('connector', String(link.connector))
    if (link.query) qp.set('query', String(link.query))
    return `/settings?${qp.toString()}`
  }
  if (editor === 'dictionary') {
    qp.set('tab', 'dictionary')
    if (link.kind) qp.set('kind', String(link.kind))
    if (link.scope) qp.set('scope', String(link.scope))
    if (link.name) qp.set('name', String(link.name))
    return `/settings?${qp.toString()}`
  }
  if (editor === 'menus') {
    qp.set('tab', 'menus')
    if (link.app) qp.set('app', String(link.app))
    if (link.item) qp.set('item', String(link.item))
    return `/settings?${qp.toString()}`
  }
  if (editor === 'charts') {
    qp.set('tab', 'charts')
    if (link.scope) qp.set('scope', String(link.scope))
    if (link.chart) qp.set('chart', String(link.chart))
    return `/settings?${qp.toString()}`
  }
  if (editor === 'dashboards') {
    qp.set('tab', 'dashboards')
    if (link.dashboard) qp.set('dashboard', String(link.dashboard))
    return `/settings?${qp.toString()}`
  }
  return null
}

export function FindUsagesModal({
  target, onClose,
}: { target: FindUsagesTarget; onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [response, setResponse] = useState<UsagesResponse | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null); setResponse(null)
    api.post<UsagesResponse>('/admin/find-usages', { kind: target.kind, name: target.name, scope: target.scope })
      .then((r) => { if (!cancelled) setResponse(r) })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [target.kind, target.name, target.scope])

  const groups = useMemo(() => groupUsages(response?.usages ?? []), [response])

  const titleEntity = target.label ?? `${target.kind} ${target.name}${target.scope && target.scope !== 'shared' ? ` (${target.scope})` : ''}`

  return (
    <Overlay onClick={onClose}>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(640px, 96vw)' }}>
        <Header>
          <Search size={16} />
          <span>{t('findUsages.title', 'Find usages')}</span>
          <span className="count">· {titleEntity}</span>
          {response && (
            <span className="count" style={{ marginLeft: 'auto' }}>
              {t('findUsages.count', '{{count}} reference(s)', { count: response.count })}
            </span>
          )}
        </Header>
        <Body>
          {loading && <div style={{ padding: 20, textAlign: 'center' }}><SpinnerRing size={16} thickness={2} /></div>}
          {error && <Banner $tone="error"><AlertCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />{error}</Banner>}
          {!loading && !error && response && response.usages.length === 0 && (
            <Empty>
              {t('findUsages.empty',
                'No references found. Safe to rename or delete — nothing in the loaded configuration points at this.')}
              {' '}
              <span style={{ display: 'block', fontSize: fontSize.micro, marginTop: 8 }}>
                {t('findUsages.reloadHint',
                  'Reload the configuration first (POST /admin/reload) if you edited a file on disk and need fresh results.')}
              </span>
            </Empty>
          )}
          {!loading && !error && response && response.usages.length > 0 && (
            <>
              {groups.map((g) => (
                <Group key={g.type}>
                  <GroupHead>{g.title} · {g.rows.length}</GroupHead>
                  {g.rows.map((u, i) => {
                    const url = deepLinkToUrl(u.deep_link)
                    const onClick = () => {
                      if (url) { navigate(url); onClose() }
                    }
                    return (
                      <Row key={`${u.type}-${i}`} onClick={onClick} title={url ?? ''}>
                        <Mono>{u.label}</Mono>
                        {url && <ArrowUpRight size={14} />}
                      </Row>
                    )
                  })}
                </Group>
              ))}
            </>
          )}
        </Body>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.close', 'Close')}</Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

export default FindUsagesModal
