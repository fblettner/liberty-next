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
import { Plus, X, ChevronRight, ChevronLeft, Check, Wand2, ArrowRight, ArrowLeft, KeyRound, RotateCcw } from 'lucide-react'
import { api } from '../../api/client'
import { Banner, Button, Checkbox, Field, Input, SearchSelect, SpinnerRing, type SearchSelectOption } from '../../common'
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from '../../common/Modal'
import {
  TablePicker, generateSql, suggestAlias, JOIN_TYPES,
  type PickedTable, type JoinTable, type JoinType, type JoinCond,
} from '../../common/SqlWizardModal'
import { buildCrudSql } from '../../common/sqlBuild'
import { SqlEditor } from '../../common/SqlEditor'
import { MENU_ICON_NAMES } from '../../components/SidebarMenu'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

type ColId = string                                   // "ALIAS.col"
interface WizTab { id: string; label: string; cols: ColId[] }
// One JDE dictionary proposal row (subset of /admin/dictionary/scan items, made editable).
interface DdItem {
  column: string; dd_id: string; exists: boolean; keep: boolean
  label: string; format: string; justify: string; size: number | null
  rules: string; rules_values: string; lookup_params: string   // lookup_params as "SY=01,RT=ST"
}

const STEPS = ['target', 'tables', 'columns', 'dictionary', 'menu', 'review'] as const
type StepKey = (typeof STEPS)[number]
const ICON_OPTS: SearchSelectOption[] = MENU_ICON_NAMES.map((n) => ({ value: n, label: n, mono: n }))

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
  & .grow { flex: 1; }
  & .k { display: inline-flex; align-items: center; gap: 3px; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const Chip = styled.button<{ $active: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; cursor: pointer; font-size: ${fontSize.sm};
  border: 1px solid ${(p) => (p.$active ? colors.blue.main : colors.border)};
  background: ${(p) => (p.$active ? colors.blue.bg : 'transparent')}; color: ${(p) => (p.$active ? colors.blue.main : colors.text.primary)};
`
const Grid2 = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 12px;`
const DdGrid = styled.div`display: grid; gap: 6px 8px; align-items: center; font-size: ${fontSize.sm}; max-height: 46vh; overflow: auto;`
const DdHead = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em;`

const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
const colName = (id: ColId) => (id.split('.').slice(1).join('.') || id).toUpperCase()   // bare column name
const DEFAULT_TABS: WizTab[] = [{ id: 'general', label: 'General', cols: [] }]
// "SY=01,RT=ST" → { SY: "01", RT: "ST" }
function parseParams(s: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const part of s.split(',')) { const [k, ...v] = part.split('='); if (k.trim()) out[k.trim()] = v.join('=').trim() }
  return out
}

export default function ScreenAssistant({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { apps } = useWorkspace()
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)

  // Step 1 — target
  const [connectors, setConnectors] = useState<SearchSelectOption[]>([])
  const [connector, setConnector] = useState('')
  const [app, setApp] = useState('')

  // Step 2 — tables & joins
  const [joins, setJoins] = useState<JoinTable[]>([])
  const [adding, setAdding] = useState(false)

  // Step 3 — columns → tabs
  const [tabs, setTabs] = useState<WizTab[]>(DEFAULT_TABS)
  const [activeTab, setActiveTab] = useState('general')
  const [keys, setKeys] = useState<Set<ColId>>(new Set())

  // Step 4 — JDE dictionary
  const [isJde, setIsJde] = useState(false)
  const [ddItems, setDdItems] = useState<DdItem[] | null>(null)
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
  const screenId = tableName ? slug(tableName) : ''
  const readSql = useMemo(() => generateSql(joins, assigned, [], []), [joins, assigned])

  // ── reset ──────────────────────────────────────────────────────────────────────────────
  const resetTables = () => { setJoins([]); setAdding(false); setTabs(DEFAULT_TABS); setActiveTab('general'); setKeys(new Set()); setDdItems(null) }
  const resetAll = () => {
    setStep(0); setMsg(null); setResult(null); setConnector(''); setApp('')
    resetTables(); setIsJde(false); setDdLoading(false)
    setCreateMenu(true); setMenuLabel(''); setMenuIcon('table'); setMenuParent(''); setTableName(''); setScreenLabel('')
  }

  // ── step 2 helpers (base + joins) ──────────────────────────────────────────────────────
  const pickBase = (tbl: PickedTable) => {
    const alias = suggestAlias(tbl.name, new Set())
    setJoins([{ schema: tbl.schema, name: tbl.name, alias, columns: tbl.columns }])
    if (!tableName) setTableName(slug(tbl.name))
  }
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

  // ── step 3 helpers (columns → tabs) ────────────────────────────────────────────────────
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

  // ── step 5: parent-menu options for the chosen app (sorted) ────────────────────────────
  const appKey = slug(app) || connector
  useEffect(() => {
    if (STEPS[step] !== 'menu' || !appKey) return
    void api.get<{ menus: Record<string, { items?: Array<{ id: string; label?: string }> }> }>('/admin/config/menus/parsed')
      .then((r) => {
        const items = r.menus[appKey]?.items ?? []
        setMenuItems(items
          .map((it) => ({ value: it.id, label: it.label ? `${it.label} (${it.id})` : it.id, mono: it.id }))
          .sort((a, b) => a.label.localeCompare(b.label)))
      }).catch(() => setMenuItems([]))
  }, [step, appKey])

  // ── step 4: run the JDE scan on the base table ─────────────────────────────────────────
  const runScan = useCallback(async () => {
    if (!base) return
    setDdLoading(true); setMsg(null)
    try {
      const r = await api.post<{ jde: boolean; items: Array<Record<string, unknown>> }>('/admin/dictionary/scan', {
        connector, table: base.name, db_schema: base.schema, scope: connector,
      })
      setIsJde(!!r.jde)
      setDdItems(r.items.map((it) => ({
        column: String(it.column ?? ''), dd_id: String(it.dd_id ?? it.column ?? ''),
        exists: !!it.exists, keep: !it.exists,
        label: String(it.label ?? ''), format: String(it.format ?? 'text'),
        justify: String(it.justify ?? ''), size: (it.size as number | null) ?? null,
        rules: String(it.rules ?? ''), rules_values: String(it.rules_values ?? ''),
        // The scan returns lookup_params as { SY: "01", RT: "ST" } for UDC columns — flatten to the
        // "SY=01,RT=ST" the input edits.
        lookup_params: Object.entries((it.lookup_params as Record<string, string> | undefined) ?? {}).map(([k, v]) => `${k}=${v}`).join(','),
      })))
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }); setDdItems([]) }
    finally { setDdLoading(false) }
  }, [base, connector])
  useEffect(() => { if (STEPS[step] === 'dictionary' && ddItems === null && base) void runScan() }, [step, ddItems, base, runScan])

  const patchDd = (col: string, patch: Partial<DdItem>) =>
    setDdItems((items) => (items ?? []).map((d) => d.column === col ? { ...d, ...patch } : d))
  const ddByCol = useMemo(() => {
    const m = new Map<string, DdItem>()
    for (const d of ddItems ?? []) m.set(d.column.toUpperCase(), d)
    return m
  }, [ddItems])

  // ── assemble + submit ──────────────────────────────────────────────────────────────────
  const screenColumns = useMemo(() => {
    const seen = new Set<string>()
    const out: Array<{ name: string; dd?: string; key?: boolean }> = []
    for (const tb of tabs) for (const id of tb.cols) {
      const nm = colName(id)
      if (seen.has(nm)) continue
      seen.add(nm)
      const dd = ddByCol.get(nm)
      out.push({ name: nm, ...(dd && dd.keep ? { dd: dd.dd_id } : {}), ...(keys.has(id) ? { key: true } : {}) })
    }
    return out
  }, [tabs, keys, ddByCol])

  const baseColNames = useMemo(() => new Set((base?.columns ?? []).map((c) => c.name.toUpperCase())), [base])
  const submit = async () => {
    if (!base) return
    setBusy(true); setMsg(null)
    const assignedBase = screenColumns.map((c) => c.name).filter((n) => baseColNames.has(n))
    const keyBase = screenColumns.filter((c) => c.key).map((c) => c.name).filter((n) => baseColNames.has(n))
    const crud = (kind: 'put' | 'post' | 'delete') =>
      buildCrudSql({ crud: kind, schema: base.schema, table: base.name, selectCols: assignedBase, keyCols: keyBase }) || undefined
    const kept = (ddItems ?? []).filter((d) => d.keep && !d.exists)
    const payload = {
      connector, app: appKey,
      table: {
        name: slug(tableName), label: screenLabel || undefined,
        get_sql: readSql,
        put_sql: keyBase.length ? crud('put') : undefined,
        post_sql: assignedBase.length ? crud('post') : undefined,
        delete_sql: keyBase.length ? crud('delete') : undefined,
      },
      dictionary_scope: connector,
      dictionary: kept.map((d) => ({
        id: d.dd_id, label: d.label || undefined, format: d.format || undefined,
        ...(d.justify ? { justify: d.justify } : {}), ...(d.size != null ? { size: d.size } : {}),
        ...(d.rules ? { rules: d.rules } : {}), ...(d.rules_values ? { rules_values: d.rules_values } : {}),
        ...(d.lookup_params.trim() ? { lookup_params: parseParams(d.lookup_params) } : {}),
      })),
      screen: {
        id: screenId, label: screenLabel || tableName, connector,
        read_query: `${slug(tableName)}_get`,
        ...(assignedBase.length ? { insert_query: `${slug(tableName)}_post` } : {}),
        ...(keyBase.length ? { update_query: `${slug(tableName)}_put`, delete_query: `${slug(tableName)}_delete` } : {}),
        columns: screenColumns,
        dialog: { title: screenLabel || tableName, tabs: tabs.filter((tb) => tb.cols.length).map((tb) => ({ id: tb.id, label: tb.label, type: 'form', fields: tb.cols.map((id) => ({ name: colName(id) })) })) },
      },
      menu: createMenu ? {
        app: appKey, id: screenId, label: menuLabel || screenLabel || tableName,
        icon: menuIcon || undefined, parent: menuParent || undefined, target: screenId, connector,
      } : null,
    }
    try {
      const r = await api.post<{ created: { screen: string } }>('/admin/assistant/scaffold', payload)
      setResult({ screen: r.created.screen })
      setMsg({ tone: 'ok', text: t('assistant.created', { screen: r.created.screen, defaultValue: 'Created {{screen}} and reloaded.' }) })
    } catch (e) { setMsg({ tone: 'err', text: e instanceof Error ? e.message : String(e) }) } finally { setBusy(false) }
  }

  // ── per-step gating ────────────────────────────────────────────────────────────────────
  const joinsReady = joins.length > 0 && joins.slice(1).every((j) => (j.join?.conds ?? []).some((c) => c.left && c.right) || (j.join?.raw ?? '').trim())
  const canNext = (
    STEPS[step] === 'target' ? !!connector && !!app.trim()
      : STEPS[step] === 'tables' ? !!base && joinsReady
        : STEPS[step] === 'columns' ? assigned.size > 0
          : true
  )
  const stepState = (i: number): 'done' | 'active' | 'todo' => (i < step ? 'done' : i === step ? 'active' : 'todo')
  const stepLabel = (k: StepKey) => t(`assistant.step.${k}`, {
    target: 'Target', tables: 'Tables & joins', columns: 'Columns → tabs', dictionary: 'Dictionary', menu: 'Menu', review: 'Review & create',
  }[k])
  const activeWizTab = tabs.find((tb) => tb.id === activeTab)
  const ddCols = isJde ? 'auto 1.1fr 1.4fr 90px 84px 84px 1fr 52px' : 'auto 1.2fr 1.6fr 120px 64px'

  return createPortal(
    // No click-outside / Esc close — those wiped a half-built screen by accident. Close is explicit
    // only: the header ✕ or the footer Cancel. The backdrop still dims, it just doesn't dismiss.
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
              <Section>
                <SectionTitle>{t('assistant.preset', 'Preset')}</SectionTitle>
                <SearchSelect value="" onChange={() => undefined} options={[]} placeholder={t('assistant.presetSoon', 'Catalog presets — coming soon')} />
              </Section>
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

          {/* ── Step 3: columns → tabs ── */}
          {STEPS[step] === 'columns' && (
            <>
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
                <Pane>
                  <PaneHead><ArrowRight size={12} /> {t('assistant.available', 'Available columns')} · {available.length}</PaneHead>
                  <PaneList>
                    {available.map((id) => (
                      <ColRow key={id}><span className="grow">{colName(id)}</span><Mini type="button" onClick={() => moveToTab(id)}><ArrowRight size={11} /></Mini></ColRow>
                    ))}
                    {available.length === 0 && <ColRow><span className="grow" style={{ color: colors.text.muted }}>{t('assistant.allAssigned', 'All columns assigned')}</span></ColRow>}
                  </PaneList>
                </Pane>
                <Pane>
                  <PaneHead><ArrowLeft size={12} /> {activeWizTab?.label} · {activeWizTab?.cols.length ?? 0}</PaneHead>
                  <PaneList>
                    {(activeWizTab?.cols ?? []).map((id) => (
                      <ColRow key={id}>
                        <span className="grow">{colName(id)}</span>
                        <span className="k"><Checkbox checked={keys.has(id)} onChange={() => toggleKey(id)} /><KeyRound size={11} /></span>
                        <Mini type="button" onClick={() => removeFromTab(id)}><X size={11} /></Mini>
                      </ColRow>
                    ))}
                  </PaneList>
                </Pane>
              </Dual>
            </>
          )}

          {/* ── Step 4: JDE dictionary ── */}
          {STEPS[step] === 'dictionary' && (
            ddLoading ? <SpinnerRing /> : (
              <>
                {!isJde && <Banner $tone="info">{t('assistant.notJde', 'This pool is not JDE/Oracle — dictionary entries are inferred from column names. Review or skip.')}</Banner>}
                <RowBar><SectionTitle style={{ flex: 1 }}>{t('assistant.ddProposals', 'Proposed dictionary entries (base table)')}</SectionTitle>
                  <Mini type="button" onClick={() => void runScan()}>{t('assistant.rescan', 'Re-scan')}</Mini></RowBar>
                <DdGrid style={{ gridTemplateColumns: ddCols }}>
                  <DdHead /><DdHead>{t('assistant.ddId', 'DD / column')}</DdHead><DdHead>{t('assistant.ddLabel', 'Label')}</DdHead><DdHead>{t('assistant.ddFormat', 'Format')}</DdHead>
                  {isJde && <><DdHead>{t('assistant.ddRules', 'Rules')}</DdHead><DdHead>{t('assistant.ddRulesVal', 'Rule value')}</DdHead><DdHead>{t('assistant.ddLookupParams', 'Lookup params')}</DdHead></>}
                  <DdHead>{t('assistant.ddSize', 'Size')}</DdHead>
                  {(ddItems ?? []).map((d) => (
                    <Fragment key={d.column}>
                      <Checkbox checked={d.keep} disabled={d.exists} onChange={() => patchDd(d.column, { keep: !d.keep })} />
                      <Mono title={d.exists ? t('assistant.ddExists', 'already in dictionary') : ''} style={{ opacity: d.exists ? 0.5 : 1 }}>
                        {d.dd_id}{d.column !== d.dd_id ? ` (${d.column})` : ''}
                      </Mono>
                      <Input value={d.label} onChange={(e) => patchDd(d.column, { label: e.target.value })} disabled={!d.keep} />
                      <SearchSelect value={d.format} onChange={(v) => patchDd(d.column, { format: v })} options={fmtOpts} allowCustom disabled={!d.keep} placeholder="text" />
                      {isJde && <>
                        <SearchSelect value={d.rules} onChange={(v) => patchDd(d.column, { rules: v })} options={ruleOpts} anyLabel={t('common.none', '(none)')} disabled={!d.keep} placeholder="—" />
                        <Input value={d.rules_values} onChange={(e) => patchDd(d.column, { rules_values: e.target.value })} disabled={!d.keep || !d.rules} placeholder="—" />
                        <Input value={d.lookup_params} onChange={(e) => patchDd(d.column, { lookup_params: e.target.value })} disabled={!d.keep || d.rules !== 'LOOKUP'} placeholder={d.rules === 'LOOKUP' ? 'SY=01,RT=ST' : ''} />
                      </>}
                      <Input value={d.size == null ? '' : String(d.size)} onChange={(e) => patchDd(d.column, { size: e.target.value ? Number(e.target.value) : null })} disabled={!d.keep} />
                    </Fragment>
                  ))}
                </DdGrid>
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
                    <div>· {t('assistant.sumTable', 'Connector table')} <Mono>{connector}.{slug(tableName)}</Mono> ({screenColumns.length} {t('assistant.cols', 'columns')})</div>
                    <div>· {t('assistant.sumScreen', 'Screen + dialog')} <Mono>{appKey}.{screenId}</Mono> ({tabs.filter((tb) => tb.cols.length).length} {t('assistant.tabsWord', 'tabs')})</div>
                    <div>· {t('assistant.sumDict', 'Dictionary entries')}: {(ddItems ?? []).filter((d) => d.keep && !d.exists).length}</div>
                    {createMenu && <div>· {t('assistant.sumMenu', 'Menu item')} <Mono>{appKey}</Mono>{menuParent ? ` → ${menuParent}` : ''}</div>}
                  </div>
                </Section>
                <Section>
                  <SectionTitle>{t('assistant.readPreview', 'Read query (SELECT)')}</SectionTitle>
                  <SqlEditor value={readSql} onChange={() => undefined} rows={8} readOnly />
                </Section>
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
    </Overlay>,
    document.body,
  )
}
