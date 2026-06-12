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
import { Overlay, Modal, ModalBody, ModalFooter, Button, Banner, Field, Checkbox, SearchSelect, SpinnerRing, Tag, type SearchSelectOption } from '../../common'
import { TablePicker } from '../../common/SqlWizardModal'
import { ScanProposalsTable, summarizeProposals, type ScanProposal } from './DictionaryScanTable'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, radius } from '../../theme'

interface ScanItem { column: string; dd_id: string; exists: boolean; type: string | null; data_item: string | null; source: 'jde' | 'inferred'; label: string | null; format: string | null; rules?: string | null; rules_values?: string | null; default?: string | null; justify?: string | null; size?: number | null; lookup_params?: Record<string, string> | null }
interface ScanResult { scope: string; dialect: string | null; jde: boolean; items: ScanItem[] }

// "SY=01,RT=ST" ⇄ { SY: "01", RT: "ST" }
const paramsToStr = (p: Record<string, string> | null | undefined) => Object.entries(p ?? {}).map(([k, v]) => `${k}=${v}`).join(',')
function parseParams(s: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of s.split(',')) { const [k, ...v] = part.split('='); if (k.trim()) out[k.trim()] = v.join('=').trim() }
  return out
}
// One scanned column → its editable proposal (keyed by physical column).
const toProposal = (it: ScanItem): ScanProposal => ({
  column: it.column, dd_id: it.dd_id, exists: it.exists, type: it.type, source: it.source,
  keep: !it.exists, label: it.label ?? '', format: it.format ?? '',
  rules: it.rules ?? '', rules_values: it.rules_values ?? '', lookup_params: paramsToStr(it.lookup_params),
  default: it.default ?? '', justify: it.justify ?? '', size: it.size ?? null,
})

