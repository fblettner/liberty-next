// Structured editor for `[connectors.*]` (sql + api) — Phase-7 config builder. Left = the connector
// list; right pane has two views for a SQL connector — **Tables** (the default: queries grouped by
// `<base>_<get|put|post|delete>` suffix, each table opens a unified editor where General/Columns/
// Read/Update/Insert/Delete share one form) and **Form** (the full connector config via
// SchemaNavigator — General/Pool/Queries, the escape hatch for non-CRUD queries and connector-level
// settings). API connectors only show Form. Save validates each connector against the discriminated
// schema + rewrites only the [connectors.*] tables of connectors.toml (a changed connector's subtree
// is re-rendered, so its inline `columns = [{…}]` may become `[[…]]` — review in git), then reloads.
// No rename yet — delete + re-add. Renders the body only; Settings/index.tsx wraps the page.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, Database, Globe, Search, Layers, FileCog, Copy } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, Mono, SchemaNavigator, FrameworkEnumsContext, type FrameworkEnum, type FrameworkEnums, type JsonSchema } from '../../common'
import type { ConfigSchemas, ConnectorsDoc, DictionaryDoc } from '../../types/config'
import { colors, fontSize, fonts, radius } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import ConnectorsTableEditor from './ConnectorsTableEditor'
import { CRUD_KINDS, duplicateTable as duplicateTableQueries, groupQueriesByTable, newQueryStub, tableExists } from './connectorTables'

type Connectors = Record<string, Record<string, unknown>>

const Split = styled.div`display: flex; gap: 14px; align-items: flex-start;`
// Left nav scrolls on its own (a connector list with dozens of entries shouldn't drag the page
// along when wheeled). Search row + the two "+ Add" buttons stay pinned outside the scroller —
// items live in NavList.
const NavCol = styled.div`flex: 0 0 210px; display: flex; flex-direction: column; gap: 4px; min-width: 0; max-height: calc(100dvh - 18rem);`
const NavList = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding-right: 4px;`
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
const ModeBar = styled.div`display: inline-flex; gap: 4px; padding: 3px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};`
const ModeBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; height: 26px; padding: 0 10px; border-radius: ${radius.sm};
  border: none; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  background: ${({ $active }) => ($active ? colors.bg.card : 'transparent')};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  & svg { color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)}; }
  &:hover { color: ${colors.text.primary}; }
`
// Same idea as NavList — a connector with dozens of tables (jdedwards has 52) needs its own
// scroller so wheeling doesn't drag the whole Settings page along.
const TableList = styled.div`display: flex; flex-direction: column; gap: 6px; max-height: calc(100dvh - 22rem); overflow-y: auto; padding-right: 4px;`
const TableRow = styled.button`
  display: flex; align-items: center; gap: 10px; padding: 9px 11px; width: 100%; text-align: left;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; cursor: pointer;
  color: ${colors.text.primary};
  & .text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .base { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .desc {
    font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted};
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`
const Slot = styled.span<{ $on?: boolean }>`
  display: inline-block; min-width: 38px; padding: 2px 6px; border-radius: ${radius.sm};
  font-size: ${fontSize.micro}; font-family: ${fonts.mono}; text-align: center;
  border: 1px solid ${({ $on }) => ($on ? colors.green.border : colors.border)};
  background: ${({ $on }) => ($on ? colors.green.bg : 'transparent')};
  color: ${({ $on }) => ($on ? colors.green.main : colors.text.muted)};
  opacity: ${({ $on }) => ($on ? 1 : 0.45)};
`
const RowAction = styled.span`
  display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; flex-shrink: 0;
  border-radius: ${radius.sm}; border: 1px solid ${colors.border}; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`
