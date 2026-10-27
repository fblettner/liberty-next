// One entity of an explorer model: which query it is, how it relates to the hub (cardinality,
// key), and what its columns mean (measure with a default aggregate, hidden, the default date).
// The columns are discovered by running the query for one row — the same discovery the model
// endpoint does — so the operator ticks real columns instead of typing names.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { api, ApiError } from '../../api/client'
import { Banner, Checkbox, Field, Input, SearchSelect, SpinnerRing, type SearchSelectOption } from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import type { QueryResult } from '../../types/connectors'
import type { ExplorerEntityBody } from '../../types/config'
import { colors, fontSize, fonts, radius } from '../../theme'

const AGGREGATES = ['sum', 'avg', 'min', 'max', 'count', 'count_distinct'] as const

const Grid2 = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 12px;`
const Grid3 = styled.div`display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px;`
const Seg = styled.div`display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const SegBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; padding: 6px 12px; border: 0; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; font-weight: 600;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')}; color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  & + & { border-left: 1px solid ${colors.border}; }
`
const Table = styled.table`
  width: 100%; border-collapse: collapse; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & th { text-align: left; font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em; padding: 6px 8px; border-bottom: 1px solid ${colors.border}; }
  & td { padding: 4px 8px; border-bottom: 1px solid ${colors.border}; vertical-align: middle; }
  & td.name { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  & td.type { color: ${colors.text.muted}; font-family: ${fonts.mono}; font-size: ${fontSize.micro}; }
`
const Muted = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 12px 0;`

export interface ExplorerEntityRecord extends ExplorerEntityBody { id: string }

/** A result column with the hints the matching screen gives it (label, key, hidden, format). */
interface DiscoveredColumn { name: string; type: string | null; label?: string; key?: boolean; hidden?: boolean; format?: string }

export function ExplorerEntityModal({ initial, app, takenIds, onSave, onClose }: {
  initial: ExplorerEntityRecord | null
  app: string
  takenIds: string[]
  onSave: (id: string, body: ExplorerEntityBody) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const { connectors, screens } = useWorkspace()
  const [id, setId] = useState(initial?.id ?? '')
  const [label, setLabel] = useState(initial?.label ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [connector, setConnector] = useState(initial?.connector ?? '')
  const [query, setQuery] = useState(initial?.query ?? '')
  const [screen, setScreen] = useState(initial?.screen ?? '')
  const [cardinality, setCardinality] = useState<'one' | 'many'>(initial?.cardinality ?? 'one')
  // Column names are kept UPPERCASE (the framework's convention — Oracle's case; Postgres
  // returns lowercase and the backend matches without case).
  const [key, setKey] = useState<string[]>((initial?.key ?? []).map((c) => c.toUpperCase()))
  const [hidden, setHidden] = useState<string[]>((initial?.hidden ?? []).map((c) => c.toUpperCase()))
  const [measures, setMeasures] = useState<Record<string, string>>(Object.fromEntries(Object.entries(initial?.measures ?? {}).map(([k, v]) => [k.toUpperCase(), v])))
  const [defaultDate, setDefaultDate] = useState((initial?.default_date ?? '').toUpperCase())
  const [columns, setColumns] = useState<DiscoveredColumn[] | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const mark = <T,>(set: (v: T) => void) => (v: T) => { set(v); setDirty(true) }

  const effConnector = connector || app
  const sqlConns = useMemo(() => (connectors ?? []).filter((c) => c.type === 'sql'), [connectors])
  const connectorOpts = useMemo<SearchSelectOption[]>(() => sqlConns.map((c) => ({ value: c.name, label: c.name })), [sqlConns])
  const queryOpts = useMemo<SearchSelectOption[]>(() => {
    const c = sqlConns.find((x) => x.name === effConnector)
    if (!c || c.type !== 'sql') return []
    return c.queries.filter((q) => q.statement_type === 'SELECT').map((q) => ({ value: q.name, label: q.label || q.name, mono: q.name }))
  }, [sqlConns, effConnector])
  const screenOpts = useMemo<SearchSelectOption[]>(() => {
    const list = screens?.[app] ?? []
    const matching = list.filter((s) => s.read_query === query && s.connector === effConnector)
    const rest = list.filter((s) => !matching.includes(s))
    return [...matching, ...rest].map((s) => ({ value: s.id, label: s.label || s.id, mono: s.id }))
  }, [screens, app, query, effConnector])

  // Discover the columns whenever the query changes (one row — the row cap keeps it cheap).
  useEffect(() => {
    if (!query) { setColumns(null); return }
    let cancelled = false
    setDiscovering(true); setError(null)
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(effConnector)}/${encodeURIComponent(query)}?_limit=1`)
      .then((r) => {
        if (cancelled) return
        const cols: DiscoveredColumn[] = r.columns.map((c) => ({ name: c.name.toUpperCase(), type: c.type ?? null, label: c.label, key: c.key, hidden: c.hidden, format: c.format }))
        setColumns(cols)
        // A new entity takes its key from the screen's key columns — the screen already knows.
        if (!initial && key.length === 0) {
          const fromScreen = cols.filter((c) => c.key).map((c) => c.name)
          if (fromScreen.length) setKey(fromScreen)
        }
      })
      .catch((e) => { if (!cancelled) { setColumns([]); setError(e instanceof ApiError ? e.message : String(e)) } })
      .finally(() => { if (!cancelled) setDiscovering(false) })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- the key pre-fill reads the state once, on discovery
  }, [effConnector, query])

  const colNames = useMemo(() => new Set((columns ?? []).map((c) => c.name)), [columns])
  // Columns referenced by the record but absent from the discovered list (a renamed query) stay
  // visible so the operator can untick them.
  const extra = useMemo(() => {
    const all = new Set([...key, ...hidden, ...Object.keys(measures), ...(defaultDate ? [defaultDate] : [])])
    return [...all].filter((n) => !colNames.has(n)).map((name): DiscoveredColumn => ({ name, type: null }))
  }, [key, hidden, measures, defaultDate, colNames])
  const rows = [...(columns ?? []), ...extra]
  const toggle = (list: string[], set: (v: string[]) => void, name: string) => set(list.includes(name) ? list.filter((x) => x !== name) : [...list, name])

  const idError = !id.trim() ? t('settings.explorer.idRequired') : !/^[A-Za-z0-9_-]+$/.test(id) ? t('settings.explorer.idChars') : takenIds.includes(id) ? t('settings.explorer.idTaken') : null
  const canSave = !idError && !!query && dirty

  const save = () => {
    const body: ExplorerEntityBody = { query, cardinality }
    if (label.trim()) body.label = label.trim()
    if (description.trim()) body.description = description.trim()
    if (connector && connector !== app) body.connector = connector
    if (screen) body.screen = screen
    if (key.length) body.key = key
    if (hidden.length) body.hidden = hidden
    if (Object.keys(measures).length) body.measures = measures
    if (defaultDate) body.default_date = defaultDate
    onSave(id.trim(), body)
  }

  return (
    <EditorModalShell title={initial ? `${t('settings.explorer.entity')} · ${initial.id}` : t('settings.explorer.addEntity')} onClose={onClose}
      dirty={dirty} onSave={save} saveDisabled={!canSave}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '4px 0' }}>
        {error && <Banner $tone="error">{error}</Banner>}
        <Grid3>
          <Field label={t('settings.explorer.entityId')}>
            <Input value={id} onChange={(e) => mark(setId)(e.target.value)} disabled={!!initial} placeholder="sales" />
            {idError && !initial && <span style={{ color: colors.red.main, fontSize: fontSize.micro }}>{idError}</span>}
          </Field>
          <Field label={t('settings.explorer.label')}><Input value={label} onChange={(e) => mark(setLabel)(e.target.value)} /></Field>
          <Field label={t('settings.explorer.cardinality')}>
            <Seg>
              <SegBtn type="button" $active={cardinality === 'one'} onClick={() => mark(setCardinality)('one')}>1..1 · {t('settings.explorer.one')}</SegBtn>
              <SegBtn type="button" $active={cardinality === 'many'} onClick={() => mark(setCardinality)('many')}>1..n · {t('settings.explorer.many')}</SegBtn>
            </Seg>
          </Field>
        </Grid3>
        <Field label={t('settings.explorer.description')}><Input value={description} onChange={(e) => mark(setDescription)(e.target.value)} /></Field>
        <Grid3>
          <Field label={t('settings.explorer.connector')}>
            <SearchSelect value={connector} onChange={(v) => { mark(setConnector)(v); setQuery('') }} options={connectorOpts} anyLabel={`${app} (${t('settings.explorer.theApp')})`} />
          </Field>
          <Field label={t('settings.explorer.query')}>
            <SearchSelect value={query} onChange={mark(setQuery)} options={queryOpts} placeholder={t('settings.access.pickItem', 'Pick…')} />
          </Field>
          <Field label={t('settings.explorer.screen')}>
            <SearchSelect value={screen} onChange={mark(setScreen)} options={screenOpts} anyLabel={t('settings.explorer.noScreen')} />
          </Field>
        </Grid3>
        <Grid2>
          <Field label={t('settings.explorer.defaultDate')}>
            <SearchSelect value={defaultDate} onChange={mark(setDefaultDate)} options={rows.map((c) => ({ value: c.name, label: c.name }))} anyLabel={t('settings.explorer.none')} />
          </Field>
        </Grid2>
        <div>
          <div style={{ fontSize: fontSize.micro, color: colors.text.muted, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>
            {t('settings.explorer.columns')} {discovering && <SpinnerRing size={11} thickness={2} />}
          </div>
          {!query ? <Muted>{t('settings.explorer.pickQueryFirst')}</Muted>
            : rows.length === 0 && !discovering ? <Muted>{t('settings.explorer.noColumns')}</Muted>
            : (
              <Table>
                <thead><tr><th>{t('settings.explorer.column')}</th><th>{t('settings.explorer.label')}</th><th>{t('settings.explorer.type')}</th><th>{t('settings.explorer.key')}</th><th>{t('settings.explorer.measure')}</th><th>{t('settings.explorer.hidden')}</th></tr></thead>
                <tbody>
                  {rows.map((c) => (
                    <tr key={c.name}>
                      <td className="name">{c.name}{!colNames.has(c.name) && columns && <span style={{ color: colors.orange.main }}> ?</span>}</td>
                      <td style={{ color: colors.text.secondary }}>{c.label ?? ''}</td>
                      <td className="type">{[c.format, c.type].filter(Boolean).join(' · ')}</td>
                      <td><Checkbox checked={key.includes(c.name)} onChange={() => { toggle(key, setKey, c.name); setDirty(true) }} /></td>
                      <td style={{ width: 160 }}>
                        <SearchSelect value={measures[c.name] ?? ''} anyLabel={t('settings.explorer.notAMeasure')}
                          onChange={(v) => { setMeasures((m) => { const n = { ...m }; if (v) n[c.name] = v; else delete n[c.name]; return n }); setDirty(true) }}
                          options={AGGREGATES.map((a) => ({ value: a, label: a }))} />
                      </td>
                      <td>{c.hidden
                        ? <span title={t('settings.explorer.hiddenByScreen')}><Checkbox checked disabled /></span>
                        : <Checkbox checked={hidden.includes(c.name)} onChange={() => { toggle(hidden, setHidden, c.name); setDirty(true) }} />}</td>
                    </tr>
                  ))}
                </tbody>
              </Table>
            )}
        </div>
      </div>
    </EditorModalShell>
  )
}
