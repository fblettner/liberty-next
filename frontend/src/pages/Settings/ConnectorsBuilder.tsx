// Structured editor for `[connectors.*]` (sql + api) — Phase-7 config builder. Left = the
// connector list; right pane shows the **Tables** view for SQL connectors (queries grouped by
// `<base>_<get|put|post|delete>` suffix, each table opens a unified editor where
// General/Columns/Read/Update/Insert/Delete share one form). API connectors render straight
// into the SchemaNavigator (no CRUD shape to group by). A "Settings…" button raises a modal
// hosting the full SchemaNavigator over the connector (label / pool / max_rows + loose
// queries) — the rare escape hatch for non-CRUD shapes. Save validates each connector against
// the discriminated schema + rewrites only the [connectors.*] tables of connectors.toml (a
// changed connector's subtree is re-rendered, so its inline `columns = [{…}]` may become
// `[[…]]` — review in git), then reloads. No rename yet — delete + re-add. Renders the body
// only; Settings/index.tsx wraps the page.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, Plus, Trash2, Database, Globe, Search, FileCog, Copy, Edit3, Layers, Undo2, GitBranch, Shuffle, ArrowRightLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, Mono, SchemaForm, SearchSelect, FrameworkEnumsContext, SqlConnectorContext, useModals, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, Input, type FrameworkEnum, type FrameworkEnums, type JsonSchema } from '../../common'
import type { ConfigSchemas, ConnectorsDoc, DictionaryDoc } from '../../types/config'
import ApiConnectorEditor, { type ApiConnector as ApiConnectorEditorValue } from './ApiConnectorEditor'
import { colors, fontSize, fonts, radius } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import ConnectorsTableEditor from './ConnectorsTableEditor'
import { ScreenDesignerModal } from './ScreenDesignerModal'
import { CRUD_KINDS, type TableEntry, duplicateTableEntry, findTableIndex, newEmptyTable, pickSchemaProperties, removeTableByName, replaceTableByName, tableEntryFromFlatQueries, tableExistsByName } from './connectorTables'
import { ScaffoldQueryModal, type ScaffoldKind } from './ScaffoldQueryModal'
import { CrudWizardModal } from './CrudWizardModal'
import { FindUsagesModal, type FindUsagesTarget } from './FindUsagesModal'
import { MoveQueryModal } from './MoveQueryModal'
import { validateId, suggestCloneId } from '../../services/idValidator'
import { DictionaryScan } from './DictionaryScan'

type Connectors = Record<string, Record<string, unknown>>

// Layout: outer Shell flex-fills, top toolbar is fixed, the Split fills remaining height,
// only the inner panels scroll. Same pattern as PoolsBuilder.
const Shell = styled.div`
  display: flex; flex-direction: column; gap: 12px;
  flex: 1; min-height: 0; height: 100%;
`
const Toolbar = styled.div`display: flex; align-items: center; gap: 10px; flex-shrink: 0; flex-wrap: wrap;`
const ToolbarLeft = styled.div`display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0;`
const ToolbarRight = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap;`
const ToolbarDivider = styled.span`
  display: inline-block; width: 1px; height: 18px; background: ${colors.border}; margin: 0 2px;
`
const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0; align-items: stretch;`
// Left nav scrolls on its own (a connector list with dozens of entries shouldn't drag the page
// along when wheeled). Search row + the two "+ Add" buttons stay pinned outside the scroller —
// items live in NavList. The flex chain (Shell → Split → NavCol → NavList) caps the list height.
const NavCol = styled.div`flex: 0 0 210px; display: flex; flex-direction: column; gap: 4px; min-width: 0; min-height: 0;`
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
const FormCol = styled(Card)`flex: 1; min-width: 0; min-height: 0; overflow-y: auto;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`
const NavGroupLabel = styled.div`
  font-size: ${fontSize.micro}; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
  color: ${colors.text.muted}; padding: 8px 6px 3px;
`
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
// scroller so wheeling doesn't drag the whole Settings page along. With the FormCol now scrolling
// internally (overflow-y: auto + min-height: 0), the table list rides that scroll naturally — but
// we keep min-height: 0 here so the gap collapses cleanly when the Stack flexes.
const TableList = styled.div`display: flex; flex-direction: column; gap: 6px; min-height: 0; padding-right: 4px;`
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