const LooseNote = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 8px 4px 0;`

export default function ConnectorsBuilder() {
  const { t } = useTranslation()
  const { findScreen } = useWorkspace()
  const [schemas, setSchemas] = useState<ConfigSchemas | null>(null)
  // The dictionary is read-only here — we just need its keys (entry ids per scope) to populate
  // the DD_ENTRIES dropdown that drives `ColumnHint.dd` and `FilterDep.source/column`.
  const [dictionary, setDictionary] = useState<DictionaryDoc['dictionary'] | null>(null)
  const [path, setPath] = useState('')
  const [conns, setConns] = useState<Connectors | null>(null)
  const [original, setOriginal] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [mode, setMode] = useState<'tables' | 'form'>('tables')
  const [selTable, setSelTable] = useState<string | null>(null)
  const [tq, setTq] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed'),
      api.get<DictionaryDoc>('/admin/config/dictionary/parsed'),
    ])
      .then(([s, d, dd]) => {
        setSchemas(s); setPath(d.path); setConns(d.connectors); setOriginal(JSON.stringify(d.connectors))
        setDictionary(dd.dictionary)
        setSel((cur) => (cur && d.connectors[cur] ? cur : Object.keys(d.connectors)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  // reset table selection + search when changing connector or mode
  useEffect(() => { setSelTable(null); setTq('') }, [sel, mode])

  const dirty = useMemo(() => conns != null && JSON.stringify(conns) !== original, [conns, original])
  const schemaFor = (c: Record<string, unknown>): JsonSchema | null => (!schemas ? null : c.type === 'api' ? schemas.api : schemas.sql)

  // Per-connector dynamic enum: `DD_ENTRIES` for the current connector — shared entries plus the
  // connector's own overlay (per-connector wins on collision, same as the runtime resolves them).
  // Drives `ColumnHint.dd` (the entry the column references) and `FilterDep.source/column` (the
  // source / target column for cascading filters). Recomputed when `sel` or `dictionary` changes.
  const augmentedEnums: FrameworkEnums | null = useMemo(() => {
    if (!schemas) return null
    const base: FrameworkEnums = { ...(schemas.framework_enums ?? {}) }
    if (!dictionary) { base.DD_ENTRIES = { label: 'Dictionary entries', values: [] }; return base }
    const out = new Map<string, string>()
    const ingest = (m: Record<string, Record<string, unknown>> | undefined) => {
      if (!m) return
      for (const [id, rec] of Object.entries(m)) {
        const lbl = typeof (rec as Record<string, unknown>)?.label === 'string'
          ? ((rec as Record<string, unknown>).label as string)
          : id
        out.set(id, lbl)  // later wins → per-connector overlay overrides shared
      }
    }
    ingest(dictionary.entries)
    if (sel) ingest((dictionary.connectors ?? {})[sel]?.entries)
    const values: FrameworkEnum['values'] = [...out.entries()]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([value, label]) => ({ value, label }))
    base.DD_ENTRIES = { label: 'Dictionary entries (this connector)', values }
    return base
  }, [schemas, dictionary, sel])

  const update = (name: string, v: Record<string, unknown>) => setConns((p) => ({ ...(p ?? {}), [name]: { ...v, type: (p ?? {})[name]?.type } }))
  const updateQueries = (name: string, queries: Record<string, unknown>[]) => {
    setConns((p) => {
      const cur = (p ?? {})[name] ?? {}
      return { ...(p ?? {}), [name]: { ...cur, queries } }
    })
    setStatus(null)
  }
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
  const addTable = (connectorName: string) => {
    const base = window.prompt(t('settings.tables.namePrompt'))?.trim()
    if (!base) return
    const cur = (conns ?? {})[connectorName] ?? {}
    const queries = Array.isArray(cur.queries) ? (cur.queries as Record<string, unknown>[]) : []
    const getName = `${base}_get`
    if (queries.some((q) => typeof q.name === 'string' && q.name.toLowerCase() === getName.toLowerCase())) {
      setSelTable(base); return
    }
    updateQueries(connectorName, [...queries, newQueryStub(base, 'get')])
    setSelTable(base)
  }
  const duplicateTable = (connectorName: string, oldBase: string) => {
    const cur = (conns ?? {})[connectorName] ?? {}
    const queries = Array.isArray(cur.queries) ? (cur.queries as Record<string, unknown>[]) : []
    const suggested = `${oldBase}_copy`
    const newBase = window.prompt(t('settings.tables.duplicatePrompt', { name: oldBase }), suggested)?.trim()
    if (!newBase) return
    if (newBase.toLowerCase() === oldBase.toLowerCase()) {
      window.alert(t('settings.tables.duplicateSameName'))
      return
    }
    if (tableExists(queries, newBase)) {
      window.alert(t('settings.tables.duplicateExists', { name: newBase }))
      return
    }
    const next = duplicateTableQueries(queries, oldBase, newBase)
    if (next === queries) {
      window.alert(t('settings.tables.duplicateNoSource', { name: oldBase }))
      return
    }
    updateQueries(connectorName, next)
    setSelTable(newBase)
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
  const selConn = sel && conns[sel] ? conns[sel] : null
  const selSchema = selConn ? schemaFor(selConn) : null
  const isSql = selConn?.type !== 'api'
  const effectiveMode: 'tables' | 'form' = isSql ? mode : 'form'

  // --- table grouping (only when SQL + tables mode) --------------------------
  const queriesArr = (selConn && Array.isArray(selConn.queries) ? selConn.queries : []) as Record<string, unknown>[]
  const grouped = isSql ? groupQueriesByTable(queriesArr) : { tables: [], loose: [] }
  const tNeedle = tq.trim().toLowerCase()
  const shownTables = tNeedle ? grouped.tables.filter((g) => g.base.toLowerCase().includes(tNeedle)) : grouped.tables
  const queryDefSchema = (selSchema?.$defs?.QueryDef ?? null) as JsonSchema | null
  const allDefs = (selSchema?.$defs ?? {}) as Record<string, JsonSchema>

  return (
    <FrameworkEnumsContext.Provider value={augmentedEnums}>
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
          <NavList>
            {shownNames.map((n) => (
              <NavItem key={n} $active={n === sel} onClick={() => { setSel(n); setStatus(null) }}>
                {conns[n].type === 'api' ? <Globe size={13} /> : <Database size={13} />} <span className="name">{n}</span>
              </NavItem>
            ))}
            {shownNames.length === 0 && <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '2px 4px' }}>no match</div>}
          </NavList>
          <Row gap={4} style={{ marginTop: 6 }}>
            <Button $variant="ghost" $size="sm" onClick={() => addConnector('sql')} style={{ flex: 1, justifyContent: 'flex-start' }}><Plus size={13} /> {t('settings.connectors.addSql')}</Button>
          </Row>
          <Button $variant="ghost" $size="sm" onClick={() => addConnector('api')} style={{ justifyContent: 'flex-start' }}><Plus size={13} /> {t('settings.connectors.addApi')}</Button>
        </NavCol>
        <FormCol>
          {selConn && selSchema ? (
            <Stack gap={12}>
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>
                  [connectors.{sel}] <span style={{ color: colors.text.muted, fontWeight: 400 }}>· {String(selConn.type)}</span>
                </strong>
                <Row gap={8}>
                  {isSql && (
                    <ModeBar>
                      <ModeBtn type="button" $active={effectiveMode === 'tables'} onClick={() => setMode('tables')}>
                        <Layers size={13} /> {t('settings.tables.tablesView')}
                      </ModeBtn>
                      <ModeBtn type="button" $active={effectiveMode === 'form'} onClick={() => setMode('form')}>
                        <FileCog size={13} /> {t('settings.tables.formView')}
                      </ModeBtn>
                    </ModeBar>
                  )}
                  <Button $variant="danger" $size="sm" onClick={() => sel && removeConnector(sel)} disabled={busy}>
                    <Trash2 size={13} /> {t('settings.connectors.delete')}
                  </Button>
                </Row>
              </Row>
              {effectiveMode === 'form' && (
                <SchemaNavigator root={{ label: sel!, schema: selSchema, value: selConn, onChange: (v) => update(sel!, v) }} />
              )}
              {effectiveMode === 'tables' && (
                selTable && queryDefSchema ? (() => {
                  // The corresponding Screen (if any) is keyed by (connector, get-slot name).
                  // The cross-link only shows when both are present + a screen with a dialog is
                  // actually registered for this read query.
                  const slotsForSel = grouped.tables.find((g) => g.base === selTable)?.slots ?? {}
                  const getName = slotsForSel.get?.name
                  const matchedScreen = sel && getName ? findScreen(sel, getName) : null
                  return (
                    <ConnectorsTableEditor
                      base={selTable}
                      slots={slotsForSel}
                      queries={queriesArr}
                      queryDefSchema={queryDefSchema}
                      defs={allDefs}
                      onChangeQueries={(next) => updateQueries(sel!, next)}
                      onBack={() => setSelTable(null)}
                      onDuplicate={() => sel && duplicateTable(sel, selTable)}
                      screenLink={matchedScreen ? { app: matchedScreen.app, id: matchedScreen.id } : null}
                    />
                  )
                })() : (
                  <Stack gap={10}>
                    {grouped.tables.length > 6 && (
                      <NavSearch>
                        <Search size={13} />
                        <input value={tq} onChange={(e) => setTq(e.target.value)} placeholder={`filter ${grouped.tables.length}…`} />
                      </NavSearch>
                    )}
                    {shownTables.length === 0 && grouped.tables.length > 0 && (
                      <Empty>{t('common.noMatches')}</Empty>
                    )}
                    {grouped.tables.length === 0 && (
                      <Empty>{t('settings.tables.emptyConnector')}</Empty>
                    )}
                    <TableList>
                      {shownTables.map((g) => {
                        // Friendly description for the table — read query's `description`
                        // (v1's tbl_label, e.g. "Security - Users"), falls back to `label`.
                        // Same fallback chain as TableView's title resolver and the main
                        // /connectors page; sits below the technical base name on a second row.
                        const getQ = g.slots.get?.query as Record<string, unknown> | undefined
                        const desc = (typeof getQ?.description === 'string' && getQ.description)
                                  || (typeof getQ?.label === 'string' && getQ.label)
                                  || null
                        return (
                          <TableRow key={g.base} type="button" onClick={() => setSelTable(g.base)}>
                            <span className="text">
                              <span className="base">{g.base}</span>
                              {desc && <span className="desc">{desc}</span>}
                            </span>
                            {CRUD_KINDS.map((c) => (
                              <Slot key={c} $on={!!g.slots[c]} title={g.slots[c]?.name ?? `${g.base}_${c} (missing)`}>{c.toUpperCase().slice(0, 3)}</Slot>
                            ))}
                            <RowAction
                              role="button"
                              aria-label={t('settings.tables.duplicate')}
                              title={t('settings.tables.duplicate')}
                              onClick={(e) => { e.stopPropagation(); if (sel) duplicateTable(sel, g.base) }}
                            >
                              <Copy size={13} />
                            </RowAction>
                          </TableRow>
                        )
                      })}
                    </TableList>
                    <Row gap={6}>
                      <Button $variant="ghost" $size="sm" onClick={() => sel && addTable(sel)}>
                        <Plus size={13} /> {t('settings.tables.addTable')}
                      </Button>
                    </Row>
                    {grouped.loose.length > 0 && (
                      <LooseNote>{t('settings.tables.looseHint', { count: grouped.loose.length })}</LooseNote>
                    )}
                  </Stack>
                )
              )}
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
    </FrameworkEnumsContext.Provider>
  )
}
