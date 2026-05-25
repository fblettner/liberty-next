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
import { Save, RefreshCw, Plus, Trash2, Database, Globe, Search, FileCog, Copy, Edit3, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, Mono, SchemaForm, FrameworkEnumsContext, SqlConnectorContext, useModals, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, type FrameworkEnum, type FrameworkEnums, type JsonSchema } from '../../common'
import type { ConfigSchemas, ConnectorsDoc, DictionaryDoc } from '../../types/config'
import ApiConnectorEditor, { type ApiConnector as ApiConnectorEditorValue } from './ApiConnectorEditor'
import { validateRename } from '../../services/keyRename'
import { colors, fontSize, fonts, radius } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import ConnectorsTableEditor from './ConnectorsTableEditor'
import { CRUD_KINDS, duplicateTable as duplicateTableQueries, groupQueriesByTable, newQueryStub, pickSchemaProperties, tableExists } from './connectorTables'
import { ScaffoldQueryModal, type ScaffoldKind } from './ScaffoldQueryModal'
import { CrudWizardModal } from './CrudWizardModal'

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
const LooseNote = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 8px 4px 0;`

export default function ConnectorsBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { findScreen, refresh: refreshWorkspace } = useWorkspace()
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
  // Tables / Sequences / Lookups view — the connector list is the same underneath, the mode
  // just filters which queries you see and which editor they open. Tables = CRUD-grouped
  // queries (the canonical case). Sequences = queries referenced by ``[sequences.*]`` in
  // dictionary.toml. Lookups = queries referenced by ``[lookups.*]``. The "loose" queries
  // (none of the three) stay accessible via the Settings… escape hatch.
  const [mode, setMode] = useState<'tables' | 'sequences' | 'lookups'>('tables')
  // ``settingsOpen`` raises a small connector-level form (label / pool / licensed / max_rows /
  // description). Was the old "Form view" toggle — promoted to a modal so the operator only
  // sees those rare fields when they ask for them.
  const [settingsOpen, setSettingsOpen] = useState(false)
  // Scaffold modal — opened from the Sequences / Lookups mode's "Add" button. The modal
  // pulls live pool introspection, the operator picks a table + column(s), and on save we
  // append the generated query to the connector + the matching ``[sequences.<id>]`` /
  // ``[lookups.<id>]`` entry to dictionary.toml (under the connector's scope when one exists,
  // else shared). Both files are written together on the next Save click.
  const [scaffoldKind, setScaffoldKind] = useState<ScaffoldKind | null>(null)
  // CRUD wizard modal — opened from "+ Add table → Generate from DB". Reverse-engineers a table
  // into all four CRUD queries via the live pool introspection.
  const [crudWizardOpen, setCrudWizardOpen] = useState(false)
  const [selTable, setSelTable] = useState<string | null>(null)
  // Selected single-query name when ``mode === 'sequences'`` or ``'lookups'``.
  const [selQuery, setSelQuery] = useState<string | null>(null)
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
        setSchemas(s); setConns(d.connectors); setOriginal(JSON.stringify(d.connectors))
        setDictionary(dd.dictionary); setDictOriginal(JSON.stringify(dd.dictionary))
        setSel((cur) => (cur && d.connectors[cur] ? cur : Object.keys(d.connectors)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])

  // reset table selection + search when switching connectors
  useEffect(() => { setSelTable(null); setSelQuery(null); setTq(''); setSettingsOpen(false); setMode('tables') }, [sel])
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
  const addConnector = async (type: 'sql' | 'api') => {
    const name = (await modals.prompt({
      title: type === 'api' ? t('settings.connectors.addApi') : t('settings.connectors.addSql'),
      message: t('settings.connectors.namePrompt'),
    }))?.trim()
    if (!name) return
    if (conns && name in conns) { setSel(name); return }
    setConns((p) => ({ ...(p ?? {}), [name]: type === 'api' ? { type: 'api', base_url: '' } : { type: 'sql', queries: [] } }))
    setSel(name); setStatus(null)
  }
  // Note: API connector testing is no longer a toolbar action — it lives in the editor's
  // Test tab (see ``ApiConnectorEditor``), where the operator picks an endpoint, supplies
  // its placeholder values, and sees the result in-place. Backed by the same
  // ``POST /admin/config/api/test`` endpoint; that endpoint accepts ``test_endpoint`` +
  // ``params`` so it can fire any named endpoint without saving first.
  const removeConnector = async (name: string) => {
    const ok = await modals.confirm({
      title: t('settings.connectors.delete'),
      message: t('settings.connectors.confirmDelete', { name }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    setConns((p) => { const next = { ...(p ?? {}) }; delete next[name]; return next })
    setSel((s) => (s === name ? null : s)); setStatus(null)
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
    const existing = Object.keys(conns)
    const next = (await modals.prompt({
      title: t('settings.rename.button'),
      message: t('settings.connectors.renamePrompt', { name: oldName }),
      defaultValue: oldName,
      submitLabel: t('settings.rename.button'),
      validate: (v) => {
        const err = validateRename(oldName, v, existing)
        if (err === 'unchanged') return null
        if (err === 'empty') return t('settings.rename.empty')
        if (err === 'exists') return t('settings.rename.exists', { name: v })
        // v2 identifier shape (must match the backend's regex). Same rule slugify enforces on
        // migration so the rename produces a name that reads like a TOML key, a permission
        // string, and a URL segment all at once.
        if (!/^[a-z][a-z0-9_]*$/.test(v)) return t('settings.rename.invalidIdentifier')
        return null
      },
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
    })
    if (choice === 'cancel' || choice == null) return
    if (choice === 'wizard') { setCrudWizardOpen(true); return }
    // Empty stub path — original behaviour.
    const base = (await modals.prompt({
      title: t('settings.tables.addTable'),
      message: t('settings.tables.namePrompt'),
    }))?.trim()
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
  const duplicateTable = async (connectorName: string, oldBase: string) => {
    const cur = (conns ?? {})[connectorName] ?? {}
    const queries = Array.isArray(cur.queries) ? (cur.queries as Record<string, unknown>[]) : []
    // Pre-check: nothing to duplicate from. Surface as an alert before prompting at all.
    if (duplicateTableQueries(queries, oldBase, `${oldBase}_copy`) === queries) {
      await modals.alert({
        title: t('settings.tables.duplicate'),
        message: t('settings.tables.duplicateNoSource', { name: oldBase }),
        variant: 'danger',
      })
      return
    }
    const suggested = `${oldBase}_copy`
    const newBase = (await modals.prompt({
      title: t('settings.tables.duplicate'),
      message: t('settings.tables.duplicatePrompt', { name: oldBase }),
      defaultValue: suggested,
      submitLabel: t('settings.tables.duplicate'),
      validate: (v) => {
        if (!v) return null   // empty → close as if cancelled
        if (v.toLowerCase() === oldBase.toLowerCase()) return t('settings.tables.duplicateSameName')
        if (tableExists(queries, v)) return t('settings.tables.duplicateExists', { name: v })
        return null
      },
    }))?.trim()
    if (!newBase) return
    const next = duplicateTableQueries(queries, oldBase, newBase)
    if (next === queries) return
    updateQueries(connectorName, next)
    setSelTable(newBase)
  }

  // Handler for the scaffold modal save: append the new query to the connector + the new
  // dictionary entry under the connector's per-connector scope (creating the scope if it
  // doesn't exist yet — same convention DictionaryBuilder uses). The operator then hits the
  // top-toolbar Save to commit both files; this just stages the edits in memory.
  const onScaffoldSave = (kind: ScaffoldKind, result: { query: { name: string; sql: string; label?: string }; dictEntry: Record<string, unknown>; dictId: string }) => {
    if (!sel || !conns || !dictionary) return
    // 1) append the query to the selected connector's queries list
    const conn = conns[sel] ?? {}
    const existing = Array.isArray(conn.queries) ? (conn.queries as Record<string, unknown>[]) : []
    updateQueries(sel, [...existing, { ...result.query }])
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

  // Names of queries referenced by a ``[sequences.*]`` / ``[lookups.*]`` entry pointing at
  // *this* connector — drives the Sequences / Lookups filter modes. Each def can be defined
  // at the shared scope (top-level ``[sequences.*]``) or scoped to a connector (under
  // ``[connectors.<name>.sequences.*]``); the def's own ``connector`` field overrides the
  // implicit scope, defaulting to the scope's own name. v1's per-app dictionaries map onto
  // these so two migrated apps can carry separate ``[sequences.7]`` entries safely.
  // *Hook MUST live above the early returns* — React's rules-of-hooks require a stable hook
  // call order across every render, and the early returns below would otherwise skip it on
  // the loading-fallback render and break the next render's hook count (the #310 error).
  const queryNamesByMode = useMemo<{ sequences: Set<string>; lookups: Set<string> }>(() => {
    const seqNames = new Set<string>()
    const lkpNames = new Set<string>()
    if (!dictionary || !sel) return { sequences: seqNames, lookups: lkpNames }
    const ingest = (defs: Record<string, Record<string, unknown>> | undefined, target: Set<string>, defaultConn: string) => {
      if (!defs) return
      for (const [, def] of Object.entries(defs)) {
        const conn = typeof def.connector === 'string' && def.connector ? def.connector : defaultConn
        const query = typeof def.query === 'string' ? def.query : ''
        if (conn === sel && query) target.add(query)
      }
    }
    // Shared scope — operator must spell out ``connector = "<name>"`` on the def to point at
    // *this* connector. (No implicit fallback — a shared def with no connector is "any" and
    // we'd over-include it; surfacing only the explicit ones keeps the list tidy.)
    ingest((dictionary.sequences ?? {}) as Record<string, Record<string, unknown>>, seqNames, '')
    ingest((dictionary.lookups ?? {}) as Record<string, Record<string, unknown>>, lkpNames, '')
    // Per-connector scope — the scope name is the default ``connector``.
    const perConn = (dictionary.connectors ?? {}) as Record<string, { sequences?: Record<string, Record<string, unknown>>; lookups?: Record<string, Record<string, unknown>> }>
    for (const [connName, secs] of Object.entries(perConn)) {
      ingest(secs.sequences, seqNames, connName)
      ingest(secs.lookups, lkpNames, connName)
    }
    return { sequences: seqNames, lookups: lkpNames }
  }, [dictionary, sel])

  if (error && !conns) return <Banner $tone="error">{error}</Banner>
  if (!conns || !schemas) return <Centered />
  // Alphabetical sort — operators expect a stable name-sorted list (the v1 ``apps_seq``
  // ordering is gone). ``localeCompare`` handles mixed case naturally.
  const names = Object.keys(conns).sort((a, b) => a.localeCompare(b))
  const needle = q.trim().toLowerCase()
  const shownNames = needle ? names.filter((n) => n.toLowerCase().includes(needle)) : names
  const selConn = sel && conns[sel] ? conns[sel] : null
  const selSchema = selConn ? schemaFor(selConn) : null
  const isSql = selConn?.type !== 'api'

  // --- table grouping (only when SQL + tables mode) --------------------------
  const queriesArr = (selConn && Array.isArray(selConn.queries) ? selConn.queries : []) as Record<string, unknown>[]
  const grouped = isSql ? groupQueriesByTable(queriesArr) : { tables: [], loose: [] }
  const tNeedle = tq.trim().toLowerCase()
  const shownTables = tNeedle ? grouped.tables.filter((g) => g.base.toLowerCase().includes(tNeedle)) : grouped.tables
  const queryDefSchema = (selSchema?.$defs?.QueryDef ?? null) as JsonSchema | null
  const allDefs = (selSchema?.$defs ?? {}) as Record<string, JsonSchema>

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
          <Button $variant="ghost" $size="sm" onClick={() => addConnector('sql')} disabled={busy}>
            <Plus size={13} /> {t('settings.connectors.addSql')}
          </Button>
          <Button $variant="ghost" $size="sm" onClick={() => addConnector('api')} disabled={busy}>
            <Plus size={13} /> {t('settings.connectors.addApi')}
          </Button>
          {sel && conns[sel] && (
            <Button $variant="danger" $size="sm" onClick={() => sel && removeConnector(sel)} disabled={busy} title={t('settings.connectors.delete')}>
              <Trash2 size={13} /> {t('settings.connectors.delete')}
            </Button>
          )}
          <ToolbarDivider />
          <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
          </Button>
          <Button $variant="ghost" $size="sm" onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <RefreshCw size={13} />} {t('settings.pools.reloadFromDisk')}
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
            {shownNames.map((n) => (
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
                      <ModeBtn type="button" $active={mode === 'tables'} onClick={() => setMode('tables')}>
                        {t('settings.tables.tablesView')}
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
                  {isSql && mode === 'tables' && !selTable && (
                    <Button $variant="ghost" $size="sm" onClick={() => sel && addTable(sel)} disabled={busy}>
                      <Plus size={13} /> {t('settings.tables.addTable')}
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
                  {/* Small connector-level "Settings…" — modal with type / pool / licensed /
                      max_rows fields. The 3-mode body below handles the per-query editing;
                      this button is just for the few connector-wide settings + the rare loose
                      queries. */}
                  {isSql && (
                    <Button $variant="ghost" $size="sm" onClick={() => setSettingsOpen(true)} disabled={busy} title={t('settings.connectors.openSettings', 'Connector settings (pool / licensed / max rows)')}>
                      <FileCog size={13} /> {t('settings.connectors.settings', 'Settings…')}
                    </Button>
                  )}
                  <Button $variant="ghost" $size="sm" onClick={() => sel && renameConnector(sel)} disabled={busy}>
                    <Edit3 size={13} /> {t('settings.rename.button')}
                  </Button>
                  <Button $variant="danger" $size="sm" onClick={() => sel && removeConnector(sel)} disabled={busy}>
                    <Trash2 size={13} /> {t('settings.connectors.delete')}
                  </Button>
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
                <ApiConnectorEditor name={sel!} value={selConn as ApiConnectorEditorValue} onChange={(v) => update(sel!, v as unknown as Record<string, unknown>)} />
              )}
              {isSql && mode === 'tables' && (
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
                      connectorName={sel!}
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
                    {/* "Add table" moved to the top toolbar (per-mode Add cluster) — keeps
                        every per-mode creator in one place at the top of the page. */}
                    {grouped.loose.length > 0 && (
                      <LooseNote>{t('settings.tables.looseHint', { count: grouped.loose.length })}</LooseNote>
                    )}
                  </Stack>
                )
              )}
              {/* Sequences / Lookups views — flat list of queries from ``queriesArr`` whose
                  name is referenced by any ``[sequences.*]`` / ``[lookups.*]`` entry in
                  dictionary.toml (scoped to this connector — see ``queryNamesByMode`` above).
                  Click a query → opens a single-query editor (the same per-query SchemaForm
                  over the QueryDef schema that the Tables view uses inside the CRUD slots).
                  The dictionary metadata (dd_id / value / label / params / …) stays editable
                  in DictionaryBuilder — this view edits the SQL + params for the query
                  itself, the dictionary def is a separate concern. */}
              {isSql && (mode === 'sequences' || mode === 'lookups') && (() => {
                const usedNames = mode === 'sequences' ? queryNamesByMode.sequences : queryNamesByMode.lookups
                const matches = queriesArr.filter((q) => typeof q.name === 'string' && usedNames.has(q.name as string))
                  .sort((a, b) => String(a.name).localeCompare(String(b.name)))
                const qNeedle = tq.trim().toLowerCase()
                const shown = qNeedle ? matches.filter((q) => String(q.name).toLowerCase().includes(qNeedle)) : matches
                const picked = selQuery ? queriesArr.find((q) => q.name === selQuery) : null
                if (picked && queryDefSchema) {
                  // Single-query editor — patches the picked query in place via
                  // updateQueries. ``onBack`` clears the selection to return to the list.
                  // Back button on the LEFT matches the Tables-mode ConnectorsTableEditor
                  // header convention (operator's eye is already there from the click).
                  return (
                    <Stack gap={10}>
                      <Row gap={8} style={{ alignItems: 'center' }}>
                        <Button $variant="ghost" $size="sm" onClick={() => setSelQuery(null)}>
                          ← {t('common.back')}
                        </Button>
                        <Mono style={{ fontSize: fontSize.sm }}>{String(picked.name)}</Mono>
                      </Row>
                      <SqlConnectorContext.Provider value={sel ?? undefined}>
                        <SchemaForm
                          schema={queryDefSchema}
                          defs={allDefs}
                          value={picked as Record<string, unknown>}
                          onChange={(v: Record<string, unknown>) => {
                            const next = queriesArr.map((q) => (q.name === picked.name ? v : q))
                            updateQueries(sel!, next)
                            // Track the (possibly renamed) query so the editor stays open on it.
                            const vn = v.name
                            if (typeof vn === 'string' && vn && vn !== picked.name) setSelQuery(vn)
                          }}
                        />
                      </SqlConnectorContext.Provider>
                    </Stack>
                  )
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
                        {mode === 'sequences'
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
            </Stack>
          ) : (
            <Empty>{names.length ? t('settings.connectors.pickOne') : t('settings.connectors.empty')}</Empty>
          )}
        </FormCol>
      </Split>
      {/* Settings modal — the "rare-case" escape hatch for SQL connectors: edits everything
          the Tables view doesn't show (label / pool / max_rows / loose non-CRUD queries) via
          the full SchemaNavigator. Click-outside / Escape / Close all dismiss; edits flow
          through the same ``update`` callback as the inline editor, so the dirty flag /
          Save / Reload at the top stay in sync. */}
      {/* Settings modal — small connector-level form. Picks only the rare-edit fields
          (``type``, ``pool``, ``licensed``, ``max_rows``) out of the full connector schema;
          ``queries`` lives in the Tables / Sequences / Lookups views, not here. Click-outside
          / Escape / Close all dismiss; edits flow through the same ``update`` callback so
          the dirty flag at the top toolbar stays in sync. */}
      {settingsOpen && selConn && selSchema && (() => {
        const fieldKeys = isSql
          ? ['type', 'pool', 'licensed', 'max_rows'] as const
          : ['type', 'licensed'] as const
        const settingsSchema = pickSchemaProperties(selSchema, fieldKeys as unknown as string[])
        return (
          <Overlay onClick={() => setSettingsOpen(false)}>
            <Modal style={{ width: 'min(560px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
              <ModalHeader>
                <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center', width: '100%' }}>
                  <span>
                    {t('settings.connectors.settings', 'Settings…')} ·{' '}
                    <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontWeight: 400 }}>
                      [connectors.{sel}]
                    </span>
                  </span>
                  <Button $variant="ghost" $size="sm" onClick={() => setSettingsOpen(false)}>
                    <X size={13} /> {t('common.close')}
                  </Button>
                </Row>
              </ModalHeader>
              <ModalBody>
                <SchemaForm
                  schema={settingsSchema}
                  defs={allDefs}
                  value={selConn as Record<string, unknown>}
                  onChange={(v: Record<string, unknown>) => {
                    // Patch — keep keys we didn't pick (queries, endpoints, base_url, …) untouched.
                    const patch: Record<string, unknown> = {}
                    for (const k of fieldKeys) patch[k] = v[k]
                    update(sel!, { ...selConn, ...patch })
                  }}
                />
              </ModalBody>
              <ModalFooter>
                <Button $variant="ghost" $size="sm" onClick={() => setSettingsOpen(false)}>
                  <X size={13} /> {t('common.close')}
                </Button>
              </ModalFooter>
            </Modal>
          </Overlay>
        )
      })()}
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
          existingQueryNames={new Set(queriesArr
            .map((q) => typeof q.name === 'string' ? q.name : '')
            .filter(Boolean))}
          onSave={(result) => onScaffoldSave(scaffoldKind, result)}
          onCancel={() => setScaffoldKind(null)}
        />
      )}
      {/* CRUD wizard — opened from "+ Add table → Generate from DB". Saves the four queries
          to the connector's ``queries`` list + auto-selects the new table in the list so the
          operator can keep tweaking inside ConnectorsTableEditor. The top-toolbar Save commits
          the result to disk. */}
      {crudWizardOpen && sel && (
        <CrudWizardModal
          connector={sel}
          existingQueryNames={new Set(queriesArr
            .map((q) => typeof q.name === 'string' ? q.name : '')
            .filter(Boolean))}
          onSave={(result) => {
            const cur = (conns ?? {})[sel] ?? {}
            const existing = Array.isArray(cur.queries) ? (cur.queries as Record<string, unknown>[]) : []
            updateQueries(sel, [...existing, ...result.queries])
            setCrudWizardOpen(false)
            // The base name follows the v1 ``<base>_<crud>`` convention; jump straight to the
            // new table in the Tables list so the operator can tweak the auto-generated SQL.
            const firstName = result.queries[0]?.name ?? ''
            const m = firstName.match(/^(.+)_(get|put|post|delete)$/i)
            if (m) setSelTable(m[1])
          }}
          onCancel={() => setCrudWizardOpen(false)}
        />
      )}
    </Shell>
    </FrameworkEnumsContext.Provider>
  )
}
