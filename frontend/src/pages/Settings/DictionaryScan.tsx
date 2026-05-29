// "Generate dictionary items from a table" — the v1 reverse-engineering helper. Scans a table's
// columns (POST /admin/dictionary/scan), flags which have no dictionary entry yet, fills a proposed
// definition (JDE → label from the JDE data dictionary; non-JDE → format inferred from the column
// type, label editable), lets the operator tick + edit, then merges the chosen entries into
// dictionary.toml (under the connector's scope) and reloads.
//
// Two modes: standalone (no connector/table props → shows pickers) or pre-filled from the reverse
// wizard (connector + table passed in → scans immediately).
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { X, BookText, Search } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Overlay, Modal, ModalBody, ModalFooter, Button, Banner, Field, Input, Checkbox, SearchSelect, SpinnerRing, Tag, type SearchSelectOption } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

interface ScanItem { column: string; dd_id: string; exists: boolean; type: string | null; data_item: string | null; source: 'jde' | 'inferred'; label: string | null; format: string | null }
interface ScanResult { scope: string; dialect: string | null; jde: boolean; items: ScanItem[] }
interface Sel { include: boolean; label: string; format: string }

const Box = styled(Modal)`width: min(820px, 96vw); height: min(680px, 92vh);`
const Header = styled.div`display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0; & .title { font-size: ${fontSize.lg}; font-weight: 700; color: ${colors.text.primary}; }`
const CloseBtn = styled.button`margin-left: auto; display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: ${radius.md}; border: 1px solid ${colors.border}; background: transparent; color: ${colors.text.muted}; cursor: pointer; &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }`
const PickRow = styled.div`display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap;`
const Table = styled.table`width: 100%; border-collapse: collapse; font-size: ${fontSize.base};`
const Th = styled.th`text-align: left; padding: 6px 8px; color: ${colors.text.muted}; font-weight: 600; font-size: ${fontSize.sm}; border-bottom: 1px solid ${colors.border}; position: sticky; top: 0; background: ${colors.bg.dropdown}; z-index: 1;`
const Td = styled.td`padding: 5px 8px; border-bottom: 1px solid ${colors.border}; vertical-align: middle;`
const Mono = styled.span`font-family: ${fonts.mono}; color: ${colors.text.primary};`
const Muted = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; text-align: center; padding: 20px;`

const FORMAT_OPTS: SearchSelectOption[] = [
  { value: '', label: '(text)' },
  { value: 'number', label: 'number' },
  { value: 'date', label: 'date' },
  { value: 'jdedate', label: 'jdedate' },
  { value: 'boolean', label: 'boolean' },
]

export function DictionaryScan({
  connector: initialConnector, table: initialTable, schema, scope: initialScope, onClose, onSaved,
}: {
  connector?: string
  table?: string
  schema?: string
  scope?: string
  onClose: () => void
  onSaved?: () => void
}) {
  const { t } = useTranslation()
  const { connectors } = useWorkspace()
  // Pre-filled (table mode) from the reverse wizard, else standalone (query mode).
  const preset = !!(initialConnector && initialTable)
  const [connector, setConnector] = useState(initialConnector ?? '')
  const [query, setQuery] = useState('')   // standalone: an already-reversed read query
  // The dictionary SCOPE (which app the entries live under). Usually the connector, but for a
  // cross-pool app it differs — e.g. the `nomajde` app's queries run against the `jdedwards` data
  // pool, yet its dictionary entries are `connectors.nomajde.entries`. So this is selectable.
  const [scope, setScope] = useState(initialScope ?? initialConnector ?? '')
  const [result, setResult] = useState<ScanResult | null>(null)
  const [sel, setSel] = useState<Record<string, Sel>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)

  const sqlConns = useMemo(() => (connectors ?? []).filter((c) => c.type === 'sql').map((c) => c.name), [connectors])
  // Standalone query picker: the connector's already-defined READ queries — i.e. the tables
  // you've already reversed. (Not a fresh DB introspection — that'd list every table in the
  // schema; here we only offer what's been built.)
  const readQueries = useMemo<SearchSelectOption[]>(() => {
    const c = (connectors ?? []).find((x) => x.name === connector && x.type === 'sql')
    if (!c || c.type !== 'sql') return []
    return c.queries
      .filter((q) => (q.statement_type ?? 'SELECT').toUpperCase() === 'SELECT')
      .map((q) => ({ value: q.name, label: q.label || q.name, mono: q.name }))
  }, [connectors, connector])

  const scan = useCallback(async () => {
    if (!connector || (!preset && !query)) return
    setBusy(true); setError(null); setStatus(null); setResult(null)
    try {
      const body = preset
        ? { connector, table: initialTable, ...(schema ? { schema } : {}), ...(scope ? { scope } : {}) }
        : { connector, query, ...(scope ? { scope } : {}) }
      const r = await api.post<ScanResult>('/admin/dictionary/scan', body)
      setResult(r)
      const next: Record<string, Sel> = {}
      for (const it of r.items) next[it.dd_id] = { include: !it.exists, label: it.label ?? '', format: it.format ?? '' }
      setSel(next)
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }, [connector, query, preset, initialTable, schema, scope])

  // Auto-scan when pre-filled from the wizard.
  useEffect(() => { if (preset) void scan() }, [preset, scan])

  const patch = (id: string, p: Partial<Sel>) => setSel((s) => ({ ...s, [id]: { ...s[id], ...p } }))
  const chosen = result ? result.items.filter((it) => sel[it.dd_id]?.include) : []

  const save = async () => {
    if (!result || chosen.length === 0) return
    setBusy(true); setError(null); setStatus(null)
    try {
      const doc = (await api.get<{ dictionary: Record<string, unknown> }>('/admin/config/dictionary/parsed')).dictionary as {
        connectors?: Record<string, { entries?: Record<string, unknown> }>
      }
      doc.connectors = doc.connectors || {}
      const sc = (doc.connectors[result.scope] = doc.connectors[result.scope] || {})
      sc.entries = sc.entries || {}
      for (const it of chosen) {
        const s = sel[it.dd_id]
        const entry: Record<string, unknown> = {}
        if (s.label.trim()) entry.label = s.label.trim()
        if (s.format) entry.format = s.format
        sc.entries[it.dd_id] = entry
      }
      await api.put('/admin/config/dictionary/parsed', { dictionary: doc })
      await api.post('/admin/reload')
      setStatus(t('settings.dictscan.saved', 'Added {{n}} dictionary item(s) to “{{scope}}”.', { n: chosen.length, scope: result.scope }))
      onSaved?.()
      // Refresh existing-flags so a second save doesn't re-add.
      await scan()
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }

  return (
    <Overlay onClick={onClose}>
      <Box onClick={(e) => e.stopPropagation()}>
        <Header>
          <BookText size={17} color={colors.blue.main} />
          <span className="title">{t('settings.dictscan.title', 'Generate dictionary items')}</span>
          <CloseBtn onClick={onClose} title={t('common.close')}><X size={16} /></CloseBtn>
        </Header>
        <ModalBody>
          {error && <Banner $tone="error">{error}</Banner>}
          {status && <Banner $tone="ok">{status}</Banner>}

          {!preset && (
            <PickRow>
              <Field label={t('settings.charts.connector', 'Connector')}>
                <SearchSelect value={connector} onChange={(v) => { setConnector(v); setQuery(''); setScope(v); setResult(null) }}
                  options={sqlConns.map((c) => ({ value: c, label: c, mono: c }))} placeholder={t('chart.spec.pick', 'Pick…')} />
              </Field>
              <Field label={t('settings.dictscan.query', 'Reversed table (read query)')}>
                <SearchSelect value={query} onChange={(v) => { setQuery(v); setResult(null) }}
                  options={readQueries}
                  placeholder={connector ? (readQueries.length ? t('chart.spec.pick', 'Pick…') : t('settings.dictscan.noQueries', 'No read queries on this connector')) : t('settings.charts.pickConnFirst', 'Pick a connector first')} allowCustom />
              </Field>
              <Field label={t('settings.dictscan.scope', 'Store under (app)')}>
                <SearchSelect value={scope} onChange={(v) => { setScope(v); setResult(null) }}
                  options={sqlConns.map((c) => ({ value: c, label: c, mono: c }))} allowCustom
                  placeholder={t('chart.spec.pick', 'Pick…')} />
              </Field>
              <Button $size="sm" $variant="primary" onClick={() => void scan()} disabled={!connector || !query || busy}>
                {busy ? <SpinnerRing size={13} thickness={2} /> : <Search size={13} />} {t('settings.dictscan.scan', 'Scan')}
              </Button>
            </PickRow>
          )}

          {result && (
            <>
              <div style={{ fontSize: fontSize.sm, color: colors.text.muted, marginTop: 4 }}>
                {result.jde ? <Tag $tone="orange">JDE</Tag> : null}{' '}
                {t('settings.dictscan.summary', '{{missing}} missing of {{total}} columns → scope “{{scope}}”', {
                  missing: result.items.filter((i) => !i.exists).length, total: result.items.length, scope: result.scope,
                })}
              </div>
              <div style={{ overflowY: 'auto', flex: 1, minHeight: 0, border: `1px solid ${colors.border}`, borderRadius: radius.md, marginTop: 6 }}>
                <Table>
                  <thead><tr>
                    <Th style={{ width: 36 }} />
                    <Th>{t('settings.dictscan.column', 'Column')}</Th>
                    <Th>{t('settings.dictscan.label', 'Label')}</Th>
                    <Th style={{ width: 120 }}>{t('settings.dictscan.format', 'Format')}</Th>
                    <Th style={{ width: 110 }}>{t('settings.dictscan.source', 'Source')}</Th>
                  </tr></thead>
                  <tbody>
                    {result.items.map((it) => {
                      const s = sel[it.dd_id]
                      return (
                        <tr key={it.dd_id} style={it.exists ? { opacity: 0.55 } : undefined}>
                          <Td><Checkbox checked={!!s?.include} disabled={it.exists} onChange={(v) => patch(it.dd_id, { include: v })} /></Td>
                          <Td><Mono>{it.dd_id}</Mono>{it.exists && <span style={{ marginLeft: 6, fontSize: fontSize.micro, color: colors.text.muted }}>({t('settings.dictscan.exists', 'exists')})</span>}<div style={{ fontSize: fontSize.micro, color: colors.text.muted }}>{it.type}{it.data_item ? ` · DD ${it.data_item}` : ''}</div></Td>
                          <Td><Input value={s?.label ?? ''} disabled={it.exists} onChange={(e) => patch(it.dd_id, { label: e.target.value })} placeholder={it.dd_id} /></Td>
                          <Td><SearchSelect value={s?.format ?? ''} disabled={it.exists} onChange={(v) => patch(it.dd_id, { format: v })} options={FORMAT_OPTS} allowCustom /></Td>
                          <Td>{it.source === 'jde' ? <Tag $tone="orange">JDE&nbsp;DD</Tag> : <Tag $tone="blue">{t('settings.dictscan.inferred', 'inferred')}</Tag>}</Td>
                        </tr>
                      )
                    })}
                  </tbody>
                </Table>
              </div>
            </>
          )}
          {!result && !preset && <Muted>{t('settings.dictscan.hint', 'Pick a connector + table, then Scan to find columns without a dictionary entry.')}</Muted>}
          {!result && preset && busy && <Muted><SpinnerRing size={18} thickness={2} /></Muted>}
        </ModalBody>
        <ModalFooter>
          <span style={{ fontSize: fontSize.sm, color: colors.text.muted }}>{result ? t('settings.dictscan.selected', '{{n}} selected', { n: chosen.length }) : ''}</span>
          <div style={{ flex: 1 }} />
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.close')}</Button>
          <Button $size="sm" $variant="primary" onClick={() => void save()} disabled={busy || chosen.length === 0}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : null} {t('settings.dictscan.add', 'Add {{n}} item(s)', { n: chosen.length })}
          </Button>
        </ModalFooter>
      </Box>
    </Overlay>
  )
}

export default DictionaryScan