export default function ConnectorsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { findScreen, currentApp, menus, refresh: refreshWorkspace } = useWorkspace()
  const [schemas, setSchemas] = useState<ConfigSchemas | null>(null)
  // The dictionary is read-only here — we just need its keys (entry ids per scope) to populate
  // the DD_ENTRIES dropdown that drives `ColumnHint.dd` and `FilterDep.source/column`.
  const [dictionary, setDictionary] = useState<DictionaryDoc['dictionary'] | null>(null)
  const [conns, setConns] = useState<Connectors | null>(null)
  const [original, setOriginal] = useState('')
  // Dictionary edits — only the scaffold flow writes here (adding a sequence/lookup creates
  // a matching entry under the connector's scope). Tracked separately so the dirty flag /
  // save() picks them up alongside the connector edits.
  const [dictOriginal, setDictOriginal] = useState('')
  const [sel, setSel] = useState<string | null>(null)
  const [q, setQ] = useState('')
  // "Clone app" modal — duplicates the source app's config (connector, dictionary
  // overlay, menus, screens) under a new name pointing at a new pool. The on-disk
  // edit is atomic: the backend POST /admin/config/clone-app validates every
  // rewritten doc through Pydantic before writing any file.
  const [cloneModalOpen, setCloneModalOpen] = useState(false)
  const [cloneSource, setCloneSource] = useState('')
  const [cloneNewApp, setCloneNewApp] = useState('')
  const [cloneNewPool, setCloneNewPool] = useState('')
  const [cloneBusy, setCloneBusy] = useState(false)
  const [cloneError, setCloneError] = useState<string | null>(null)
  const [pools, setPools] = useState<string[]>([])  // for the new-pool dropdown
  // "+ Connector" modal — one entry point for both connector kinds; the type (sql / api) is
  // picked here (it's fixed once created — it's the discriminator) along with the name.
  const [addConnOpen, setAddConnOpen] = useState(false)
  const [addConnType, setAddConnType] = useState<'sql' | 'api'>('sql')
  const [addConnName, setAddConnName] = useState('')
  const [addConnError, setAddConnError] = useState<string | null>(null)
  // Tables / Sequences / Lookups view — the connector list is the same underneath, the mode
  // just filters which queries you see and which editor they open. Tables = CRUD-grouped
  // queries (the canonical case). Sequences = queries referenced by ``[sequences.*]`` in
  // dictionary.toml. Lookups = queries referenced by ``[lookups.*]``. The "loose" queries
  // (none of the three) stay accessible via the Settings… escape hatch.
  const [mode, setMode] = useState<'tables' | 'custom' | 'sequences' | 'lookups' | 'settings'>('settings')
  // Scaffold modal — opened from the Sequences / Lookups mode's "Add" button. The modal
  // pulls live pool introspection, the operator picks a table + column(s), and on save we
  // append the generated query to the connector + the matching ``[sequences.<id>]`` /
  // ``[lookups.<id>]`` entry to dictionary.toml (under the connector's scope when one exists,
  // else shared). Both files are written together on the next Save click.
  const [scaffoldKind, setScaffoldKind] = useState<ScaffoldKind | null>(null)
  // CRUD wizard modal — opened from "+ Add table → Generate from DB". Reverse-engineers a table
  // into all four CRUD queries via the live pool introspection.
  const [crudWizardOpen, setCrudWizardOpen] = useState(false)
  // After the CRUD wizard reverses a table, offer to generate its dictionary items.
  const [dictScan, setDictScan] = useState<{ connector: string; table: string; schema?: string } | null>(null)
  // "Open visual builder" from a CRUD table row → the screen designer modal opens in-place, no
  // Settings tab switch. Self-contained: the modal fetches + saves screens.toml itself.
  const [screenDesigner, setScreenDesigner] = useState<{ app: string; id: string } | null>(null)
  const [selTable, setSelTable] = useState<string | null>(null)
  const [usagesTarget, setUsagesTarget] = useState<FindUsagesTarget | null>(null)
  const [moveTarget, setMoveTarget] = useState<{ kind: 'table' | 'query' | 'sequence' | 'lookup'; name: string } | null>(null)
  // Selected single-query name when ``mode === 'sequences'`` or ``'lookups'``.
  const [selQuery, setSelQuery] = useState<string | null>(null)
  const [tq, setTq] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  // Connector names the framework hardcodes as licensed — checkbox locked in the editor;
  // the loader gates these regardless of what's on disk (see liberty/licensing/__init__.py).
  const [alwaysLicensed, setAlwaysLicensed] = useState<Set<string>>(new Set())

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed'),
      api.get<DictionaryDoc>('/admin/config/dictionary/parsed'),
      // Pool names feed the Clone-app modal's "new pool" picker (drop a pool the
      // operator already created via Settings → Pools). A separate fetch keeps
      // this builder decoupled from the pools builder's full payload.
      api.get<{ pools: Record<string, unknown> }>('/admin/config/pools'),
    ])
      .then(([s, d, dd, p]) => {
        // The builder edits the sectioned shape (`tables` / `queries` / `sequences` / `lookups`)
        // directly — no flattening. Save sends it back as-is.
        setSchemas(s); setConns(d.connectors); setOriginal(JSON.stringify(d.connectors))
        setAlwaysLicensed(new Set(d.always_licensed ?? []))
        setDictionary(dd.dictionary); setDictOriginal(JSON.stringify(dd.dictionary))
        // Keep the current selection across reloads; on first open default to the workspace's
        // selected app (when it's a connector here), else the first connector.
        setSel((cur) => (cur && d.connectors[cur] ? cur : (currentApp && d.connectors[currentApp] ? currentApp : Object.keys(d.connectors)[0] ?? null)))
        setPools(Object.keys(p.pools ?? {}))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  // reset table selection + search when switching connectors
  useEffect(() => { setSelTable(null); setSelQuery(null); setTq(''); setMode('settings') }, [sel])
  // reset list selection + search when switching mode (different list, different selection)
  useEffect(() => { setSelTable(null); setSelQuery(null); setTq('') }, [mode])

  const dirty = useMemo(() => {
    const connsDirty = conns != null && JSON.stringify(conns) !== original
    const dictDirty = dictionary != null && JSON.stringify(dictionary) !== dictOriginal
    return connsDirty || dictDirty
  }, [conns, original, dictionary, dictOriginal])
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
    // MENU_HOME_ITEMS — leaf items in the current connector's menu, used by the Settings tab's
    // ``home`` SearchSelect. Walks the resolved menu tree (folders are hidden).
    const homeOptions: { value: string; label: string; mono?: string }[] = []
    const walk = (items: ReadonlyArray<{ id: string; label: string; type?: string; target?: string; items?: unknown[] }>) => {
      for (const it of items) {
        if (it.type && it.target) homeOptions.push({ value: it.id, label: it.label || it.id, mono: it.id })
        if (Array.isArray(it.items)) walk(it.items as typeof items)
      }
    }
    if (sel && menus && menus[sel]) walk(menus[sel].items)
    base.MENU_HOME_ITEMS = { label: 'Menu items', values: homeOptions }
    return base
  }, [schemas, dictionary, sel, menus])

  const update = (name: string, v: Record<string, unknown>) => setConns((p) => ({ ...(p ?? {}), [name]: { ...v, type: (p ?? {})[name]?.type } }))
  // Replace one section array (`tables` / `queries` / `sequences` / `lookups`) on a connector.
  const updateSection = (name: string, section: string, arr: Record<string, unknown>[]) => {
    setConns((p) => {
      const cur = (p ?? {})[name] ?? {}
      return { ...(p ?? {}), [name]: { ...cur, [section]: arr } }
    })
    setStatus(null)
  }
  const updateTables = (name: string, tables: TableEntry[]) => updateSection(name, 'tables', tables as Record<string, unknown>[])
  // Add a free-standing custom query (Custom queries tab) — a query not tied to a table's CRUD set.
  const addCustomQuery = async () => {
    if (!sel) return
    const existing = customQueries.map((q) => String(q.name ?? ''))
    const name = (await modals.prompt({
      title: t('settings.connectors.addQuery', 'Add query'),
      message: t('settings.connectors.queryNamePrompt', 'Query name (e.g. post_invoice, customer_balance):'),
      placeholder: 'snake_case',
      validate: (v) => validateId({ kind: 'query', proposed: v, existing, mode: 'add' }),
    }))?.trim()
    if (!name) return
    if (customQueries.some((q) => q.name === name)) { setSelQuery(name); return }
    updateSection(sel, 'queries', [...customQueries, { name, sql: '' }])
    setSelQuery(name); setStatus(null)
  }
  const openAddConnector = () => { setAddConnType('sql'); setAddConnName(''); setAddConnError(null); setAddConnOpen(true) }
  const submitAddConnector = () => {
    const name = addConnName.trim()
    // Use the shared validator so the connector dialog matches every other Add prompt
    // (one source of truth for the pattern + duplicate check + warnings).
    const result = validateId({ kind: 'connector', proposed: name, existing: conns ? Object.keys(conns) : [], mode: 'add' })
    if (result.error) { setAddConnError(result.error); return }
    setConns((p) => ({ ...(p ?? {}), [name]: addConnType === 'api' ? { type: 'api', base_url: '' } : { type: 'sql', queries: [] } }))
    setSel(name); setStatus(null); setAddConnOpen(false)
  }
  // Discard local edits — revert connectors + dictionary to the last-loaded state (client-side,
  // no network). Same as Pools' Discard.
  const discard = () => {
    if (original) setConns(JSON.parse(original) as Connectors)
    if (dictOriginal) setDictionary(JSON.parse(dictOriginal) as DictionaryDoc['dictionary'])
    setStatus(null); setError(null)
  }
  // Clone / delete a single query (Unclassified / Sequences / Lookups editor) — the counterparts
  // of the Tables editor's per-table Clone / Delete, so every query kind has the same Rename /
  // Clone / Delete trio. Like the table clone, this prompts for the new name (no silent _copy).
  // The single-query editor (Custom / Sequences / Lookups tabs) operates on whichever section the
  // current ``mode`` maps to. Reads ``conns`` / ``sel`` / ``mode`` directly so it doesn't depend
  // on the post-early-return render locals.
  const curSection = (): { key: string; arr: Record<string, unknown>[] } => {
    const key = mode === 'sequences' ? 'sequences' : mode === 'lookups' ? 'lookups' : 'queries'
    const c = sel ? (conns ?? {})[sel] : null
    const arr = (c && Array.isArray((c as Record<string, unknown>)[key]) ? (c as Record<string, unknown>)[key] : []) as Record<string, unknown>[]
    return { key, arr }
  }
  const duplicateQuery = async (name: string) => {
    if (!sel) return
    const { key, arr } = curSection()
    const src = arr.find((qq) => qq.name === name)
    if (!src) return
    const existing = arr.map((qq) => String(qq.name ?? ''))
    const next = (await modals.prompt({
      title: t('common.clone', 'Clone'),
      message: t('settings.tables.duplicatePrompt', { name }),
      defaultValue: suggestCloneId(name, existing),
      submitLabel: t('common.clone', 'Clone'),
      validate: (v) => {
        if (v === name) return { error: t('settings.tables.duplicateSameName', 'Pick a different name.') }
        return validateId({ kind: 'query', proposed: v, existing, mode: 'clone' })
      },
    }))?.trim()
    if (!next) return
    updateSection(sel, key, [...arr, { ...src, name: next }])
    setSelQuery(next); setStatus(null)
  }
  const deleteQuery = async (name: string) => {
    if (!sel) return
    const ok = await modals.confirm({
      title: t('settings.connectors.deleteQuery', 'Delete query'),
      message: t('settings.connectors.confirmDeleteQuery', 'Delete query {{name}}?', { name }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const { key, arr } = curSection()
    updateSection(sel, key, arr.filter((qq) => qq.name !== name))
    setSelQuery(null); setStatus(null)
  }
  // Note: API connector testing is no longer a toolbar action — it lives in the editor's
  // Test tab (see ``ApiConnectorEditor``), where the operator picks an endpoint, supplies
  // its placeholder values, and sees the result in-place. Backed by the same
  // ``POST /admin/config/api/test`` endpoint; that endpoint accepts ``test_endpoint`` +
  // ``params`` so it can fire any named endpoint without saving first.
  // Connector delete is the cascade (``deleteApp`` below) — it removes the connector *and*
  // everything scoped under it. A connectors.toml-only delete left orphaned screens / dictionary
  // overlay / charts / dashboards behind, so it's gone.

  // Open the Clone-app modal, pre-filled with the currently-selected connector as the
  // source (most common case: operator clicks a connector then "Clone app").
  const openCloneApp = () => {
    setCloneSource(sel ?? '')
    setCloneNewApp('')
    setCloneNewPool('')
    setCloneError(null)
    setCloneModalOpen(true)
  }
  // Connector delete = cross-file cascade. Removes the connector AND everything scoped under it:
  // its dictionary overlay, menu, screens, charts, and dashboards. The pool is left alone (managed
  // in Settings → Pools, and may be shared). One confirmation — deleting a connector is a big deal.
  const deleteApp = async (appName: string) => {
    const ok = await modals.confirm({
      title: t('settings.connectors.delete', 'Delete'),
      message: t(
        'settings.connectors.confirmDeleteApp',
        'Delete connector {{name}} and everything that belongs to it?',
        { name: appName },
      ),
      variant: 'danger',
      confirmLabel: t('common.delete', 'Delete'),
    })
    if (!ok) return
    setBusy(true); setStatus(null); setError(null)
    try {
      await api.post('/admin/config/delete-app', { app: appName })
      await api.post('/admin/reload')
      refreshWorkspace()
      setSel((s) => (s === appName ? null : s))
      load()
      setStatus(t('settings.connectors.deleteAppSuccess', 'Deleted connector {{name}}.', { name: appName }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const submitCloneApp = async () => {
    setCloneBusy(true); setCloneError(null)
    try {
      const body = { source_app: cloneSource, new_app: cloneNewApp, new_pool: cloneNewPool }
      const result = await api.post<{ total_entries: number; warnings: string[] }>('/admin/config/clone-app', body)
      // Reload the framework so the new app's routes/menus/screens are picked up live,
      // then refresh the page state so the operator sees the new connector in the list.
      await api.post('/admin/reload')
      load()
      setCloneModalOpen(false)
      setStatus(t(
        'settings.connectors.cloneSuccess',
        'Cloned {{src}} → {{dst}} ({{n}} entries; reload applied)',
        { src: cloneSource, dst: cloneNewApp, n: result.total_entries },
      ))
    } catch (e) {
      setCloneError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setCloneBusy(false)
    }
  }
  // Rename the selected connector — and every cross-file reference. Goes through the
  // ``POST /admin/config/rename`` endpoint so the connectors.toml top-level key change rides
  // alongside the screen / menu / dictionary / dashboard / chart updates in one atomic pass.
  // Failing that, a previous-generation rename would have left stale ``connector = "<old>"``
  // values scattered across the other files, and the operator would have hunted them by hand.
  //
  // Constraints:
  //   * Refuses to run when the builder has unsaved local edits — those would be clobbered by
  //     the disk-side rewrite + reload that follows the rename. We prompt the operator to save
  //     or discard first (their choice).
  //   * Confirms loudly — the rename touches files outside this builder; surprising the
  //     operator with edits they didn't expect to make is the wrong UX.
  //   * After the endpoint succeeds, runs ``/admin/reload`` to swap the live registry, then
  //     reloads the local connectors view AND refreshes the workspace context so screens /
  //     menus reflect the new name everywhere in the app.
  const renameConnector = async (oldName: string) => {
    if (!conns) return
    if (dirty) {
      const choice = await modals.choose({
        title: t('settings.rename.button'),
        message: t('settings.rename.unsavedFirst'),
        options: [
          { value: 'save', label: t('common.save'), variant: 'primary', autoFocus: true },
          { value: 'cancel', label: t('common.cancel'), variant: 'ghost' },
        ],
        cancelValue: 'cancel',
      })
      if (choice !== 'save') return
      await save()
      // ``save()`` clears ``dirty``; bail if it didn't (network failure / validation error
      // already surfaced on the banner — the operator can retry the rename).
      if (dirty) return
    }
    const existing = Object.keys(conns).filter((k) => k !== oldName)
    const next = (await modals.prompt({
      title: t('settings.rename.button'),
      message: t('settings.connectors.renamePrompt', { name: oldName }),
      defaultValue: oldName,
      submitLabel: t('settings.rename.button'),
      validate: (v) => validateId({ kind: 'connector', proposed: v, existing, mode: 'rename', currentName: oldName }),
    }))?.trim()
    if (!next || next === oldName) return
    setBusy(true)
    try {
      const result = await api.post<{
        files: Record<string, number>
        warnings: string[]
        total_refs: number
      }>('/admin/config/rename', { kind: 'connector', old_name: oldName, new_name: next })
      // The rename succeeded on disk; now make the running app pick it up.
      await api.post('/admin/reload')
      refreshWorkspace()                          // sync — bumps a nonce that triggers refetch
      // Reload our own view (re-reads connectors.toml + dictionary.toml from disk, clears the
      // dirty flag, points selection at the new key). Pick the new connector so the operator
      // doesn't lose their place.
      setSel(next)
      load()
      // Surface the result on the status banner — ref count tells the operator what was
      // actually touched; any warnings the helper emitted (e.g. matching menu app key not
      // renamed) ride alongside so they know to follow up.
      const filesTouched = Object.values(result.files).filter((n) => n > 0).length
      const tail = result.warnings.length ? ` · ${result.warnings.join(' · ')}` : ''
      setStatus(t('settings.connectors.renamedAcross', {
        from: oldName, to: next, refs: result.total_refs, files: filesTouched,
      }) + tail)
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e)
      setError(t('settings.connectors.renameFailed', { name: oldName, error: msg }))
    } finally {
      setBusy(false)
    }
  }
  // Shared prelude for a query / table rename: flush-or-cancel pending edits (the rename rewrites
  // files on disk + reloads, which would drop unsaved edits), then prompt for the new name with the
  // same validation the connector rename uses. Returns the new name, or null if cancelled.
  const promptRename = async (oldName: string, title: string, message: string, existing: string[]): Promise<string | null> => {
    if (dirty) {
      const choice = await modals.choose({
        title, message: t('settings.rename.unsavedFirst'),
        options: [
          { value: 'save', label: t('common.save'), variant: 'primary', autoFocus: true },
          { value: 'cancel', label: t('common.cancel'), variant: 'ghost' },
        ],
        cancelValue: 'cancel',
      })
      if (choice !== 'save') return null
      await save()
      if (dirty) return null
    }
    const existingMinusSelf = existing.filter((n) => n !== oldName)
    const next = (await modals.prompt({
      title, message, defaultValue: oldName, submitLabel: t('settings.rename.button'),
      validate: (v) => validateId({ kind: 'query', proposed: v, existing: existingMinusSelf, mode: 'rename', currentName: oldName }),
    }))?.trim()
    return next && next !== oldName ? next : null
  }
  // Cross-file rename via POST /admin/config/rename (kind = query | table), scoped to the selected
  // connector; reload + refresh + re-select on success. Mirrors renameConnector.
  const runCrossRename = async (kind: 'query' | 'table', oldName: string, newName: string, onDone: (n: string) => void) => {
    setBusy(true)
    try {
      const result = await api.post<{ files: Record<string, number>; warnings: string[]; total_refs: number }>(
        '/admin/config/rename', { kind, old_name: oldName, new_name: newName, scope: sel },
      )
      await api.post('/admin/reload')
      refreshWorkspace()
      onDone(newName)
      load()
      const filesTouched = Object.values(result.files).filter((n) => n > 0).length
      setStatus(t('settings.connectors.renamedAcross', { from: oldName, to: newName, refs: result.total_refs, files: filesTouched }))
    } catch (e) {
      setError(t('settings.connectors.renameFailed', { name: oldName, error: e instanceof ApiError ? e.message : String(e) }))
    } finally { setBusy(false) }
  }
  const renameQuery = async (oldName: string) => {
    if (!sel) return
    const existing = curSection().arr.map((q) => String(q.name))
    const next = await promptRename(oldName, t('settings.connectors.renameQuery', 'Rename query'), t('settings.connectors.renameQueryPrompt', { name: oldName }), existing)
    if (next) await runCrossRename('query', oldName, next, (n) => setSelQuery(n))
  }
  const renameTable = async (oldBase: string) => {
    if (!sel) return
    const existing = tablesArr.map((tb) => tb.name)
    const next = await promptRename(oldBase, t('settings.connectors.renameTable', 'Rename table'), t('settings.connectors.renameTablePrompt', { name: oldBase }), existing)
    if (next) await runCrossRename('table', oldBase, next, (n) => setSelTable(n))
  }
  const addTable = async (connectorName: string) => {
    // Two flows for "+ Add table":
    //   * **Generate from DB**: opens the CRUD wizard — pick a table, mark key columns, get
    //     all four queries (_get / _put / _post / _delete) auto-generated against the live
    //     pool. The recommended path for any table that exists in the DB.
    //   * **Empty stub**: prompts for a name and creates a blank ``<base>_get`` (the old
    //     behaviour). For when the table doesn't exist in the DB yet, or the operator wants
    //     to hand-write each query.
    const choice = await modals.choose({
      title: t('settings.tables.addTable'),
      message: t('settings.crudWizard.howAdd', 'How do you want to add this table?'),
      options: [
        { value: 'wizard', label: t('settings.crudWizard.generateFromDb', 'Generate from DB'), variant: 'primary', autoFocus: true },
        { value: 'empty', label: t('settings.crudWizard.emptyStub', 'Empty stub'), variant: 'ghost' },
      ],
      cancelValue: 'cancel',
      cancelLabel: t('common.cancel'),
    })
    if (choice === 'cancel' || choice == null) return
    if (choice === 'wizard') { setCrudWizardOpen(true); return }
    // Empty stub path — the operator types the table NAME; we create a blank table with an
    // empty read (`get`) slot. Validate against the existing table names so two can't collide.
    const cur = (conns ?? {})[connectorName] ?? {}
    const curTables = (Array.isArray(cur.tables) ? cur.tables : []) as TableEntry[]
    const existingNames = curTables.map((tb) => tb.name)
    const base = (await modals.prompt({
      title: t('settings.tables.addTable'),
      message: t('settings.tables.namePrompt'),
      placeholder: 'snake_case',
      validate: (v) => validateId({ kind: 'query', proposed: v, existing: existingNames, mode: 'add' }),
    }))?.trim()
    if (!base) return
    if (tableExistsByName(curTables, base)) { setSelTable(base); return }
    updateTables(connectorName, [...curTables, newEmptyTable(base)])
    setSelTable(base)
  }
  const duplicateTable = async (connectorName: string, oldBase: string) => {
    const cur = (conns ?? {})[connectorName] ?? {}
    const curTables = (Array.isArray(cur.tables) ? cur.tables : []) as TableEntry[]
    // Pre-check: nothing to duplicate from. Surface as an alert before prompting at all.
    if (findTableIndex(curTables, oldBase) < 0) {
      await modals.alert({
        title: t('settings.tables.duplicate'),
        message: t('settings.tables.duplicateNoSource', { name: oldBase }),
        variant: 'danger',
      })
      return
    }
    const newBase = (await modals.prompt({
      title: t('settings.tables.duplicate'),
      message: t('settings.tables.duplicatePrompt', { name: oldBase }),
      defaultValue: `${oldBase}_copy`,
      submitLabel: t('settings.tables.duplicate'),
      validate: (v) => {
        if (!v) return null   // empty → close as if cancelled
        if (v.toLowerCase() === oldBase.toLowerCase()) return t('settings.tables.duplicateSameName')
        if (tableExistsByName(curTables, v)) return t('settings.tables.duplicateExists', { name: v })
        return null
      },
    }))?.trim()
    if (!newBase) return
    updateTables(connectorName, duplicateTableEntry(curTables, oldBase, newBase))
    setSelTable(newBase)
  }

  // Reclassify a connector entity between the four sections. A standalone query / sequence /
  // lookup is the same `QueryDef` shape, so moving among those three keeps the name (and every
  // reference) intact. Crossing the Table boundary changes the *runtime* name — a table's slot is
  // addressed as `<base>_<crud>`, a standalone query by its bare name — so we confirm first and the
  // operator updates any screen / action / dictionary reference afterwards. Converting a table is
  // only offered for a single-slot table (a multi-slot table is genuinely a CRUD set, not one
  // query). Common reason: a v1 query mis-tagged `type = "table"` migrated into a one-slot table.
  type SectionKind = 'tables' | 'queries' | 'sequences' | 'lookups'
  const sectionLabel = (k: SectionKind): string => ({
    tables: t('settings.tables.tablesView'),
    queries: t('settings.tables.looseSectionLabel', 'Queries'),
    sequences: t('settings.connectors.sequencesView', 'Sequences'),
    lookups: t('settings.connectors.lookupsView', 'Lookups'),
  }[k])
  const modeFor = (k: SectionKind): 'tables' | 'custom' | 'sequences' | 'lookups' => (k === 'queries' ? 'custom' : k)
  const changeType = async (fromKind: SectionKind, name: string) => {
    if (!sel || !conns) return
    const conn = conns[sel] ?? {}
    const arrOf = (k: SectionKind) => (Array.isArray(conn[k]) ? conn[k] : []) as Record<string, unknown>[]
    const choice = await modals.choose({
      title: t('settings.connectors.changeTypeTitle', 'Change type'),
      message: t('settings.connectors.changeTypePrompt', 'Move "{{name}}" to which kind?', { name }),
      options: (['tables', 'queries', 'sequences', 'lookups'] as SectionKind[])
        .filter((k) => k !== fromKind)
        .map((k) => ({ value: k, label: sectionLabel(k) })),
      cancelValue: 'cancel',
      cancelLabel: t('common.cancel'),
    })
    if (!choice || choice === 'cancel') return
    const toKind = choice as SectionKind
    const crossesTable = (fromKind === 'tables') !== (toKind === 'tables')

    let queryDef: Record<string, unknown> | null = null
    let tableEntry: TableEntry | null = null
    const nextConn: Record<string, unknown> = { ...conn }

    if (fromKind === 'tables') {
      const tb = (arrOf('tables') as unknown as TableEntry[]).find((x) => x.name === name)
      if (!tb) return
      const filled = CRUD_KINDS.filter((c) => tb[c])
      if (filled.length > 1) {
        await modals.alert({
          title: t('settings.connectors.changeTypeTitle', 'Change type'),
          message: t('settings.connectors.changeTypeMultiSlot', 'This table has more than one CRUD slot, so it is a real CRUD set — not a single query. Delete the extra slots first if it is really just one query.'),
          variant: 'danger',
        })
        return
      }
      const slot = (tb[filled[0]] ?? {}) as Record<string, unknown>
      queryDef = {
        name: tb.name, sql: slot.sql ?? '',
        ...(slot.writable != null ? { writable: slot.writable } : {}),
        ...(slot.params != null ? { params: slot.params } : {}),
        ...(tb.label != null ? { label: tb.label } : {}),
        ...(tb.description != null ? { description: tb.description } : {}),
      }
      nextConn.tables = removeTableByName(arrOf('tables') as unknown as TableEntry[], name)
    } else {
      const q = arrOf(fromKind).find((x) => x.name === name)
      if (!q) return
      nextConn[fromKind] = arrOf(fromKind).filter((x) => x.name !== name)
      if (toKind === 'tables') {
        tableEntry = {
          name: String(q.name),
          ...(q.label != null ? { label: q.label as string } : {}),
          ...(q.description != null ? { description: q.description as string } : {}),
          get: {
            sql: q.sql ?? '',
            ...(q.writable != null ? { writable: q.writable as boolean } : {}),
            ...(q.params != null ? { params: q.params as unknown[] } : {}),
          },
        }
      } else {
        queryDef = { ...q }   // same QueryDef shape — just changes which section it lives in
      }
    }

    if (crossesTable) {
      const oldRef = fromKind === 'tables' ? `${name}_get` : name
      const newRef = toKind === 'tables' ? `${name}_get` : name
      const ok = await modals.confirm({
        title: t('settings.connectors.changeTypeTitle', 'Change type'),
        message: t('settings.connectors.changeTypeRenameWarn', 'This changes the query\'s runtime name from "{{old}}" to "{{new}}". Update any screen / action / dictionary reference to the new name afterwards. Continue?', { old: oldRef, new: newRef }),
        confirmLabel: t('settings.connectors.changeTypeTitle', 'Change type'),
      })
      if (!ok) return
    }

    if (toKind === 'tables' && tableEntry) {
      const cur = (Array.isArray(nextConn.tables) ? nextConn.tables : []) as TableEntry[]
      nextConn.tables = [...cur, tableEntry]
    } else if (queryDef) {
      const cur = (Array.isArray(nextConn[toKind]) ? nextConn[toKind] : []) as Record<string, unknown>[]
      nextConn[toKind] = [...cur, queryDef]
    }
    setConns((p) => ({ ...(p ?? {}), [sel]: nextConn }))
    setSelTable(null); setSelQuery(null); setStatus(null)
    setMode(modeFor(toKind))
  }

  // Handler for the scaffold modal save: append the new query to the connector + the new
  // dictionary entry under the connector's per-connector scope (creating the scope if it
  // doesn't exist yet — same convention DictionaryBuilder uses). The operator then hits the
  // top-toolbar Save to commit both files; this just stages the edits in memory.
  const onScaffoldSave = (kind: ScaffoldKind, result: { query: { name: string; sql: string; label?: string }; dictEntry: Record<string, unknown>; dictId: string }) => {
    if (!sel || !conns || !dictionary) return
    // 1) append the scaffolded query to the matching section (sequences / lookups).
    const section = kind === 'sequence' ? 'sequences' : 'lookups'
    const conn = conns[sel] ?? {}
    const existing = Array.isArray(conn[section]) ? (conn[section] as Record<string, unknown>[]) : []
    updateSection(sel, section, [...existing, { ...result.query }])
    // 2) write the dict entry under the connector's per-connector scope (creating it when
    //    absent). The kind picks ``sequences`` vs ``lookups``; the dict key is ``result.dictId``.
    const dictKey = kind === 'sequence' ? 'sequences' : 'lookups'
    setDictionary((prev) => {
      const cur = prev ?? { entries: {}, enums: {}, lookups: {}, sequences: {}, connectors: {} }
      const connectors = { ...(cur.connectors ?? {}) }
      const scope = { ...(connectors[sel] ?? {}) }
      const section = { ...((scope as Record<string, Record<string, Record<string, unknown>>>)[dictKey] ?? {}) }
      section[result.dictId] = result.dictEntry
      ;(scope as Record<string, unknown>)[dictKey] = section
      connectors[sel] = scope as typeof connectors[string]
      return { ...cur, connectors }
    })
    setScaffoldKind(null)
    setSelQuery(result.query.name)   // jump to the new query so the operator can tweak its SQL
  }

  async function save() {
    if (!conns) return
    setBusy(true); setError(null); setStatus(null)
    try {
      const connsDirty = JSON.stringify(conns) !== original
      const dictDirty = dictionary != null && JSON.stringify(dictionary) !== dictOriginal
      if (connsDirty) await api.put<{ saved: boolean }>('/admin/config/connectors/parsed', { connectors: conns })
      // Scaffold writes a sequence / lookup entry into dictionary.toml — PUT here so a single
      // Save commits both files atomically (from the operator's view). The reload below then
      // re-reads everything; ``load()`` resets both originals so the dirty flag clears.
      if (dictDirty) await api.put<{ saved: boolean }>('/admin/config/dictionary/parsed', { dictionary })
      const r = await api.post<{ connectors: string[] }>('/admin/reload')
      // Bump the workspace nonce so every consumer (the ScreenDesigner's action editor,
      // the dashboards, …) refetches /api/connectors + /api/screens with the new shape.
      // Without this, freshly-added endpoint params don't surface in the call_api action's
      // param-bind dropdown until the operator hard-refreshes the page.
      refreshWorkspace()
      setStatus(t('settings.connectors.saved', { connectors: r.connectors.join(', ') || `(${t('common.none')})` }))
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  // (Sequences / Lookups tabs now filter by each query's explicit ``type`` — see ``sequenceQueries``
  // / ``lookupQueries`` below — so the old dictionary-reference scan that determined them is gone.)

  if (error && !conns) return <Banner $tone="error">{error}</Banner>
  if (!conns || !schemas) return <Centered />
  // Alphabetical sort — operators expect a stable name-sorted list (the v1 ``apps_seq``
  // ordering is gone). ``localeCompare`` handles mixed case naturally.
  const names = Object.keys(conns).sort((a, b) => a.localeCompare(b))
  const needle = q.trim().toLowerCase()
  const shownNames = needle ? names.filter((n) => n.toLowerCase().includes(needle)) : names
  // Group the list: a connector with a menu is an "app"; the rest are plain data sources. Makes
  // "what's an app vs a backing data source (jdedwards, ais_connection…)" legible at a glance.
  const appSet = new Set(menus ? Object.keys(menus) : [])
  const shownAppNames = shownNames.filter((n) => appSet.has(n))
  const shownDataNames = shownNames.filter((n) => !appSet.has(n))
  const selConn = sel && conns[sel] ? conns[sel] : null
  const selSchema = selConn ? schemaFor(selConn) : null
  const isSql = selConn?.type !== 'api'

  // --- query classification by explicit ``type`` ----------------------------
  // The connector is in the sectioned shape — read each section directly. No more name-suffix /
  // ``type``-field inference: the section a query sits in IS its kind.
  const tablesArr = (selConn && isSql && Array.isArray(selConn.tables) ? selConn.tables : []) as TableEntry[]
  const customQueries = (selConn && isSql && Array.isArray(selConn.queries) ? selConn.queries : []) as Record<string, unknown>[]
  const sequenceQueries = (selConn && isSql && Array.isArray(selConn.sequences) ? selConn.sequences : []) as Record<string, unknown>[]
  const lookupQueries = (selConn && isSql && Array.isArray(selConn.lookups) ? selConn.lookups : []) as Record<string, unknown>[]
  // Every query name across all four sections — used to guard new-name collisions in the scaffold /
  // CRUD-wizard modals (a custom query can't reuse a table slot's synthesised name, etc.).
  const allQueryNames = (() => {
    const s = new Set<string>()
    for (const tb of tablesArr) for (const c of CRUD_KINDS) if (tb[c]) s.add(`${tb.name}_${c}`)
    for (const arr of [customQueries, sequenceQueries, lookupQueries]) for (const q of arr) if (typeof q.name === 'string') s.add(q.name)
    return s
  })()
  const tNeedle = tq.trim().toLowerCase()
  // Display sorted by name (the on-disk order is preserved — this only orders the list view),
  // matching the Queries / Sequences / Lookups lists below.
  const shownTables = (tNeedle ? tablesArr.filter((tb) => tb.name.toLowerCase().includes(tNeedle)) : tablesArr)
    .slice().sort((a, b) => a.name.localeCompare(b.name))
  const queryDefSchema = (selSchema?.$defs?.QueryDef ?? null) as JsonSchema | null
  const tableDefSchema = (selSchema?.$defs?.TableDef ?? null) as JsonSchema | null
  const crudSlotSchema = (selSchema?.$defs?.CrudSlot ?? null) as JsonSchema | null
  const allDefs = (selSchema?.$defs ?? {}) as Record<string, JsonSchema>
  // QueryDef minus `name`: the name is display-only in the editor and renamed via the cross-file
  // Rename button — editing it inline would silently break every reference (screens / dictionary /
  // charts / dashboards / menus). (Plain const, not useMemo — we're past the early returns.)
  const queryEditorSchema: JsonSchema | null = (() => {
    const props = (queryDefSchema as { properties?: Record<string, unknown> } | null)?.properties
    if (!queryDefSchema || !props) return queryDefSchema
    const rest = Object.fromEntries(Object.entries(props).filter(([k]) => k !== 'name'))
    const required = ((queryDefSchema as { required?: string[] }).required ?? []).filter((r) => r !== 'name')
    return { ...(queryDefSchema as object), properties: rest, required } as JsonSchema
  })()

  // Single-query editor (the SchemaForm over QueryDef) — used by the Unclassified / Sequences /
  // Lookups views. The name is shown read-only with a Rename button (cross-file rename); everything
  // else edits inline. Caller guards on ``queryDefSchema`` being loaded.
  const renderQueryEditor = (picked: Record<string, unknown>) => (
    <Stack gap={10}>
      <Row gap={8} style={{ alignItems: 'center' }}>
        <Button $variant="ghost" $size="sm" onClick={() => setSelQuery(null)}>
          ← {t('common.back')}
        </Button>
        <Mono style={{ fontSize: fontSize.sm, flex: 1 }}>{String(picked.name)}</Mono>
        <Button $variant="ghost" $size="sm" onClick={() => sel && setUsagesTarget({
          kind: 'query', name: String(picked.name), scope: sel, label: `query ${sel}.${picked.name}`,
        })} disabled={busy}>
          <GitBranch size={13} /> {t('findUsages.button', 'Find usages')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={() => renameQuery(String(picked.name))} disabled={busy}>
          <Edit3 size={13} /> {t('settings.rename.button')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={() => changeType(curSection().key as SectionKind, String(picked.name))} disabled={busy}>
          <Shuffle size={13} /> {t('settings.connectors.changeTypeButton', 'Change type')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={() => {
          const k = curSection().key
          setMoveTarget({ kind: k === 'sequences' ? 'sequence' : k === 'lookups' ? 'lookup' : 'query', name: String(picked.name) })
        }} disabled={busy}>
          <ArrowRightLeft size={13} /> {t('settings.connectors.moveButton', 'Move')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={() => duplicateQuery(String(picked.name))} disabled={busy}>
          <Copy size={13} /> {t('settings.tables.duplicate')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={() => void deleteQuery(String(picked.name))} disabled={busy} style={{ color: colors.red.main }}>
          <Trash2 size={13} /> {t('common.delete')}
        </Button>
      </Row>
      <SqlConnectorContext.Provider value={sel ?? undefined}>
        <SchemaForm
          schema={queryEditorSchema as JsonSchema}
          defs={allDefs}
          value={picked}
          onChange={(v: Record<string, unknown>) => {
            // The form omits `name`, so preserve it on the patched query.
            const merged = { ...v, name: picked.name }
            const { key, arr } = curSection()
            updateSection(sel!, key, arr.map((qq) => (qq.name === picked.name ? merged : qq)))
          }}
        />
      </SqlConnectorContext.Provider>
    </Stack>
  )

  return (
    <FrameworkEnumsContext.Provider value={augmentedEnums}>
    <Shell>
      {/* One consolidated top toolbar — config path + status on the left, Save + Reload on the
          right. Replaces the old "config path at top + bottom Save Row" split — the operator
          never has to scroll past a long connectors list to reach Save / Reload. Add (sql/api)
          and Delete actions live on the connector list / detail panes since they're per-row. */}
      <Toolbar>
        <ToolbarLeft>
          {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
          {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
          {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        </ToolbarLeft>
        <ToolbarRight>
          {/* One "+ Connector" entry — the kind (sql / api) is chosen in the modal. Per-connector
              actions (Clone, Rename, Delete) live in the right panel's Settings tab where they
              target the selected connector. */}
          <Button $variant="ghost" $size="sm" onClick={openAddConnector} disabled={busy}>
            <Plus size={13} /> {t('settings.connectors.addConnector', 'Connector')}
          </Button>
          <ToolbarDivider />
          <Button $variant="ghost" $size="sm" onClick={discard} disabled={busy || !dirty} title={t('common.discard', 'Discard')}>
            <Undo2 size={13} /> {t('common.discard', 'Discard')}
          </Button>
          <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
          </Button>
        </ToolbarRight>
      </Toolbar>
      <Split>
        <NavCol>
          {names.length > 6 && (
            <NavSearch>
              <Search size={13} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`filter ${names.length}…`} />
            </NavSearch>
          )}
          <NavList>
            {shownAppNames.length > 0 && <NavGroupLabel>{t('settings.connectors.groupApps', 'Apps')}</NavGroupLabel>}
            {shownAppNames.map((n) => (
              <NavItem key={n} $active={n === sel} onClick={() => { setSel(n); setStatus(null) }}>
                {conns[n].type === 'api' ? <Globe size={13} /> : <Database size={13} />} <span className="name">{n}</span>
              </NavItem>
            ))}
            {shownDataNames.length > 0 && <NavGroupLabel>{t('settings.connectors.groupData', 'Data sources')}</NavGroupLabel>}
            {shownDataNames.map((n) => (
              <NavItem key={n} $active={n === sel} onClick={() => { setSel(n); setStatus(null) }}>
                {conns[n].type === 'api' ? <Globe size={13} /> : <Database size={13} />} <span className="name">{n}</span>
              </NavItem>
            ))}
            {shownNames.length === 0 && <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '2px 4px' }}>no match</div>}
          </NavList>
          {/* "Add SQL" / "Add API" / "Delete" moved to the top toolbar — keeps every connector-
              level action in one place at the top, no scrolling past the list. */}
        </NavCol>
        <FormCol>
          {selConn && selSchema ? (
            <Stack gap={12}>
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <Row gap={10} style={{ alignItems: 'center' }}>
                  <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>
                    [connectors.{sel}] <span style={{ color: colors.text.muted, fontWeight: 400 }}>· {String(selConn.type)}</span>
                  </strong>
                  {/* Mode switcher — Tables / Sequences / Lookups. Filters which queries are
                      shown + which editor opens on click. SQL connectors only — API connectors
                      have no CRUD / sequence / lookup grouping (queries aren't even a concept
                      there). The 3-mode set comes from the user: tables (CRUD), sequences
                      (queries referenced by ``[sequences.*]``), lookups (queries referenced by
                      ``[lookups.*]``). Loose queries that don't fit any of the three stay in
                      the Settings… modal. */}
                  {isSql && (
                    <ModeBar>
                      <ModeBtn type="button" $active={mode === 'settings'} onClick={() => setMode('settings')}>
                        <FileCog size={13} /> {t('settings.connectors.settings', 'Settings')}
                      </ModeBtn>
                      <ModeBtn type="button" $active={mode === 'tables'} onClick={() => setMode('tables')}>
                        {t('settings.tables.tablesView')}
                      </ModeBtn>
                      <ModeBtn type="button" $active={mode === 'custom'} onClick={() => setMode('custom')}>
                        {t('settings.tables.looseSectionLabel', 'Queries')}
                      </ModeBtn>
                      <ModeBtn type="button" $active={mode === 'sequences'} onClick={() => setMode('sequences')}>
                        {t('settings.connectors.sequencesView', 'Sequences')}
                      </ModeBtn>
                      <ModeBtn type="button" $active={mode === 'lookups'} onClick={() => setMode('lookups')}>
                        {t('settings.connectors.lookupsView', 'Lookups')}
                      </ModeBtn>
                    </ModeBar>
                  )}
                </Row>
                <Row gap={8}>
                  {/* Per-mode Add button — Tables / Sequences / Lookups each need their own
                      creator. Tables: prompt-for-name + scaffold a ``<base>_get`` query stub
                      (existing flow). Sequences / Lookups: open ScaffoldQueryModal which
                      introspects the pool, lets the operator pick a table + column(s), and
                      writes both the new query AND the matching dictionary entry. Hidden in
                      the single-query editor (selQuery set) so it doesn't collide with the
                      Back-to-list affordance. */}
                  {isSql && mode === 'tables' && !selTable && !selQuery && (
                    <Button $variant="ghost" $size="sm" onClick={() => sel && addTable(sel)} disabled={busy}>
                      <Plus size={13} /> {t('settings.tables.addTable')}
                    </Button>
                  )}
                  {isSql && mode === 'custom' && !selQuery && (
                    <Button $variant="ghost" $size="sm" onClick={addCustomQuery} disabled={busy}>
                      <Plus size={13} /> {t('settings.connectors.addQuery', 'Add query')}
                    </Button>
                  )}
                  {isSql && mode === 'sequences' && !selQuery && (
                    <Button $variant="ghost" $size="sm" onClick={() => setScaffoldKind('sequence')} disabled={busy}>
                      <Plus size={13} /> {t('settings.connectors.addSequence', 'Add sequence')}
                    </Button>
                  )}
                  {isSql && mode === 'lookups' && !selQuery && (
                    <Button $variant="ghost" $size="sm" onClick={() => setScaffoldKind('lookup')} disabled={busy}>
                      <Plus size={13} /> {t('settings.connectors.addLookup', 'Add lookup')}
                    </Button>
                  )}
                  {/* Connector-level actions (Clone / Rename / Delete) sit at the top-right of the
                      right panel — shown on the SQL Settings tab and always for API connectors
                      (which have no mode strip). Delete cascades: it removes the connector and
                      everything scoped under it (dictionary overlay, menu, screens, charts,
                      dashboards). */}
                  {(!isSql || mode === 'settings') && (
                    <>
                      <Button $variant="ghost" $size="sm" onClick={() => sel && setUsagesTarget({
                        kind: 'connector', name: sel, label: `connector ${sel}`,
                      })} disabled={busy}>
                        <GitBranch size={13} /> {t('findUsages.button', 'Find usages')}
                      </Button>
                      <Button $variant="ghost" $size="sm" onClick={openCloneApp} disabled={busy || dirty}
                        title={dirty ? t('settings.connectors.cloneRequiresSave', 'Save current edits first') : t('settings.connectors.clone', 'Clone')}>
                        <Layers size={13} /> {t('settings.connectors.clone', 'Clone')}
                      </Button>
                      <Button $variant="ghost" $size="sm" onClick={() => sel && renameConnector(sel)} disabled={busy}>
                        <Edit3 size={13} /> {t('settings.rename.button')}
                      </Button>
                      <Button $variant="ghost" $size="sm" onClick={() => sel && deleteApp(sel)} disabled={busy || dirty}
                        title={dirty ? t('settings.connectors.deleteRequiresSave', 'Save current edits first') : t('settings.connectors.delete', 'Delete')}
                        style={{ color: colors.red.main }}>
                        <Trash2 size={13} /> {t('common.delete', 'Delete')}
                      </Button>
                    </>
                  )}
                </Row>
              </Row>
              {!isSql && (
                // API connectors get a dedicated 5-tab editor (Connection / Authentication /
                // Endpoints / Webhooks / Test) — matches the nomaubl shape, with conditional
                // auth fields per method and an in-place Test tab that fires through
                // ``POST /admin/config/api/test`` against the operator's *in-progress* config
                // (no need to save first). SchemaNavigator was the previous render path; it
                // surfaced 20+ flat fields including OAuth2 ones that didn't apply to a basic
                // connector and vice versa, which made the editor noisy and hard to scan.
                <ApiConnectorEditor name={sel!} value={selConn as ApiConnectorEditorValue} hasMenu={appSet.has(sel!)} onChange={(v) => update(sel!, v as unknown as Record<string, unknown>)} />
              )}
              {/* Tables view = CRUD-grouped tables ONLY. Custom (non-CRUD) queries live in their
                  own tab now, so this list isn't mixed with them anymore. */}
              {isSql && mode === 'tables' && (
                selTable && tableDefSchema && crudSlotSchema ? (() => {
                  const table = tablesArr.find((tb) => tb.name === selTable)
                  if (!table) return <Empty>{t('settings.tables.emptyConnector')}</Empty>
                  // The corresponding Screen (if any) is keyed by (connector, get-slot name).
                  const getName = table.get ? `${table.name}_get` : undefined
                  const matchedScreen = sel && getName ? findScreen(sel, getName) : null
                  return (
                    <ConnectorsTableEditor
                      table={table}
                      connectorName={sel!}
                      tableDefSchema={tableDefSchema}
                      crudSlotSchema={crudSlotSchema}
                      defs={allDefs}
                      onChange={(next) => updateTables(sel!, replaceTableByName(tablesArr, selTable, next))}
                      onDelete={() => { updateTables(sel!, removeTableByName(tablesArr, selTable)); setSelTable(null) }}
                      onBack={() => setSelTable(null)}
                      onDuplicate={() => sel && duplicateTable(sel, selTable)}
                      onRename={() => renameTable(selTable)}
                      onChangeType={() => changeType('tables', selTable)}
                      onMove={() => setMoveTarget({ kind: 'table', name: selTable })}
                      onFindUsages={() => {
                        if (!sel) return
                        // Target the read slot (else the first present slot) — the dependent
                        // screens / menus reference the table through its slot query names.
                        const crud = CRUD_KINDS.find((c) => table[c]) ?? 'get'
                        const qn = `${table.name}_${table.get ? 'get' : crud}`
                        setUsagesTarget({ kind: 'query', name: qn, scope: sel, label: `table ${sel}.${table.name}` })
                      }}
                      screenLink={matchedScreen ? { app: matchedScreen.app, id: matchedScreen.id } : null}
                      onOpenScreen={(app, id) => setScreenDesigner({ app, id })}
                    />
                  )
                })() : (
                  <Stack gap={10}>
                    {tablesArr.length > 6 && (
                      <NavSearch>
                        <Search size={13} />
                        <input value={tq} onChange={(e) => setTq(e.target.value)} placeholder={`filter ${tablesArr.length}…`} />
                      </NavSearch>
                    )}
                    {shownTables.length === 0 && tablesArr.length > 0 && (
                      <Empty>{t('common.noMatches')}</Empty>
                    )}
                    {tablesArr.length === 0 && (
                      <Empty>{t('settings.tables.emptyConnector')}</Empty>
                    )}
                    <TableList>
                      {shownTables.map((tb) => {
                        // Friendly subtitle — the table's own `description` (else `label`), which
                        // lives on the TableEntry itself (not stranded on a `_get` slot).
                        const desc = (typeof tb.description === 'string' && tb.description)
                                  || (typeof tb.label === 'string' && tb.label)
                                  || null
                        return (
                          <TableRow key={tb.name} type="button" onClick={() => setSelTable(tb.name)}>
                            <span className="text">
                              <span className="base">{tb.name}</span>
                              {desc && <span className="desc">{desc}</span>}
                            </span>
                            {CRUD_KINDS.map((c) => (
                              <Slot key={c} $on={!!tb[c]} title={tb[c] ? `${tb.name}_${c}` : `${tb.name}_${c} (missing)`}>{c.toUpperCase().slice(0, 3)}</Slot>
                            ))}
                            <RowAction
                              role="button"
                              aria-label={t('settings.tables.duplicate')}
                              title={t('settings.tables.duplicate')}
                              onClick={(e) => { e.stopPropagation(); if (sel) duplicateTable(sel, tb.name) }}
                            >
                              <Copy size={13} />
                            </RowAction>
                          </TableRow>
                        )
                      })}
                    </TableList>
                    {/* "Add table" lives in the top toolbar (per-mode Add cluster). Custom queries
                        moved to their own tab — this view is CRUD tables only now. */}
                  </Stack>
                )
              )}
              {/* Custom / Sequences / Lookups views — a flat list of single queries, each opening
                  the same per-query SchemaForm (QueryDef) on click. Each tab shows the queries whose
                  ``type`` matches it, so a query shows in exactly one tab. The dictionary def itself
                  (the sequence/lookup → column binding) stays editable in DictionaryBuilder — this
                  edits the SQL + params for the query. */}
              {isSql && (mode === 'custom' || mode === 'sequences' || mode === 'lookups') && (() => {
                const matches: Record<string, unknown>[] = (
                  mode === 'custom' ? customQueries : mode === 'sequences' ? sequenceQueries : lookupQueries
                ).slice().sort((a, b) => String(a.name).localeCompare(String(b.name)))
                const qNeedle = tq.trim().toLowerCase()
                const shown = qNeedle ? matches.filter((q) => String(q.name).toLowerCase().includes(qNeedle)) : matches
                const picked = selQuery ? matches.find((q) => q.name === selQuery) : null
                if (picked && queryDefSchema) {
                  return renderQueryEditor(picked as Record<string, unknown>)
                }
                return (
                  <Stack gap={10}>
                    {matches.length > 6 && (
                      <NavSearch>
                        <Search size={13} />
                        <input value={tq} onChange={(e) => setTq(e.target.value)} placeholder={`filter ${matches.length}…`} />
                      </NavSearch>
                    )}
                    {matches.length === 0 ? (
                      <Empty>
                        {mode === 'custom'
                          ? t('settings.connectors.emptyCustom', 'No unclassified queries. These are queries not tied to a table’s get/put/post/delete set and not used as a sequence or lookup — click "Add query" to create one.')
                          : mode === 'sequences'
                            ? t('settings.connectors.emptySequences', 'No sequences defined for this connector. Add one in Dictionary → Sequences and point its `query` at a query here.')
                            : t('settings.connectors.emptyLookups', 'No lookups defined for this connector. Add one in Dictionary → Lookups and point its `query` at a query here.')}
                      </Empty>
                    ) : (
                      <TableList>
                        {shown.map((q) => {
                          const name = String(q.name)
                          const desc = typeof q.description === 'string' && q.description
                            ? q.description
                            : typeof q.label === 'string' && q.label
                              ? q.label
                              : null
                          return (
                            <TableRow key={name} type="button" onClick={() => setSelQuery(name)}>
                              <span className="text">
                                <span className="base">{name}</span>
                                {desc && <span className="desc">{desc}</span>}
                              </span>
                            </TableRow>
                          )
                        })}
                      </TableList>
                    )}
                  </Stack>
                )
              })()}
              {/* Settings tab — connector-wide config (pool / licensed / max_rows). ``type`` is the
                  discriminator: set at creation, fixed after, and already shown next to the
                  connector name in the header — so it's not editable here. Clone / Rename / Delete
                  live at the top-right of this panel. For always-licensed connectors the
                  ``licensed`` checkbox is dropped from the form and a locked banner replaces it —
                  the loader gates these regardless of the on-disk flag, so making it look editable
                  would be a lie. */}
              {isSql && mode === 'settings' && selConn && selSchema && (() => {
                const locked = sel != null && alwaysLicensed.has(sel)
                const fieldKeys = locked
                  ? ['pool', 'pools', 'show_in_switcher', 'home', 'max_rows']
                  : ['pool', 'pools', 'show_in_switcher', 'home', 'licensed', 'max_rows']
                const settingsSchema = pickSchemaProperties(selSchema, fieldKeys)
                return (
                  <SqlConnectorContext.Provider value={sel ?? undefined}>
                    {locked && (
                      <Banner $tone="info">
                        {t(
                          'settings.connectors.alwaysLicensed',
                          'License: required (this connector is always licensed — the checkbox is locked).',
                        )}
                      </Banner>
                    )}
                    <SchemaForm
                      schema={settingsSchema}
                      defs={allDefs}
                      value={selConn as Record<string, unknown>}
                      onChange={(v: Record<string, unknown>) => {
                        const patch: Record<string, unknown> = {}
                        for (const k of fieldKeys) patch[k] = v[k]
                        // Keep ``licensed`` pinned to true for locked connectors — the backend
                        // enforces this on save too, but mirroring it client-side avoids a
                        // misleading "unsaved" badge when the user hasn't actually changed anything.
                        if (locked) patch.licensed = true
                        update(sel!, { ...selConn, ...patch })
                      }}
                    />
                  </SqlConnectorContext.Provider>
                )
              })()}
            </Stack>
          ) : (
            <Empty>{names.length ? t('settings.connectors.pickOne') : t('settings.connectors.empty')}</Empty>
          )}
        </FormCol>
      </Split>
      {/* Scaffold modal — Add sequence / Add lookup. Picks a table + column(s) from the
          connector's live pool introspection, generates the SQL, and on save stages both a
          new query (in this connector) and a matching dictionary entry (under the
          connector's scope). The top-toolbar Save commits both files together. */}
      {scaffoldKind && sel && (
        <ScaffoldQueryModal
          kind={scaffoldKind}
          connector={sel}
          existingDictIds={(() => {
            // The "existing ids" set comes from the same dict scope the save handler writes
            // to — per-connector ``[connectors.<sel>.<sequences|lookups>.*]``. Operator can't
            // pick an id that already exists there.
            const section = scaffoldKind === 'sequence' ? 'sequences' : 'lookups'
            const perConn = (dictionary?.connectors ?? {}) as Record<string, Record<string, Record<string, Record<string, unknown>>>>
            const ids = Object.keys(perConn[sel]?.[section] ?? {})
            return new Set(ids)
          })()}
          existingQueryNames={allQueryNames}
          onSave={(result) => onScaffoldSave(scaffoldKind, result)}
          onCancel={() => setScaffoldKind(null)}
        />
      )}
      {/* CRUD wizard — opened from "+ Add table → Generate from DB". Groups the four generated
          queries into ONE table entry (replace-by-name) + auto-selects it in the list so the
          operator can keep tweaking inside ConnectorsTableEditor. The top-toolbar Save commits. */}
      {crudWizardOpen && sel && (
        <CrudWizardModal
          connector={sel}
          existingQueryNames={allQueryNames}
          onSave={(result) => {
            const tb = tableEntryFromFlatQueries(result.queries)
            updateTables(sel, replaceTableByName(tablesArr, tb.name, tb))
            setCrudWizardOpen(false)
            // Jump straight to the new table so the operator can tweak the auto-generated SQL.
            if (tb.name) setSelTable(tb.name)
            // Chain the "create the missing dictionary items" step — scans the just-reversed
            // table's columns + proposes dictionary entries (JDE DD / inferred type).
            setDictScan({ connector: sel, table: result.table, schema: result.schema })
          }}
          onCancel={() => setCrudWizardOpen(false)}
        />
      )}
      {screenDesigner && (
        <ScreenDesignerModal
          app={screenDesigner.app}
          screenId={screenDesigner.id}
          onClose={() => setScreenDesigner(null)}
        />
      )}
      {dictScan && (
        <DictionaryScan
          connector={dictScan.connector}
          table={dictScan.table}
          schema={dictScan.schema}
          scope={dictScan.connector}
          onClose={() => setDictScan(null)}
        />
      )}
      {cloneModalOpen && (
        // No backdrop-click-to-close — outside clicks must not abandon the clone form mid-fill.
        <Overlay>
          <Modal style={{ width: 'min(560px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
            <ModalHeader>{t('settings.connectors.clone', 'Clone')}</ModalHeader>
            <ModalBody>
              <div style={{ color: colors.text.muted, fontSize: fontSize.sm }}>
                {t(
                  'settings.connectors.cloneHint',
                  'Duplicates the source app\'s connector, dictionary overlay, menus, and screens under a new name. The new connector points at the new pool — create that pool first via Settings → Pools (and the underlying database via CREATE DATABASE on Postgres).',
                )}
              </div>
              <Row style={{ flexDirection: 'column', alignItems: 'stretch', gap: 12 }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: fontSize.sm }}>
                  <span style={{ color: colors.text.muted }}>{t('settings.connectors.cloneSource', 'Source connector')}</span>
                  <SearchSelect
                    value={cloneSource}
                    options={Object.keys(conns ?? {}).map((n) => ({ value: n, label: n, mono: n }))}
                    onChange={(v) => setCloneSource(v)}
                    placeholder={t('common.pick', 'Pick…')}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: fontSize.sm }}>
                  <span style={{ color: colors.text.muted }}>{t('settings.connectors.cloneNewApp', 'New name')}</span>
                  <Input
                    value={cloneNewApp}
                    onChange={(e) => setCloneNewApp(e.target.value)}
                    placeholder="nomasx1b"
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: fontSize.sm }}>
                  <span style={{ color: colors.text.muted }}>{t('settings.connectors.cloneNewPool', 'Pool')}</span>
                  <SearchSelect
                    value={cloneNewPool}
                    options={pools.map((n) => ({ value: n, label: n, mono: n }))}
                    onChange={(v) => setCloneNewPool(v)}
                    placeholder={t('common.pick', 'Pick…')}
                  />
                </label>
              </Row>
              {cloneError && <Banner $tone="error">{cloneError}</Banner>}
            </ModalBody>
            <ModalFooter>
              <Button $variant="ghost" onClick={() => setCloneModalOpen(false)} disabled={cloneBusy}>
                {t('common.cancel', 'Cancel')}
              </Button>
              <Button
                $variant="primary"
                onClick={submitCloneApp}
                disabled={cloneBusy || !cloneSource || !cloneNewApp || !cloneNewPool}
              >
                {cloneBusy ? <SpinnerRing size={13} thickness={2} /> : <Copy size={13} />}{' '}
                {t('settings.connectors.cloneAppRun', 'Clone')}
              </Button>
            </ModalFooter>
          </Modal>
        </Overlay>
      )}
      {/* "+ Connector" — type (sql / api) + name. The type is the discriminator: chosen here at
          creation, fixed afterwards (so it isn't shown/editable in the Settings tab). */}
      {addConnOpen && (
        // No backdrop-click-to-close — outside clicks must not abandon the new-connector form.
        <Overlay>
          <Modal style={{ width: 'min(440px, 94vw)' }} onClick={(e) => e.stopPropagation()}>
            <ModalHeader>{t('settings.connectors.addConnectorTitle', 'Add a connector')}</ModalHeader>
            <ModalBody>
              <Row style={{ flexDirection: 'column', alignItems: 'stretch', gap: 12 }}>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: fontSize.sm }}>
                  <span style={{ color: colors.text.muted }}>{t('settings.connectors.type', 'Type')}</span>
                  <SearchSelect
                    value={addConnType}
                    options={[
                      { value: 'sql', label: t('settings.connectors.typeSql', 'SQL') },
                      { value: 'api', label: t('settings.connectors.typeApi', 'API') },
                    ]}
                    onChange={(v) => setAddConnType(v === 'api' ? 'api' : 'sql')}
                  />
                </label>
                <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: fontSize.sm }}>
                  <span style={{ color: colors.text.muted }}>{t('settings.connectors.name', 'Name')}</span>
                  <Input value={addConnName} onChange={(e) => { setAddConnName(e.target.value); setAddConnError(null) }}
                    placeholder="nomasx1" autoFocus
                    onKeyDown={(e) => { if (e.key === 'Enter') submitAddConnector() }} />
                </label>
              </Row>
              {addConnError && <Banner $tone="error">{addConnError}</Banner>}
            </ModalBody>
            <ModalFooter>
              <Button $variant="ghost" $size="sm" onClick={() => setAddConnOpen(false)}>{t('common.cancel', 'Cancel')}</Button>
              <Button $variant="primary" $size="sm" onClick={submitAddConnector}>{t('common.ok', 'OK')}</Button>
            </ModalFooter>
          </Modal>
        </Overlay>
      )}
      {usagesTarget && <FindUsagesModal target={usagesTarget} onClose={() => setUsagesTarget(null)} />}
      {moveTarget && sel && (
        <MoveQueryModal
          kind={moveTarget.kind}
          name={moveTarget.name}
          fromConnector={sel}
          candidates={Object.keys(conns ?? {}).filter((n) => n !== sel && (conns?.[n]?.type ?? 'sql') !== 'api')}
          onMoved={() => { load(); void refreshWorkspace() }}
          onClose={() => { setMoveTarget(null); setSelQuery(null); setSelTable(null) }}
        />
      )}
    </Shell>
    </FrameworkEnumsContext.Provider>
  )
}
