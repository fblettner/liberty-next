// Screen Creation Assistant — a guided "train" (in a MODAL) that turns picked tables into a live
// screen + dialog + menu in one pass. It REUSES the Query Wizard's pieces (TablePicker, generateSql,
// the join types) and the CRUD generator (buildCrudSql), runs the JDE dictionary scan when the source
// is JDE, then POSTs one payload to /admin/assistant/scaffold which writes connectors/dictionary/
// screens/menus and reloads. Superuser-only (the sidebar gates entry; the endpoint re-checks).
import { Fragment, useCallback, useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import styled from '@emotion/styled'
import { Plus, X, ChevronRight, ChevronLeft, ChevronDown, Check, Wand2, ArrowRight, ArrowLeft, KeyRound, RotateCcw, BookOpen, Search } from 'lucide-react'
import { api } from '../../api/client'
import { Banner, Button, Checkbox, Field, Input, SearchSelect, SpinnerRing, type SearchSelectOption } from '../../common'
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from '../../common/Modal'
import {
  TablePicker, generateSql, suggestAlias, JOIN_TYPES,
  type PickedTable, type JoinTable, type JoinType, type JoinCond,
} from '../../common/SqlWizardModal'
import { buildCrudSql } from '../../common/sqlBuild'
import { SqlEditor } from '../../common/SqlEditor'
import { MENU_ICON_NAMES, iconFor } from '../../components/SidebarMenu'
import { ScanProposalsTable, summarizeProposals, type ScanProposal } from '../Settings/DictionaryScanTable'
import { getPoolSchemaTables, type PoolColumn } from '../../services/poolSchema'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

type ColId = string                                   // "ALIAS.col"
interface WizTab { id: string; label: string; cols: ColId[] }
// A catalog preset (from GET /admin/assistant/presets) — base table + optional joins.
interface PresetTable {
  schema?: string | null; name: string; alias?: string; join?: string; on?: Array<{ left: string; right: string }>
  // Fast path: describe an existing connector query (its result columns) instead of walking the DB
  // schema. ``query`` is the query name; ``query_connector`` defaults to the source connector.
  query?: string; query_connector?: string
}
interface Preset { id: string; label: string; description?: string; group?: string; connector?: string; tables: PresetTable[] }
const STEPS = ['target', 'tables', 'grid', 'columns', 'dictionary', 'menu', 'review'] as const
type StepKey = (typeof STEPS)[number]
const ICON_OPTS: SearchSelectOption[] = MENU_ICON_NAMES.map((n) => {
  const I = iconFor(n)
  return { value: n, label: n, icon: I ? <I size={15} /> : undefined }
})

// ── layout ───────────────────────────────────────────────────────────────────────────────
const HeadBar = styled.div`display: flex; align-items: center; gap: 10px; width: 100%;`
const Title = styled.span`font-size: ${fontSize.lg}; font-weight: 600; color: ${colors.text.primary}; display: flex; align-items: center; gap: 8px; flex: 1;`
const Train = styled.div`display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 12px;`
const Stop = styled.button<{ $state: 'done' | 'active' | 'todo' }>`
  display: flex; align-items: center; gap: 7px; padding: 6px 12px; border-radius: 999px; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; border: 1px solid;
  border-color: ${(p) => (p.$state === 'active' ? colors.blue.main : colors.border)};
  background: ${(p) => (p.$state === 'active' ? colors.blue.bg : p.$state === 'done' ? colors.bg.input : 'transparent')};
  color: ${(p) => (p.$state === 'todo' ? colors.text.muted : colors.text.primary)};
  & .n { display: inline-flex; align-items: center; justify-content: center; width: 18px; height: 18px; border-radius: 50%;
    font-size: ${fontSize.micro}; background: ${(p) => (p.$state === 'active' ? colors.blue.main : colors.bg.dropdown)};
    color: ${(p) => (p.$state === 'active' ? '#fff' : colors.text.muted)}; }
`
const Section = styled.div`display: flex; flex-direction: column; gap: 8px; margin-top: 12px; &:first-of-type { margin-top: 0; }`
const SectionTitle = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em;`
const RowBar = styled.div`display: flex; gap: 8px; align-items: center; flex-wrap: wrap;`
const SmallX = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border}; background: transparent; color: ${colors.text.muted}; cursor: pointer; flex-shrink: 0;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; }
`
const Mini = styled.button`
  display: inline-flex; align-items: center; gap: 4px; height: 28px; padding: 0 10px; border-radius: ${radius.sm};
  border: 1px dashed ${colors.border}; background: transparent; color: ${colors.text.muted}; font-size: ${fontSize.micro}; cursor: pointer;
  &:hover:not(:disabled) { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
  &:disabled { opacity: 0.5; cursor: default; }
`
const Mono = styled.span`font-family: ${fonts.mono};`
const Dual = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start;`
const Pane = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden; display: flex; flex-direction: column;`
const PaneHead = styled.div`padding: 8px 10px; background: ${colors.bg.input}; font-size: ${fontSize.micro}; text-transform: uppercase; letter-spacing: 0.04em; color: ${colors.text.muted}; display: flex; align-items: center; gap: 6px;`
const PaneList = styled.div`max-height: 300px; overflow-y: auto; display: flex; flex-direction: column;`
const ColRow = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 5px 10px; border-bottom: 1px solid ${colors.border};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  &:last-child { border-bottom: none; }
  & .grow { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 7px; }
  & .nm { flex-shrink: 0; }
  & .lbl { font-family: ${fonts.sans}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .k { display: inline-flex; align-items: center; gap: 3px; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const PaneGroup = styled.button`
  display: flex; align-items: center; gap: 5px; width: 100%; text-align: left; cursor: pointer;
  padding: 4px 10px; background: ${colors.bg.dropdown}; border: none; border-bottom: 1px solid ${colors.border};
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; position: sticky; top: 0; z-index: 1;
  & b { color: ${colors.text.secondary}; }
  & svg { flex-shrink: 0; }
  &:hover { color: ${colors.text.primary}; }
`
const SearchBox = styled.div`
  display: flex; align-items: center; gap: 6px; padding: 6px 10px; border-bottom: 1px solid ${colors.border};
  & > svg { color: ${colors.text.muted}; flex-shrink: 0; }
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; }
`
const Chip = styled.button<{ $active: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; cursor: pointer; font-size: ${fontSize.sm};
  border: 1px solid ${(p) => (p.$active ? colors.blue.main : colors.border)};
  background: ${(p) => (p.$active ? colors.blue.bg : 'transparent')}; color: ${(p) => (p.$active ? colors.blue.main : colors.text.primary)};
`
const Grid2 = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 12px;`
// Catalog picker
const CatGroup = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.05em; margin: 14px 0 6px; &:first-of-type { margin-top: 2px; }`
const PresetCard = styled.button`
  display: block; width: 100%; text-align: left; padding: 10px 12px; margin-bottom: 6px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.card}; cursor: pointer;
  &:hover { border-color: ${colors.blue.main}; background: ${colors.blue.bg}; }
  & .lbl { font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; }
  & .desc { font-size: ${fontSize.micro}; color: ${colors.text.secondary}; margin-top: 2px; }
  & .tbls { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; margin-top: 4px; }
`

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
const colName = (id: ColId) => (id.split('.').slice(1).join('.') || id).toUpperCase()   // bare column name
const DEFAULT_TABS: WizTab[] = [{ id: 'general', label: 'General', cols: [] }]
// "SY=01,RT=ST" → { SY: "01", RT: "ST" }
function parseParams(s: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of s.split(',')) { const [k, ...v] = part.split('='); if (k.trim()) out[k.trim()] = v.join('=').trim() }
  return out
}
// The left "available columns" pane shared by the Grid-view and Dialog-columns steps: columns
// grouped by source table, each group collapsible, with a search box across name + dictionary label.
function AvailablePane({ title, joins, ids, colLabel, onAdd, emptyText }: {
  title: string
  joins: JoinTable[]
  ids: ColId[]
  colLabel: (id: ColId) => string
  onAdd: (id: ColId) => void
  emptyText: string
}) {
  const { t } = useTranslation()
  const [q, setQ] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const needle = q.trim().toLowerCase()
  const match = (id: ColId) => !needle || colName(id).toLowerCase().includes(needle) || colLabel(id).toLowerCase().includes(needle)
  const toggle = (alias: string) => setCollapsed((s) => { const n = new Set(s); if (n.has(alias)) n.delete(alias); else n.add(alias); return n })
  const shown = ids.filter(match)
  return (
    <Pane>
      <PaneHead><ArrowRight size={12} /> {title} · {shown.length}{needle && shown.length !== ids.length ? `/${ids.length}` : ''}</PaneHead>
      <SearchBox>
        <Search size={13} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('assistant.colFilter', 'Filter columns…')} />
        {q && <SmallX type="button" style={{ width: 22, height: 22 }} onClick={() => setQ('')}><X size={12} /></SmallX>}
      </SearchBox>
      <PaneList>
        {joins.map((j) => {
          const avail = ids.filter((id) => id.startsWith(`${j.alias}.`) && match(id))
          if (avail.length === 0) return null
          const isCollapsed = collapsed.has(j.alias) && !needle   // a live search always expands matches
          return (
            <Fragment key={j.alias}>
              <PaneGroup type="button" onClick={() => toggle(j.alias)}>
                {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}<b>{j.name}</b> · {j.alias} · {avail.length}
              </PaneGroup>
              {!isCollapsed && avail.map((id) => (
                <ColRow key={id}>
                  <span className="grow"><span className="nm">{colName(id)}</span>{colLabel(id) && <span className="lbl">{colLabel(id)}</span>}</span>
                  <Mini type="button" onClick={() => onAdd(id)}><ArrowRight size={11} /></Mini>
                </ColRow>
              ))}
            </Fragment>
          )
        })}
        {shown.length === 0 && <ColRow><span className="grow" style={{ color: colors.text.muted }}>{needle ? t('assistant.noColMatch', 'No matching columns') : emptyText}</span></ColRow>}
      </PaneList>
    </Pane>
  )
}

// Resolve a preset's join ref ("ALIAS.COL") to a real introspected column id. A preset references
// columns by DATA ITEM (e.g. F03012.AN8), but JDE physical columns are prefixed (AIAN8 / ABAN8) — so
// match exact, else "drop the 2-char table prefix" (JDE), else endsWith. Falls back to the raw ref.
function resolveJoinRef(tables: JoinTable[], ref: string): string {
  const dot = ref.indexOf('.')
  if (dot < 0) return ref
  const alias = ref.slice(0, dot)
  const di = ref.slice(dot + 1).toUpperCase()
  const tbl = tables.find((t) => t.alias.toLowerCase() === alias.toLowerCase())
  if (!tbl) return ref
  const col = tbl.columns.find((c) => c.name.toUpperCase() === di)
    ?? tbl.columns.find((c) => c.name.length > 2 && c.name.slice(2).toUpperCase() === di)
    ?? tbl.columns.find((c) => c.name.toUpperCase().endsWith(di))
  return col ? `${tbl.alias}.${col.name}` : ref
}

export default function ScreenAssistant({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { apps, connectors: wsConnectors, refresh } = useWorkspace()
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)

  // Step 1 — target
  const [connectors, setConnectors] = useState<SearchSelectOption[]>([])
  const [connector, setConnector] = useState('')
  const [app, setApp] = useState('')

  // Step 2 — tables & joins. ``sourceMode`` switches the base between a physical DB table
  // (introspected) and an EXISTING connector query (reused as-is — no new query is written).
  const [sourceMode, setSourceMode] = useState<'table' | 'query'>('table')
  const [sourceQuery, setSourceQuery] = useState('')
  const [joins, setJoins] = useState<JoinTable[]>([])
  const [adding, setAdding] = useState(false)
  const [presets, setPresets] = useState<Preset[]>([])
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [catalogQ, setCatalogQ] = useState('')

  // Step 3 — grid columns (the table view). The columns shown in the list — picked explicitly (no
  // default selection). The dialog (step 4) may show MORE: any dialog column not picked here is kept
  // on the screen but written with ``hidden: true`` so it's editable in the dialog yet off the grid.
  const [gridCols, setGridCols] = useState<ColId[]>([])
  // Step 4 — columns → tabs (the dialog form). Optional: a screen with no dialog is a read-only grid.
  const [tabs, setTabs] = useState<WizTab[]>(DEFAULT_TABS)
  const [activeTab, setActiveTab] = useState('general')
  const [keys, setKeys] = useState<Set<ColId>>(new Set())
  // Dictionary labels (data item → description) so columns show their meaning, not just the code.
  const [ddLabels, setDdLabels] = useState<Map<string, string>>(new Map())

  // Step 4 — JDE dictionary
  const [isJde, setIsJde] = useState(false)
  const [ddItems, setDdItems] = useState<ScanProposal[] | null>(null)
  const [ddLoading, setDdLoading] = useState(false)
  const [fmtOpts, setFmtOpts] = useState<SearchSelectOption[]>([])
  const [ruleOpts, setRuleOpts] = useState<SearchSelectOption[]>([])

  // Step 5 — menu
  const [createMenu, setCreateMenu] = useState(true)
  const [menuLabel, setMenuLabel] = useState('')
  const [menuIcon, setMenuIcon] = useState('table')
  const [menuParent, setMenuParent] = useState('')
  const [menuItems, setMenuItems] = useState<SearchSelectOption[]>([])

  // Step 6 — names
  const [tableName, setTableName] = useState('')
  const [screenLabel, setScreenLabel] = useState('')
  const [result, setResult] = useState<{ screen: string } | null>(null)

  const appOptions = useMemo<SearchSelectOption[]>(() => (apps ?? []).map((a) => ({ value: a.name, label: a.name, mono: a.name })), [apps])

  useEffect(() => {
    void api.get<{ connectors: { name: string; type: string }[] }>('/api/connectors')
      .then((r) => setConnectors(r.connectors.filter((c) => c.type === 'sql').map((c) => ({ value: c.name, label: c.name, mono: c.name }))))
      .catch(() => undefined)
    // Dictionary format + rule dropdowns — the same framework enums the Dictionary editor uses.
    // Each enum is ``{ label, values: [{value, label}] }`` — read the inner ``values`` array.
    void api.get<{ framework_enums?: Record<string, { values?: Array<{ value: string; label?: string }> }> }>('/admin/config/schema')
      .then((r) => {
        const fe = r.framework_enums ?? {}
        const opts = (k: string): SearchSelectOption[] => (fe[k]?.values ?? []).map((o) => ({ value: o.value, label: o.label ?? o.value }))
        setFmtOpts(opts('DICTIONARY_TYPE'))
        setRuleOpts(opts('DICTIONARY_RULES'))
      }).catch(() => undefined)
  }, [])
  const base = joins[0] ?? null
  const assigned = useMemo(() => new Set(tabs.flatMap((tb) => tb.cols)), [tabs])
  const allCols = useMemo<ColId[]>(() => joins.flatMap((j) => j.columns.map((c) => `${j.alias}.${c.name}`)), [joins])
  const available = useMemo(() => allCols.filter((id) => !assigned.has(id)), [allCols, assigned])
  // The dialog columns in first-seen display order (used to seed the grid + as the editable set).
  const dialogOrder = useMemo<ColId[]>(() => {
    const seen = new Set<ColId>(); const out: ColId[] = []
    for (const tb of tabs) for (const id of tb.cols) if (!seen.has(id)) { seen.add(id); out.push(id) }
    return out
  }, [tabs])
  const gridAvailable = useMemo(() => allCols.filter((id) => !gridCols.includes(id)), [allCols, gridCols])
  const screenId = tableName ? slug(tableName) : ''
  // Generate the read query over ALL columns of the joined tables (not just the displayed subset) so
  // the same query is reusable by other screens that pick different columns. The screen narrows what's
  // shown via its ``columns`` list + per-column ``hidden``.
  const readSql = useMemo(() => generateSql(joins, new Set(allCols), [], []), [joins, allCols])
  // Existing read queries on the picked connector — the "Existing query" source picker. Drop the
  // writable CRUD slots (put/post/delete); only row-returning queries make sense as a read source.
  const existingQueries = useMemo<SearchSelectOption[]>(() => {
    const meta = (wsConnectors ?? []).find((c) => c.name === connector)
    if (!meta || meta.type !== 'sql') return []
    return (meta.queries ?? [])
      .filter((q) => !q.writable)
      .map((q) => ({ value: q.name, label: q.label || q.description || q.name, mono: q.name }))
  }, [wsConnectors, connector])

  // ── reset ──────────────────────────────────────────────────────────────────────────────
  const resetTables = () => { setJoins([]); setAdding(false); setSourceQuery(''); setTabs(DEFAULT_TABS); setActiveTab('general'); setKeys(new Set()); setGridCols([]); setDdItems(null) }
  const resetAll = () => {
    setStep(0); setMsg(null); setResult(null); setConnector(''); setApp('')
    setSourceMode('table'); resetTables(); setIsJde(false); setDdLoading(false)
    setCreateMenu(true); setMenuLabel(''); setMenuIcon('table'); setMenuParent(''); setTableName(''); setScreenLabel('')
  }

  // ── step 2: catalog presets ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!connector) { setPresets([]); return }
    void api.get<{ presets: Preset[] }>(`/admin/assistant/presets?connector=${encodeURIComponent(connector)}`)
      .then((r) => setPresets(r.presets)).catch(() => setPresets([]))
  }, [connector])

  // Dictionary labels for the chosen connector — data-item id → description. Used in the Columns
  // step so each column shows its meaning. Connector scope wins, then shared, then any other scope.
  useEffect(() => {
    if (!connector) { setDdLabels(new Map()); return }
    type Ent = Record<string, { label?: string }>
    void api.get<{ dictionary: { entries?: Ent; connectors?: Record<string, { entries?: Ent }> } }>('/admin/config/dictionary/parsed')
      .then((r) => {
        const m = new Map<string, string>()
        const add = (e?: Ent) => { for (const [k, v] of Object.entries(e ?? {})) { const key = k.toUpperCase(); if (v?.label && !m.has(key)) m.set(key, v.label) } }
        const d = r.dictionary
        add(d.connectors?.[connector]?.entries)
        add(d.entries)
        for (const sc of Object.values(d.connectors ?? {})) add(sc.entries)
        setDdLabels(m)
      }).catch(() => undefined)
  }, [connector])
  // The dictionary description for a column id — try the physical name, then the JDE-stripped item.
  const colLabel = useCallback((id: ColId) => {
    const nm = colName(id)
    return ddLabels.get(nm) ?? (nm.length > 2 ? ddLabels.get(nm.slice(2)) : undefined) ?? ''
  }, [ddLabels])
  // Apply a preset: introspect each table's live columns, wire the declared joins, reset downstream.
  const applyPreset = useCallback(async (id: string) => {
    const p = presets.find((x) => x.id === id)
    if (!p) return
    setBusy(true); setMsg(null)
    try {
      // A query-backed preset (a single existing query on this connector) REUSES that query as-is —
      // no new query is generated. This is the case for presets whose query ships embedded in the
      // package: creating from the catalog (even twice) just points the screen at the existing
      // query instead of trying to write a duplicate (which would collide). Same path as the
      // "Existing query" source mode.
      const only = p.tables.length === 1 ? p.tables[0] : null
      if (only?.query && (!only.query_connector || only.query_connector === connector)) {
        const r = await api.get<{ columns?: Array<{ name: string; type?: string | null }> }>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(only.query)}?_limit=0`)
        const cols = (r.columns ?? []).map((c) => ({ name: c.name, type: c.type ?? undefined }))
        if (!cols.length) { setMsg({ tone: 'err', text: t('assistant.queryNoCols', 'That query returned no columns.') }); return }
        setSourceMode('query'); setSourceQuery(only.query)
        setJoins([{ schema: null, name: only.query, alias: suggestAlias(only.query, new Set()), columns: cols }])
        setAdding(false); setTabs(DEFAULT_TABS); setActiveTab('general'); setKeys(new Set()); setGridCols([]); setDdItems([])
        if (!tableName) setTableName(slug(p.id))
        return
      }
      // Resolve each table's columns — describe an existing connector query when the preset names one
      // (fast: one cheap ``_limit=0`` describe, no schema walk), else introspect the pool schema.
      setSourceMode('table')
      const built: JoinTable[] = []
      const taken = new Set<string>()
      for (const pt of p.tables) {
        let cols: PoolColumn[] | null = null
        let name = pt.name
        let schema = pt.schema ?? null
        if (pt.query) {
          try {
            const qc = pt.query_connector || connector
            const r = await api.get<{ columns?: Array<{ name: string; type?: string | null }> }>(`/api/sql/${encodeURIComponent(qc)}/${encodeURIComponent(pt.query)}?_limit=0`)
            cols = (r.columns ?? []).map((c) => ({ name: c.name, type: c.type ?? undefined }))
          } catch { cols = null }
        }
        if (!cols || cols.length === 0) {
          const part = await getPoolSchemaTables(connector, pt.schema ?? null, pt.name)
          const tb = (part?.tables ?? []).find((x) => x.name.toLowerCase() === pt.name.toLowerCase())
          if (!tb) { setMsg({ tone: 'err', text: t('assistant.presetMiss', { name: pt.name, defaultValue: 'Table {{name}} not found on this connector.' }) }); continue }
          cols = tb.columns; name = tb.name; schema = pt.schema ?? tb.schema ?? null
        }
        const alias = pt.alias || suggestAlias(name, taken)
        taken.add(alias.toLowerCase())
        built.push({ schema, name, alias, columns: cols })
      }
      // Then wire the joins, resolving each ON ref (data item → physical column).
      for (let i = 1; i < built.length && i < p.tables.length; i++) {
        const pt = p.tables[i]
        built[i].join = {
          type: (pt.join as JoinType) ?? 'INNER',
          conds: (pt.on ?? [{ left: '', right: '' }]).map((c) => ({
            left: resolveJoinRef(built, c.left), right: resolveJoinRef(built, c.right),
          })),
        }
      }
      if (built.length) {
        setJoins(built); setAdding(false)
        setTabs(DEFAULT_TABS); setActiveTab('general'); setKeys(new Set()); setGridCols([]); setDdItems(null)
        if (!tableName) setTableName(slug(p.id))
      }
    } finally { setBusy(false) }
  }, [presets, connector, tableName, t])

  // Catalog browser — filter by query, group by ``group``, sort groups + presets alphabetically.
  const catalogGroups = useMemo<Array<[string, Preset[]]>>(() => {
    const q = catalogQ.trim().toLowerCase()
    const match = (p: Preset) => !q || [p.label, p.id, p.description ?? '', ...p.tables.map((tb) => tb.name)].some((s) => s.toLowerCase().includes(q))
    const m = new Map<string, Preset[]>()
    for (const p of presets) {
      if (!match(p)) continue
      const g = p.group || t('assistant.presetOther', 'Other')
      const list = m.get(g) ?? []; list.push(p); m.set(g, list)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
      .map(([g, list]) => [g, list.sort((a, b) => a.label.localeCompare(b.label))] as [string, Preset[]])
  }, [presets, catalogQ, t])

  // ── step 2 helpers (base + joins) ──────────────────────────────────────────────────────
  const pickBase = (tbl: PickedTable) => {
    const alias = suggestAlias(tbl.name, new Set())
    setJoins([{ schema: tbl.schema, name: tbl.name, alias, columns: tbl.columns }])
    if (!tableName) setTableName(slug(tbl.name))
  }
  // Pick an EXISTING connector query as the base: describe its result columns (cheap _limit=0),
  // then the column / tab / menu steps work as usual. The screen reuses this query as its
  // read_query — no new query is generated (and the dictionary scan is skipped: it isn't a table).
  const pickQuery = useCallback(async (qname: string) => {
    if (!qname) return
    setBusy(true); setMsg(null)
    try {
      const r = await api.get<{ columns?: Array<{ name: string; type?: string | null }> }>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(qname)}?_limit=0`)
      const cols = (r.columns ?? []).map((c) => ({ name: c.name, type: c.type ?? undefined }))
      if (!cols.length) { setMsg({ tone: 'err', text: t('assistant.queryNoCols', 'That query returned no columns.') }); return }
      setJoins([{ schema: null, name: qname, alias: suggestAlias(qname, new Set()), columns: cols }])
      setSourceQuery(qname)
      setTabs(DEFAULT_TABS); setActiveTab('general'); setKeys(new Set()); setGridCols([]); setDdItems([])
      if (!tableName) setTableName(slug(qname.replace(/_get$/i, '')))
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }, [connector, tableName, t])
  const addJoin = (tbl: PickedTable) => {
    setAdding(false)
    const taken = new Set(joins.map((j) => j.alias.toUpperCase()))
    const alias = suggestAlias(tbl.name, taken)
    setJoins((js) => [...js, { schema: tbl.schema, name: tbl.name, alias, columns: tbl.columns, join: { type: 'INNER', conds: [{ left: '', right: '' }] } }])
  }
  const removeJoin = (idx: number) => {
    const a = joins[idx]?.alias
    setJoins((js) => js.filter((_, i) => i !== idx))
    if (a) { setTabs((ts) => ts.map((tb) => ({ ...tb, cols: tb.cols.filter((id) => !id.startsWith(`${a}.`)) }))); setKeys((s) => new Set([...s].filter((id) => !id.startsWith(`${a}.`)))) }
  }
  const setJoinType = (idx: number, type: JoinType) =>
    setJoins((js) => js.map((j, i) => i === idx ? { ...j, join: { type, conds: j.join?.conds ?? [{ left: '', right: '' }] } } : j))
  const setConds = (idx: number, conds: JoinCond[]) =>
    setJoins((js) => js.map((j, i) => i === idx ? { ...j, join: { type: j.join?.type ?? 'INNER', conds } } : j))
  const qualOpts = (list: JoinTable[]): SearchSelectOption[] =>
    list.flatMap((j) => j.columns.map((c) => ({ value: `${j.alias}.${c.name}`, label: `${j.alias}.${c.name.toUpperCase()}`, mono: `${j.alias}.${c.name.toUpperCase()}` })))

  // ── step 4 helpers (columns → tabs, the dialog) ────────────────────────────────────────
  const moveToTab = (id: ColId) => setTabs((ts) => ts.map((tb) => tb.id === activeTab
    ? { ...tb, cols: tb.cols.includes(id) ? tb.cols : [...tb.cols, id] } : { ...tb, cols: tb.cols.filter((c) => c !== id) }))
  const removeFromTab = (id: ColId) => setTabs((ts) => ts.map((tb) => ({ ...tb, cols: tb.cols.filter((c) => c !== id) })))
  const addTab = () => { const n = tabs.length + 1; const id = `tab${n}`; setTabs((ts) => [...ts, { id, label: `Tab ${n}`, cols: [] }]); setActiveTab(id) }
  const renameTab = (id: string, label: string) => setTabs((ts) => ts.map((tb) => tb.id === id ? { ...tb, label } : tb))
  const removeTab = (id: string) => setTabs((ts) => {
    if (ts.length === 1) return ts
    const next = ts.filter((tb) => tb.id !== id)
    if (activeTab === id) setActiveTab(next[0].id)
    return next
  })
  const toggleKey = (id: ColId) => setKeys((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  const addAllToTab = () => setTabs((ts) => ts.map((tb) => tb.id === activeTab ? { ...tb, cols: [...tb.cols, ...available.filter((id) => !tb.cols.includes(id))] } : tb))
  const removeAllFromTab = () => setTabs((ts) => ts.map((tb) => tb.id === activeTab ? { ...tb, cols: [] } : tb))

  // ── step 3 helpers (grid columns) — no default selection; the operator picks explicitly ────────
  const addToGrid = (id: ColId) => setGridCols((g) => (g.includes(id) ? g : [...g, id]))
  const removeFromGrid = (id: ColId) => setGridCols((g) => g.filter((x) => x !== id))
  const addAllToGrid = () => setGridCols((g) => [...g, ...allCols.filter((id) => !g.includes(id))])
  const removeAllFromGrid = () => setGridCols([])

  // ── step 5: parent-menu options for the chosen app, as a TREE ──────────────────────────
  // The menu is a hierarchy; a flat alphabetical list makes a child sit next to a top-level item
  // and hides where the new entry would land. Emit items depth-first in their file order, tagged
  // with ``depth`` so the picker indents children under their parent (matching the sidebar).
  const appKey = slug(app) || connector
  useEffect(() => {
    if (STEPS[step] !== 'menu' || !appKey) return
    type RawItem = { id: string; label?: string; parent?: string | null }
    void api.get<{ menus: Record<string, { items?: RawItem[] }> }>('/admin/config/menus/parsed')
      .then((r) => {
        const items = r.menus[appKey]?.items ?? []
        const byParent = new Map<string, RawItem[]>()
        for (const it of items) {
          const p = it.parent || ''
          if (!byParent.has(p)) byParent.set(p, [])
          byParent.get(p)!.push(it)
        }
        const out: SearchSelectOption[] = []
        const emitted = new Set<string>()
        const walk = (parentId: string, depth: number) => {
          for (const it of byParent.get(parentId) ?? []) {
            if (emitted.has(it.id)) continue   // guard against a cyclic parent ref
            emitted.add(it.id)
            out.push({ value: it.id, label: it.label ? `${it.label} (${it.id})` : it.id, mono: it.id, depth })
            walk(it.id, depth + 1)
          }
        }
        walk('', 0)
        // Append any orphans (parent points at a missing item) so nothing is silently dropped.
        for (const it of items) if (!emitted.has(it.id)) out.push({ value: it.id, label: it.label ? `${it.label} (${it.id})` : it.id, mono: it.id })
        setMenuItems(out)
      }).catch(() => setMenuItems([]))
  }, [step, appKey])

  // ── step 4: run the JDE scan on the base table ─────────────────────────────────────────
  const runScan = useCallback(async () => {
    if (!base) return
    setDdLoading(true); setMsg(null)
    try {
      // Scope the scan to the target APP (same as the Dictionary editor's "Store under" picker), not
      // the source connector — JDE entries often live under an app scope (e.g. nomajde) that uses the
      // jdedwards pool, so scoping by connector would miss them and re-propose every column.
      const r = await api.post<{ jde: boolean; items: Array<Record<string, unknown>> }>('/admin/dictionary/scan', {
        connector, table: base.name, db_schema: base.schema, scope: appKey,
      })
      setIsJde(!!r.jde)
      setDdItems(r.items.map((it): ScanProposal => ({
        column: String(it.column ?? ''), dd_id: String(it.dd_id ?? it.column ?? ''),
        exists: !!it.exists, keep: !it.exists, type: (it.type as string | null) ?? null,
        source: (it.source as 'jde' | 'inferred' | undefined),
        label: String(it.label ?? ''), format: String(it.format ?? 'text'),
        justify: String(it.justify ?? ''), size: (it.size as number | null) ?? null,
        rules: String(it.rules ?? ''), rules_values: String(it.rules_values ?? ''), default: String(it.default ?? ''),
        // The scan returns lookup_params as { SY: "01", RT: "ST" } for UDC columns — flatten to the
        // "SY=01,RT=ST" the input edits.
        lookup_params: Object.entries((it.lookup_params as Record<string, string> | undefined) ?? {}).map(([k, v]) => `${k}=${v}`).join(','),
      })))
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }); setDdItems([]) }
    finally { setDdLoading(false) }
  }, [base, connector, appKey])
  // Changing the target app (= the dictionary scope) or the base table invalidates a prior scan, so
  // re-entering the Dictionary step re-scans against the right scope (otherwise a scan taken while the
  // app still defaulted to the connector would wrongly report every column missing).
  useEffect(() => { setDdItems(null) }, [appKey, base?.name, base?.schema])
  useEffect(() => { if (STEPS[step] === 'dictionary' && ddItems === null && base && !sourceQuery) void runScan() }, [step, ddItems, base, runScan, sourceQuery])

  const patchDd = (col: string, patch: Partial<ScanProposal>) =>
    setDdItems((items) => (items ?? []).map((d) => d.column === col ? { ...d, ...patch } : d))
  const ddByCol = useMemo(() => {
    const m = new Map<string, ScanProposal>()
    for (const d of ddItems ?? []) m.set(d.column.toUpperCase(), d)
    return m
  }, [ddItems])

  // ── assemble + submit ──────────────────────────────────────────────────────────────────
  // The screen's columns are ALL the columns the read query returns (the dialog shows every one;
  // the grid view selects which are visible). Per-column dd + key ride here.
  const screenColumns = useMemo(() => {
    const seen = new Set<string>()
    const out: Array<{ name: string; dd?: string; key?: boolean }> = []
    for (const id of allCols) {
      const nm = colName(id)
      if (seen.has(nm)) continue
      seen.add(nm)
      const dd = ddByCol.get(nm)
      out.push({ name: nm, ...(dd && dd.keep ? { dd: dd.dd_id } : {}), ...(keys.has(id) ? { key: true } : {}) })
    }
    return out
  }, [allCols, keys, ddByCol])
  // The grid selection becomes a default shared VIEW (the columns enabled in the grid, in order) —
  // NOT a subset of screen.columns. Omitting columns from a view hides them in the grid; the screen
  // still carries them all (and the dialog shows them all). Empty selection → no view.
  const gridView = useMemo(() => {
    const seen = new Set<string>(); const cols: string[] = []
    for (const id of gridCols) { const nm = colName(id); if (!seen.has(nm)) { seen.add(nm); cols.push(nm) } }
    return cols.length ? { name: 'Default', default: true, columns: cols } : null
  }, [gridCols])

  const baseColNames = useMemo(() => new Set((base?.columns ?? []).map((c) => c.name.toUpperCase())), [base])
  const submit = async () => {
    if (!base) return
    setBusy(true); setMsg(null)
    // CRUD is over the EDITABLE (dialog) columns — grid-only display columns aren't inserted/updated.
    const assignedBase = dialogOrder.map(colName).filter((n) => baseColNames.has(n))
    const keyBase = dialogOrder.filter((id) => keys.has(id)).map(colName).filter((n) => baseColNames.has(n))
    const crud = (kind: 'put' | 'post' | 'delete') =>
      buildCrudSql({ crud: kind, schema: base.schema, table: base.name, selectCols: assignedBase, keyCols: keyBase }) || undefined
    const kept = (ddItems ?? []).filter((d) => d.keep && !d.exists)
    // The dialog is OPTIONAL — a screen with no populated tab is a read-only grid (no dialog block,
    // no CRUD queries). Build the tabs once and only attach a ``dialog`` when there's something in it.
    const dialogTabs = tabs.filter((tb) => tb.cols.length).map((tb) => ({ id: tb.id, label: tb.label, type: 'form', fields: tb.cols.map((id) => ({ name: colName(id) })) }))
    const hasDialog = dialogTabs.length > 0
    const dialogBlock = hasDialog ? { dialog: { title: screenLabel || tableName, tabs: dialogTabs } } : {}
    // Two modes: a brand-new table (generate get/put/post/delete + dictionary) or an EXISTING
    // connector query reused as the read source (no new query, no dictionary scan, read-only screen).
    const payload = {
      connector, app: appKey,
      table: sourceQuery ? null : {
        name: slug(tableName), label: screenLabel || undefined,
        get_sql: readSql,
        put_sql: keyBase.length ? crud('put') : undefined,
        post_sql: assignedBase.length ? crud('post') : undefined,
        delete_sql: keyBase.length ? crud('delete') : undefined,
      },
      dictionary_scope: appKey,
      dictionary: sourceQuery ? [] : kept.map((d) => ({
        id: d.dd_id, label: d.label || undefined, format: d.format || undefined,
        ...(d.justify ? { justify: d.justify } : {}), ...(d.size != null ? { size: d.size } : {}),
        ...(d.rules ? { rules: d.rules } : {}), ...(d.rules_values ? { rules_values: d.rules_values } : {}),
        ...(d.lookup_params.trim() ? { lookup_params: parseParams(d.lookup_params) } : {}),
      })),
      screen: sourceQuery ? {
        id: screenId, label: screenLabel || tableName, connector,
        read_query: sourceQuery, read_only: true, auto_load: true,
        columns: screenColumns,
        ...(gridView ? { views: [gridView] } : {}),
        ...dialogBlock,
      } : {
        id: screenId, label: screenLabel || tableName, connector,
        read_query: `${slug(tableName)}_get`, auto_load: true,
        ...(hasDialog ? {} : { read_only: true }),
        ...(assignedBase.length ? { insert_query: `${slug(tableName)}_post` } : {}),
        ...(keyBase.length ? { update_query: `${slug(tableName)}_put`, delete_query: `${slug(tableName)}_delete` } : {}),
        columns: screenColumns,
        ...(gridView ? { views: [gridView] } : {}),
        ...dialogBlock,
      },
      menu: createMenu ? {
        app: appKey, id: screenId, label: menuLabel || screenLabel || tableName,
        icon: menuIcon || undefined, parent: menuParent || undefined, target: screenId, connector,
      } : null,
    }
    try {
      const r = await api.post<{ created: { screen: string } }>('/admin/assistant/scaffold', payload)
      // The backend reloaded its config, but the client's workspace (connectors / screens / menus)
      // is still the pre-scaffold snapshot — opening the new screen against it hangs (its connector
      // query / screen meta isn't known yet). Refresh the workspace so the new screen is resolvable
      // without a manual browser reload.
      refresh()
      setResult({ screen: r.created.screen })
      setMsg({ tone: 'ok', text: t('assistant.created', { screen: r.created.screen, defaultValue: 'Created {{screen}} and reloaded.' }) })
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }

  // ── per-step gating ────────────────────────────────────────────────────────────────────
  const joinsReady = joins.length > 0 && joins.slice(1).every((j) => (j.join?.conds ?? []).some((c) => c.left && c.right) || (j.join?.raw ?? '').trim())
  const canNext = (
    STEPS[step] === 'target' ? !!connector && !!app.trim()
      : STEPS[step] === 'tables' ? !!base && joinsReady
        : STEPS[step] === 'grid' ? gridCols.length > 0
          : true   // 'columns' (dialog) is optional — a screen with no dialog is a read-only grid
  )
  const stepState = (i: number): 'done' | 'active' | 'todo' => (i < step ? 'done' : i === step ? 'active' : 'todo')
  const stepLabel = (k: StepKey) => t(`assistant.step.${k}`, {
    target: 'Target', tables: 'Tables & joins', columns: 'Dialog columns', grid: 'Grid view', dictionary: 'Dictionary', menu: 'Menu', review: 'Review & create',
  }[k])
  const activeWizTab = tabs.find((tb) => tb.id === activeTab)

  return createPortal(
    <>
    {/* No click-outside / Esc close — those wiped a half-built screen by accident. Close is explicit
        only: the header ✕ or the footer Cancel. The backdrop still dims, it just doesn't dismiss. */}
    <Overlay style={{ zIndex: 1000 }}>
      <Modal style={{ width: 'min(1120px, 96vw)', height: 'min(840px, 92vh)', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          <HeadBar>
            <Title><Wand2 size={18} /> {t('assistant.title', 'Screen Creation Assistant')}</Title>
            <Button $size="sm" $variant="ghost" onClick={resetAll} title={t('assistant.reset', 'Reset')}><RotateCcw size={14} /></Button>
            <Button $size="sm" $variant="ghost" onClick={onClose} title={t('common.close', 'Close')}><X size={16} /></Button>
          </HeadBar>
        </ModalHeader>
        <ModalBody style={{ flex: 1, overflow: 'auto' }}>
          <Train>
            {STEPS.map((k, i) => (
              <Stop key={k} $state={stepState(i)} onClick={() => i <= step && setStep(i)} disabled={i > step}>
                <span className="n">{stepState(i) === 'done' ? <Check size={11} /> : i + 1}</span>{stepLabel(k)}
                {i < STEPS.length - 1 && <ChevronRight size={13} style={{ opacity: 0.5 }} />}
              </Stop>
            ))}
          </Train>
          {msg && <Banner $tone={msg.tone === 'ok' ? 'ok' : 'error'}>{msg.text}</Banner>}

          {/* ── Step 1: target ── */}
          {STEPS[step] === 'target' && (
            <Grid2>
              <Field label={t('assistant.connector', 'Source connector')}>
                <SearchSelect value={connector} onChange={(v) => { setConnector(v); if (!app) setApp(v) }} options={connectors} placeholder={t('common.pick', 'Pick…')} />
              </Field>
              <Field label={t('assistant.app', 'App (for the screen + menu)')}>
                <SearchSelect value={app} onChange={setApp} options={appOptions} allowCustom placeholder={t('assistant.appPick', 'Pick an app or type a new one')} />
              </Field>
            </Grid2>
          )}

          {/* ── Step 2: tables & joins ── */}
          {STEPS[step] === 'tables' && (
            <>
              {/* Source: a brand-new DB table (generates CRUD queries) or an existing connector query. */}
              <RowBar style={{ marginBottom: 4 }}>
                <Chip $active={sourceMode === 'table'} onClick={() => { if (sourceMode !== 'table') { resetTables(); setSourceMode('table') } }}>
                  {t('assistant.srcTable', 'Database table')}
                </Chip>
                <Chip $active={sourceMode === 'query'} onClick={() => { if (sourceMode !== 'query') { resetTables(); setSourceMode('query') } }}>
                  {t('assistant.srcQuery', 'Existing query')}
                </Chip>
              </RowBar>

              {sourceMode === 'query' ? (
                !base ? (
                  <Field label={t('assistant.existingQuery', 'Existing connector query')}>
                    {existingQueries.length
                      ? <SearchSelect value="" onChange={(v) => void pickQuery(v)} options={existingQueries} placeholder={t('common.pick', 'Pick…')} />
                      : <div style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('assistant.noQueries', 'This connector has no read queries.')}</div>}
                  </Field>
                ) : (
                  <Section>
                    <SectionTitle>{t('assistant.source', 'Source')}</SectionTitle>
                    <RowBar>
                      <strong style={{ fontFamily: fonts.mono, fontSize: fontSize.sm, flex: 1 }}>{sourceQuery}</strong>
                      <SmallX type="button" onClick={resetTables} title={t('common.remove', 'Remove')}><X size={13} /></SmallX>
                    </RowBar>
                  </Section>
                )
              ) : (
              <>
              {presets.length > 0 && (
                <Section>
                  <SectionTitle>{t('assistant.preset', 'Start from a catalog preset')}</SectionTitle>
                  <Button $size="sm" $variant="ghost" onClick={() => { setCatalogQ(''); setCatalogOpen(true) }} style={{ alignSelf: 'flex-start' }}>
                    <BookOpen size={14} /> {t('assistant.browseCatalog', 'Browse catalog ({{n}})', { n: presets.length })}
                  </Button>
                </Section>
              )}
              {!base ? (
                <Field label={t('assistant.baseTable', 'Base table')}><TablePicker connector={connector} onPick={pickBase} /></Field>
              ) : (
                <Section>
                  <SectionTitle>{t('assistant.tables', 'Tables')}</SectionTitle>
                  {joins.map((j, i) => (
                    <div key={`${j.alias}_${i}`}>
                      <RowBar>
                        {i > 0 && (
                          <div style={{ flex: '0 0 130px' }}>
                            <SearchSelect value={j.join?.type ?? 'INNER'} onChange={(v) => setJoinType(i, v as JoinType)} options={JOIN_TYPES.map((x) => ({ value: x, label: `${x} JOIN` }))} />
                          </div>
                        )}
                        <strong style={{ fontFamily: fonts.mono, fontSize: fontSize.sm, flex: 1 }}>
                          {(j.schema ? `${j.schema}.` : '') + j.name}<span style={{ color: colors.text.muted, marginLeft: 6 }}>{j.alias}</span>
                        </strong>
                        <SmallX type="button" onClick={() => (i === 0 ? resetTables() : removeJoin(i))}
                          title={i === 0 ? t('assistant.changeBase', 'Remove base table') : t('common.remove', 'Remove')}><X size={13} /></SmallX>
                      </RowBar>
                      {i > 0 && (() => {
                        const conds = j.join?.conds ?? [{ left: '', right: '' }]
                        const leftOpts = qualOpts(joins.slice(0, i)); const rightOpts = qualOpts([j])
                        return (
                          <div style={{ marginTop: 4 }}>
                            {conds.map((c, k) => (
                              <RowBar key={k} style={{ marginTop: 4 }}>
                                <div style={{ flex: 1 }}>
                                  <SearchSelect value={c.left} onChange={(v) => setConds(i, conds.map((x, ci) => ci === k ? { ...x, left: v } : x))} options={leftOpts} placeholder={t('assistant.joinSource', 'Source column')} />
                                </div>
                                <span style={{ color: colors.text.muted }}>=</span>
                                <div style={{ flex: 1 }}>
                                  <SearchSelect value={c.right} onChange={(v) => setConds(i, conds.map((x, ci) => ci === k ? { ...x, right: v } : x))} options={rightOpts} placeholder={t('assistant.joinTarget', 'Target column')} />
                                </div>
                                {conds.length > 1 && <SmallX type="button" onClick={() => setConds(i, conds.filter((_, ci) => ci !== k))}><X size={13} /></SmallX>}
                              </RowBar>
                            ))}
                            <Mini type="button" onClick={() => setConds(i, [...conds, { left: '', right: '' }])}><Plus size={12} /> {t('assistant.addCond', 'Add condition')}</Mini>
                          </div>
                        )
                      })()}
                    </div>
                  ))}
                  {adding ? <TablePicker connector={connector} onPick={addJoin} />
                    : <Mini type="button" onClick={() => setAdding(true)}><Plus size={12} /> {t('assistant.addJoin', 'Add a joined table')}</Mini>}
                </Section>
              )}
              </>
              )}
            </>
          )}

          {/* ── Step 4: columns → tabs (the dialog — optional) ── */}
          {STEPS[step] === 'columns' && (
            <>
              <div style={{ fontSize: fontSize.sm, color: colors.text.muted, marginBottom: 4 }}>
                {t('assistant.dialogOptional', 'Optional — arrange columns into dialog tabs for an editable form. Leave empty for a read-only grid. The dialog can show more columns than the grid.')}
              </div>
              <RowBar style={{ marginBottom: 4 }}>
                {tabs.map((tb) => (
                  <Chip key={tb.id} $active={tb.id === activeTab} onClick={() => setActiveTab(tb.id)}>
                    {tb.label}<span style={{ color: colors.text.muted }}>{tb.cols.length}</span>
                    {tabs.length > 1 && <X size={11} onClick={(e) => { e.stopPropagation(); removeTab(tb.id) }} />}
                  </Chip>
                ))}
                <Mini type="button" onClick={addTab}><Plus size={12} /> {t('assistant.addTab', 'Add tab')}</Mini>
              </RowBar>
              <RowBar>
                <Input style={{ flex: '0 0 240px' }} value={activeWizTab?.label ?? ''} onChange={(e) => renameTab(activeTab, e.target.value)} placeholder={t('assistant.tabLabel', 'Active tab label')} />
                <Mini type="button" onClick={addAllToTab} disabled={available.length === 0}>{t('assistant.addAll', 'Add all available →')}</Mini>
                <Mini type="button" onClick={removeAllFromTab} disabled={(activeWizTab?.cols.length ?? 0) === 0}>{t('assistant.removeAll', '← Remove all')}</Mini>
              </RowBar>
              <Dual>
                <AvailablePane title={t('assistant.available', 'Available columns')} joins={joins} ids={available}
                  colLabel={colLabel} onAdd={moveToTab} emptyText={t('assistant.allAssigned', 'All columns assigned')} />
                <Pane>
                  <PaneHead><ArrowLeft size={12} /> {activeWizTab?.label} · {activeWizTab?.cols.length ?? 0}</PaneHead>
                  <PaneList>
                    {(activeWizTab?.cols ?? []).map((id) => (
                      <ColRow key={id}>
                        <span className="grow"><span className="nm">{colName(id)}</span>{colLabel(id) && <span className="lbl">{colLabel(id)}</span>}</span>
                        <span className="k"><Checkbox checked={keys.has(id)} onChange={() => toggleKey(id)} /><KeyRound size={11} /></span>
                        <Mini type="button" onClick={() => removeFromTab(id)}><X size={11} /></Mini>
                      </ColRow>
                    ))}
                  </PaneList>
                </Pane>
              </Dual>
            </>
          )}

          {/* ── Step 3: grid view (the default view's enabled columns) ── */}
          {STEPS[step] === 'grid' && (
            <>
              <RowBar>
                <div style={{ flex: 1, fontSize: fontSize.sm, color: colors.text.muted }}>
                  {t('assistant.gridIntro', 'Pick the columns enabled in the default grid view. The screen keeps every query column (and the dialog shows them all); the view just sets which are visible — users can add the rest from the grid’s column menu.')}
                </div>
                <Mini type="button" onClick={addAllToGrid} disabled={gridAvailable.length === 0}>{t('assistant.addAll', 'Add all available →')}</Mini>
                <Mini type="button" onClick={removeAllFromGrid} disabled={gridCols.length === 0}>{t('assistant.removeAll', '← Remove all')}</Mini>
              </RowBar>
              <Dual>
                <AvailablePane title={t('assistant.gridHidden', 'Not in the view')} joins={joins} ids={gridAvailable}
                  colLabel={colLabel} onAdd={addToGrid} emptyText={t('assistant.allInGrid', 'Every column is in the view')} />
                <Pane>
                  <PaneHead><ArrowLeft size={12} /> {t('assistant.gridShown', 'In the view')} · {gridCols.length}</PaneHead>
                  <PaneList>
                    {gridCols.map((id) => (
                      <ColRow key={id}>
                        <span className="grow"><span className="nm">{colName(id)}</span>{colLabel(id) && <span className="lbl">{colLabel(id)}</span>}</span>
                        <Mini type="button" onClick={() => removeFromGrid(id)}><X size={11} /></Mini>
                      </ColRow>
                    ))}
                    {gridCols.length === 0 && <ColRow><span className="grow" style={{ color: colors.text.muted }}>{t('assistant.gridEmpty', 'No columns in the view yet')}</span></ColRow>}
                  </PaneList>
                </Pane>
              </Dual>
            </>
          )}

          {/* ── Step 5: JDE dictionary ── */}
          {STEPS[step] === 'dictionary' && (
            sourceQuery ? (
              <Banner $tone="info">{t('assistant.ddSkipQuery', 'Reusing an existing query — its columns already carry their dictionary entries, so there is nothing to propose here.')}</Banner>
            ) : ddLoading ? <SpinnerRing /> : (
              <>
                <RowBar>
                  <SectionTitle style={{ flex: 1 }}>
                    {t('assistant.ddProposals', 'Proposed dictionary entries (base table)')} · {t('settings.dictscan.summary2', '{{missing}} of {{total}} missing', summarizeProposals(ddItems ?? []))} · {t('assistant.ddScope', 'scope “{{scope}}”', { scope: appKey })}
                  </SectionTitle>
                  <Mini type="button" onClick={() => void runScan()}>{t('assistant.rescan', 'Re-scan')}</Mini>
                </RowBar>
                {!isJde && <Banner $tone="info">{t('assistant.notJde', 'This pool is not JDE/Oracle — dictionary entries are inferred from column names. Review or skip.')}</Banner>}
                <ScanProposalsTable items={ddItems ?? []} jde={isJde} fmtOpts={fmtOpts} ruleOpts={ruleOpts} onPatch={patchDd} />
              </>
            )
          )}

          {/* ── Step 5: menu placement ── */}
          {STEPS[step] === 'menu' && (
            <>
              <RowBar><Checkbox checked={createMenu} onChange={() => setCreateMenu((v) => !v)} label={t('assistant.createMenu', 'Add a menu item under this app')} /></RowBar>
              {createMenu && (
                <Grid2>
                  <Field label={t('assistant.menuLabel', 'Menu label')}>
                    <Input value={menuLabel} onChange={(e) => setMenuLabel(e.target.value)} placeholder={screenLabel || t('assistant.menuLabelPh', 'shown in the sidebar')} />
                  </Field>
                  <Field label={t('assistant.menuParent', 'Parent menu (optional)')}>
                    <SearchSelect value={menuParent} onChange={setMenuParent} options={menuItems} anyLabel={t('assistant.menuTop', '(top level)')}
                      placeholder={menuItems.length ? t('assistant.menuParentPh', 'Nest under…') : t('assistant.menuNoParents', 'No existing items — top level')} />
                  </Field>
                  <Field label={t('assistant.menuIcon', 'Icon')}>
                    <SearchSelect value={menuIcon} onChange={setMenuIcon} options={ICON_OPTS} anyLabel={t('assistant.menuNoIcon', '(none)')} placeholder={t('assistant.menuIconPh', 'Pick an icon')} />
                  </Field>
                </Grid2>
              )}
            </>
          )}

          {/* ── Step 6: review & create ── */}
          {STEPS[step] === 'review' && (
            result ? (
              <Section>
                <Banner $tone="ok">{t('assistant.done', 'Screen {{screen}} created and live.', { screen: result.screen })}</Banner>
                <RowBar>
                  <Button $variant="primary" onClick={() => { navigate(`/screen/${appKey}/${screenId}`); onClose() }}>{t('assistant.openScreen', 'Open the new screen')}</Button>
                  <Button $variant="ghost" onClick={resetAll}>{t('assistant.again', 'Create another')}</Button>
                  <Button $variant="ghost" onClick={onClose}>{t('common.close', 'Close')}</Button>
                </RowBar>
              </Section>
            ) : (
              <>
                <Grid2>
                  <Field label={t('assistant.tableName', 'Table / query name')}><Input value={tableName} onChange={(e) => setTableName(e.target.value)} placeholder="address_book" /></Field>
                  <Field label={t('assistant.screenLabel', 'Screen label')}><Input value={screenLabel} onChange={(e) => setScreenLabel(e.target.value)} placeholder="Address Book" /></Field>
                </Grid2>
                <Section>
                  <SectionTitle>{t('assistant.summary', 'Will create')}</SectionTitle>
                  <div style={{ fontSize: fontSize.sm, color: colors.text.secondary, lineHeight: 1.7 }}>
                    {sourceQuery
                      ? <div>· {t('assistant.sumQuery', 'Reuses existing query')} <Mono>{connector}.{sourceQuery}</Mono> ({t('assistant.readOnlyWord', 'read-only')})</div>
                      : <div>· {t('assistant.sumTable', 'Connector table')} <Mono>{connector}.{slug(tableName)}</Mono> ({screenColumns.length} {t('assistant.cols', 'columns')})</div>}
                    {tabs.some((tb) => tb.cols.length)
                      ? <div>· {t('assistant.sumScreen', 'Screen + dialog')} <Mono>{appKey}.{screenId}</Mono> ({tabs.filter((tb) => tb.cols.length).length} {t('assistant.tabsWord', 'tabs')})</div>
                      : <div>· {t('assistant.sumScreenRo', 'Screen')} <Mono>{appKey}.{screenId}</Mono> ({t('assistant.readOnlyGrid', 'read-only grid, no dialog')})</div>}
                    <div>· {t('assistant.sumCols', 'Default view enables {{grid}} of {{total}} columns', { grid: gridCols.length, total: screenColumns.length })}</div>
                    {!sourceQuery && <div>· {t('assistant.sumDict', 'Dictionary entries')}: {(ddItems ?? []).filter((d) => d.keep && !d.exists).length}</div>}
                    {createMenu && <div>· {t('assistant.sumMenu', 'Menu item')} <Mono>{appKey}</Mono>{menuParent ? ` → ${menuParent}` : ''}</div>}
                  </div>
                </Section>
                {!sourceQuery && (
                  <Section>
                    <SectionTitle>{t('assistant.readPreview', 'Read query (SELECT)')}</SectionTitle>
                    <SqlEditor value={readSql} onChange={() => undefined} rows={8} readOnly />
                  </Section>
                )}
              </>
            )
          )}
        </ModalBody>
        {!result && (
          <ModalFooter style={{ justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <Button $size="sm" $variant="ghost" disabled={busy} onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
              <Button $size="sm" $variant="ghost" disabled={step === 0 || busy} onClick={() => setStep((s) => Math.max(0, s - 1))}><ChevronLeft size={14} /> {t('common.back', 'Back')}</Button>
            </div>
            {STEPS[step] === 'review'
              ? <Button $size="sm" $variant="primary" disabled={busy || !tableName.trim() || screenColumns.length === 0} onClick={submit}>{busy ? <SpinnerRing size={14} /> : <Check size={14} />} {t('assistant.create', 'Create screen')}</Button>
              : <Button $size="sm" $variant="primary" disabled={!canNext || busy} onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}>{t('common.next', 'Next')} <ChevronRight size={14} /></Button>}
          </ModalFooter>
        )}
      </Modal>
    </Overlay>

    {/* Catalog picker — a searchable, grouped browser so the preset list stays usable as it grows. */}
    {catalogOpen && (
      <Overlay style={{ zIndex: 1100 }} onClick={() => setCatalogOpen(false)}>
        <Modal style={{ width: 'min(760px, 94vw)', height: 'min(640px, 88vh)', display: 'flex', flexDirection: 'column' }} onClick={(e) => e.stopPropagation()}>
          <ModalHeader>
            <HeadBar>
              <Title><BookOpen size={16} /> {t('assistant.catalog', 'Preset catalog')}</Title>
              <Button $size="sm" $variant="ghost" onClick={() => setCatalogOpen(false)} title={t('common.close', 'Close')}><X size={16} /></Button>
            </HeadBar>
          </ModalHeader>
          <ModalBody style={{ flex: 1, overflow: 'auto' }}>
            <Input value={catalogQ} onChange={(e) => setCatalogQ(e.target.value)} placeholder={t('assistant.catalogSearch', 'Search presets…')} autoFocus />
            {catalogGroups.length === 0 && <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '20px', textAlign: 'center' }}>{t('assistant.catalogNoMatch', 'No matching presets.')}</div>}
            {catalogGroups.map(([g, list]) => (
              <div key={g}>
                <CatGroup>{g}</CatGroup>
                {list.map((p) => (
                  <PresetCard key={p.id} type="button" onClick={() => { void applyPreset(p.id); setCatalogOpen(false) }}>
                    <div className="lbl">{p.label}</div>
                    {p.description && <div className="desc">{p.description}</div>}
                    <div className="tbls">{p.tables.map((tb) => tb.name).join(p.tables.length > 1 ? ' ⨝ ' : '')}</div>
                  </PresetCard>
                ))}
              </div>
            ))}
          </ModalBody>
        </Modal>
      </Overlay>
    )}
    </>,
    document.body,
  )
}
