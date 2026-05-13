// Structured editor for `[connectors.*]` (sql + api) — Phase-7 config builder. Left = the connector
// list; right = a SchemaNavigator over the chosen connector's schema — drill connector → query →
// column → … via a breadcrumb (no nested accordions). Save validates each against the discriminated
// schema + rewrites only the [connectors.*] tables of connectors.toml (a changed connector's subtree
// is re-rendered, so its inline `columns = [{…}]` may become `[[…]]` — review in git), then reloads.
// No rename yet — delete + re-add. Renders the body only; Settings/index.tsx wraps the page.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, Database, Globe, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, Mono, SchemaNavigator, type JsonSchema } from '../../common'
import type { ConfigSchemas, ConnectorsDoc } from '../../types/config'
import { colors, fontSize, fonts, radius } from '../../theme'

type Connectors = Record<string, Record<string, unknown>>

const Split = styled.div`display: flex; gap: 14px; align-items: flex-start;`
const NavCol = styled.div`flex: 0 0 210px; display: flex; flex-direction: column; gap: 4px; min-width: 0;`
const NavSearch = styled.div`
  display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 8px; margin-bottom: 2px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: ${colors.bg.input}; color: ${colors.text.muted};
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; &::placeholder { color: ${colors.text.muted}; } }
`
const NavItem = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 7px; padding: 7px 10px; border-radius: ${radius.md}; text-align: left;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.mono}; cursor: pointer;
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  & .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const FormCol = styled(Card)`flex: 1; min-width: 0;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`
const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.5; margin: 0;`

export default function ConnectorsBuilder() {
  const { t } = useTranslation()
  const [schemas, setSchemas] = useState<ConfigSchemas | null>(null)
  const [path, setPath] = useState('')
  const [conns, setConns] = useState<Connectors | null>(null)
  const [original, setOriginal] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([api.get<ConfigSchemas>('/admin/config/schema'), api.get<ConnectorsDoc>('/admin/config/connectors/parsed')])
      .then(([s, d]) => {
        setSchemas(s); setPath(d.path); setConns(d.connectors); setOriginal(JSON.stringify(d.connectors))
        setSel((cur) => (cur && d.connectors[cur] ? cur : Object.keys(d.connectors)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  const dirty = useMemo(() => conns != null && JSON.stringify(conns) !== original, [conns, original])
  const schemaFor = (c: Record<string, unknown>): JsonSchema | null => (!schemas ? null : c.type === 'api' ? schemas.api : schemas.sql)

  const update = (name: string, v: Record<string, unknown>) => setConns((p) => ({ ...(p ?? {}), [name]: { ...v, type: (p ?? {})[name]?.type } }))
  const addConnector = (type: 'sql' | 'api') => {
    const name = window.prompt(t('settings.connectors.namePrompt'))?.trim()
    if (!name) return
    if (conns && name in conns) { setSel(name); return }
    setConns((p) => ({ ...(p ?? {}), [name]: type === 'api' ? { type: 'api', base_url: '' } : { type: 'sql', queries: [] } }))
    setSel(name); setStatus(null)
  }
  const removeConnector = (name: string) => {
    if (!window.confirm(t('settings.connectors.confirmDelete', { name }))) return
    setConns((p) => { const next = { ...(p ?? {}) }; delete next[name]; return next })
    setSel((s) => (s === name ? null : s)); setStatus(null)
  }

  async function save() {
    if (!conns) return
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/connectors/parsed', { connectors: conns })
      const r = await api.post<{ connectors: string[] }>('/admin/reload')
      setStatus(t('settings.connectors.saved', { connectors: r.connectors.join(', ') || `(${t('common.none')})` }))
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  if (error && !conns) return <Banner $tone="error">{error}</Banner>
  if (!conns || !schemas) return <Centered />
  const names = Object.keys(conns)
  const needle = q.trim().toLowerCase()
  const shownNames = needle ? names.filter((n) => n.toLowerCase().includes(needle)) : names
  const selSchema = sel && conns[sel] ? schemaFor(conns[sel]) : null

  return (
    <Stack gap={12}>
      <Mono>{path}</Mono>
      <Split>
        <NavCol>
          {names.length > 6 && (
            <NavSearch>
              <Search size={13} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`filter ${names.length}…`} />
            </NavSearch>
          )}
          {shownNames.map((n) => (
            <NavItem key={n} $active={n === sel} onClick={() => { setSel(n); setStatus(null) }}>
              {conns[n].type === 'api' ? <Globe size={13} /> : <Database size={13} />} <span className="name">{n}</span>
            </NavItem>
          ))}
          {shownNames.length === 0 && <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '2px 4px' }}>no match</div>}
          <Row gap={4} style={{ marginTop: 6 }}>
            <Button $variant="ghost" $size="sm" onClick={() => addConnector('sql')} style={{ flex: 1, justifyContent: 'flex-start' }}><Plus size={13} /> {t('settings.connectors.addSql')}</Button>
          </Row>
          <Button $variant="ghost" $size="sm" onClick={() => addConnector('api')} style={{ justifyContent: 'flex-start' }}><Plus size={13} /> {t('settings.connectors.addApi')}</Button>
        </NavCol>
        <FormCol>
          {sel && conns[sel] && selSchema ? (
            <Stack gap={12}>
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>[connectors.{sel}] <span style={{ color: colors.text.muted, fontWeight: 400 }}>· {String(conns[sel].type)}</span></strong>
                <Button $variant="danger" $size="sm" onClick={() => removeConnector(sel)} disabled={busy}><Trash2 size={13} /> {t('settings.connectors.delete')}</Button>
              </Row>
              <SchemaNavigator root={{ label: sel, schema: selSchema, value: conns[sel], onChange: (v) => update(sel, v) }} />
            </Stack>
          ) : (
            <Empty>{names.length ? t('settings.connectors.pickOne') : t('settings.connectors.empty')}</Empty>
          )}
        </FormCol>
      </Split>
      <Row>
        <Button $variant="primary" onClick={save} disabled={busy || !dirty}>
          {busy ? <SpinnerRing size={14} thickness={2} /> : <Save size={14} />} {t('common.save')}
        </Button>
        <Button onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
          {busy ? <SpinnerRing size={14} thickness={2} /> : <RefreshCw size={14} />} {t('settings.pools.reloadFromDisk')}
        </Button>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
        {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
      </Row>
      <Hint>{t('settings.connectors.hint')}</Hint>
    </Stack>
  )
}