const Box = styled(Modal)`width: min(1120px, 96vw); height: min(720px, 92vh);`
const Header = styled.div`display: flex; align-items: center; gap: 10px; padding: 14px 18px; border-bottom: 1px solid ${colors.border}; flex-shrink: 0; & .title { font-size: ${fontSize.lg}; font-weight: 700; color: ${colors.text.primary}; }`
const CloseBtn = styled.button`margin-left: auto; display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: ${radius.md}; border: 1px solid ${colors.border}; background: transparent; color: ${colors.text.muted}; cursor: pointer; &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }`
const PickRow = styled.div`display: flex; gap: 10px; align-items: flex-end; flex-wrap: wrap;`
const Muted = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; text-align: center; padding: 20px;`

// Fallback format options if the framework-enum fetch fails; otherwise the live DICTIONARY_TYPE.
const FORMAT_FALLBACK: SearchSelectOption[] = [
  { value: '', label: '(text)' }, { value: 'number', label: 'number' }, { value: 'date', label: 'date' },
  { value: 'jdedate', label: 'jdedate' }, { value: 'jdetime', label: 'jdetime' }, { value: 'boolean', label: 'boolean' },
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
  // Pre-filled from the reverse wizard (connector + table passed in), else standalone (pick a table).
  const preset = !!(initialConnector && initialTable)
  const [connector, setConnector] = useState(initialConnector ?? '')
  // Standalone: the table picked from the live pool (schema EXPRESSION + name) — same lazy picker
  // the SQL wizard / query generator use, so a big JDE pool doesn't dump every table on open.
  const [pickedSchema, setPickedSchema] = useState<string | null>(null)
  const [pickedTable, setPickedTable] = useState('')
  // JD Edwards toggle — when on, the scan strips the 2-char table prefix so the dictionary entry is
  // keyed by the shared data item (FRDTAI → DTAI). Not persisted; defaulted from the connector name.
  const [jde, setJde] = useState(/jde/i.test(initialConnector ?? ''))
  // The dictionary SCOPE (which app the entries live under). Usually the connector, but for a
  // cross-pool app it differs — e.g. the `nomajde` app's queries run against the `jdedwards` data
  // pool, yet its dictionary entries are `connectors.nomajde.entries`. So this is selectable.
  const [scope, setScope] = useState(initialScope ?? initialConnector ?? '')
  const [result, setResult] = useState<ScanResult | null>(null)
  const [sel, setSel] = useState<Record<string, ScanProposal>>({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  // Format + rule dropdowns — the same framework enums the dictionary entry form uses.
  const [fmtOpts, setFmtOpts] = useState<SearchSelectOption[]>(FORMAT_FALLBACK)
  const [ruleOpts, setRuleOpts] = useState<SearchSelectOption[]>([])
  useEffect(() => {
    void api.get<{ framework_enums?: Record<string, { values?: Array<{ value: string; label?: string }> }> }>('/admin/config/schema')
      .then((r) => {
        const fe = r.framework_enums ?? {}
        const opts = (k: string): SearchSelectOption[] => (fe[k]?.values ?? []).map((o) => ({ value: o.value, label: o.label ?? o.value }))
        const fmt = opts('DICTIONARY_TYPE'); if (fmt.length) setFmtOpts(fmt)
        setRuleOpts(opts('DICTIONARY_RULES'))
      }).catch(() => undefined)
  }, [])

  const sqlConns = useMemo(() => (connectors ?? []).filter((c) => c.type === 'sql').map((c) => c.name), [connectors])

  const scan = useCallback(async () => {
    const tableName = preset ? initialTable : pickedTable
    if (!connector || !tableName) return
    setBusy(true); setError(null); setStatus(null); setResult(null)
    try {
      const schemaExpr = preset ? schema : (pickedSchema ?? undefined)
      const body = {
        connector, table: tableName, jde,
        ...(schemaExpr ? { schema: schemaExpr } : {}),
        ...(scope ? { scope } : {}),
      }
      const r = await api.post<ScanResult>('/admin/dictionary/scan', body)
      setResult(r)
      const next: Record<string, ScanProposal> = {}
      for (const it of r.items) next[it.column] = toProposal(it)
      setSel(next)
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }, [connector, preset, initialTable, pickedTable, pickedSchema, schema, scope, jde])

  // Auto-scan when pre-filled from the wizard; re-scan when the JDE toggle changes after a scan.
  useEffect(() => { if (preset) void scan() }, [preset, scan])
  // eslint-disable-next-line react-hooks/exhaustive-deps -- re-run only on the JDE toggle
  useEffect(() => { if (!preset && result) void scan() }, [jde])

  const patch = (column: string, p: Partial<ScanProposal>) => setSel((s) => ({ ...s, [column]: { ...s[column], ...p } }))
  const proposals = result ? result.items.map((it) => sel[it.column]).filter(Boolean) : []
  const chosen = proposals.filter((d) => d.keep && !d.exists)

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
      for (const s of chosen) {
        const entry: Record<string, unknown> = {}
        if (s.label.trim()) entry.label = s.label.trim()
        if (s.format) entry.format = s.format
        // JDE DD enrichment — carry the rule + default the scan pulled (operator reviews + tweaks
        // here, then in the dictionary editor). Only written when present so non-JDE stays terse.
        if (s.rules.trim()) entry.rules = s.rules.trim()
        if (s.rules_values.trim()) entry.rules_values = s.rules_values.trim()
        if (s.default.trim()) entry.default = s.default.trim()
        if (s.justify.trim()) entry.justify = s.justify.trim()   // JDE F9210.FRDRUL → right_blank / right_zero
        if (s.size != null) entry.size = s.size                  // JDE F9210.FRDTAS — data item width
        if (s.lookup_params.trim()) entry.lookup_params = parseParams(s.lookup_params)   // UDC SY/RT binds
        sc.entries[s.dd_id] = entry
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
            <>
              <PickRow>
                <Field label={t('settings.charts.connector', 'Connector')}>
                  <SearchSelect value={connector} onChange={(v) => { setConnector(v); setPickedTable(''); setPickedSchema(null); setScope(v); setResult(null); setJde(/jde/i.test(v)) }}
                    options={sqlConns.map((c) => ({ value: c, label: c, mono: c }))} placeholder={t('chart.spec.pick', 'Pick…')} />
                </Field>
                <Field label={t('settings.dictscan.scope', 'Store under (app)')}>
                  <SearchSelect value={scope} onChange={(v) => { setScope(v); setResult(null) }}
                    options={sqlConns.map((c) => ({ value: c, label: c, mono: c }))} allowCustom
                    placeholder={t('chart.spec.pick', 'Pick…')} />
                </Field>
              </PickRow>
              {/* Lazy table picker — schema -> filter -> table, no full-catalog walk. */}
              <div style={{ marginTop: 8 }}>
                <Field label={t('settings.dictscan.table', 'Table to scan')}>
                  {connector
                    ? <TablePicker connector={connector} onPick={(p) => { setPickedSchema(p.schema); setPickedTable(p.name); setResult(null) }} />
                    : <Muted>{t('settings.charts.pickConnFirst', 'Pick a connector first')}</Muted>}
                  {pickedTable && <div style={{ marginTop: 4, fontSize: fontSize.sm, color: colors.text.muted }}>{pickedSchema ? `${pickedSchema}.` : ''}{pickedTable}</div>}
                </Field>
              </div>
            </>
          )}
          {/* JD Edwards toggle — strips the 2-char table prefix so entries key on the shared data item. */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
            <Checkbox checked={jde} onChange={setJde} label={t('settings.dictscan.jde', 'JD Edwards connector (strip 2-char prefix → shared data item)')} />
            {!preset && (
              <Button $size="sm" $variant="primary" onClick={() => void scan()} disabled={!connector || !pickedTable || busy} style={{ marginLeft: 'auto' }}>
                {busy ? <SpinnerRing size={13} thickness={2} /> : <Search size={13} />} {t('settings.dictscan.scan', 'Scan')}
              </Button>
            )}
          </div>

          {result && (
            <>
              <div style={{ fontSize: fontSize.sm, color: colors.text.muted, marginTop: 4 }}>
                {result.jde ? <Tag $tone="orange">JDE</Tag> : null}{' '}
                {t('settings.dictscan.summary', '{{missing}} missing of {{total}} columns → scope “{{scope}}”', {
                  ...summarizeProposals(proposals), scope: result.scope,
                })}
              </div>
              <div style={{ flex: 1, minHeight: 0, marginTop: 6, display: 'flex' }}>
                <ScanProposalsTable items={proposals} jde={result.jde} fmtOpts={fmtOpts} ruleOpts={ruleOpts} onPatch={patch} />
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
