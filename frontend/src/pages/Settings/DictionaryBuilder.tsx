// Structured editor for `config/dictionary.toml` — the shared field dictionary (v1's ly_dictionary
// family + ly_enum/ly_lookup). Three record kinds via sub-tabs: **Entries** (a column's label/
// format/display rule), **Enums** (a code→label set referenced from a BOOLEAN/ENUM/LOOKUP rule),
// **Lookups** (a query-backed code→label table). Each kind exists at two scopes — `Shared` (the
// top-level `[entries.*]` etc.) and per-connector overlays (`[connectors.<name>.entries.*]`) — v1's
// per-app dictionaries map onto these so two migrated apps can't clash on a `dd_id`. Save validates
// the whole `DictionaryFile` then rewrites the file (via tomlkit), then reloads. No rename yet —
// delete + re-add. Renders the body only; Settings/index.tsx wraps the page.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { Save, Plus, Trash2, Search, Edit3, BookText, Undo2, Copy, GitBranch } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, SchemaNavigator, FrameworkEnumsContext, useModals, Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Field, SearchSelect, type FrameworkEnums, type JsonSchema, type SearchSelectOption } from '../../common'
import type { ConfigSchemas, ConnectorsDoc, DictionaryDoc, DictionaryKind, DictionarySection } from '../../types/config'
import { renameKey } from '../../services/keyRename'
import { validateId, suggestCloneId } from '../../services/idValidator'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { AddScopeModal } from './AddScopeModal'
import { FindUsagesModal, type FindUsagesTarget } from './FindUsagesModal'
import { EditQueryModal } from './EditQueryModal'
import { ScopeBar as ScopeRow } from './ScopeBar'
import { DictionaryScan } from './DictionaryScan'
import { colors, fontSize, fonts, radius } from '../../theme'
import { groupQueriesByTable } from './connectorTables'

type DictionaryData = DictionaryDoc['dictionary']

const SCOPE_SHARED = '' as const

// Layout: outer Shell flex-fills, top toolbar is fixed, the Split fills remaining height,
// only the inner panels scroll. Same pattern as PoolsBuilder.
const Shell = styled.div`
  display: flex; flex-direction: column; gap: 12px;
  flex: 1; min-height: 0; height: 100%;
`
const SubTabs = styled.div`display: flex; align-items: center; gap: 4px; border-bottom: 1px solid ${colors.border}; padding-bottom: 6px;`
const SubTab = styled.button<{ $active?: boolean }>`
  height: 30px; padding: 0 14px; border-radius: ${radius.sm}; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  &:hover { color: ${colors.text.primary}; background: ${({ $active }) => ($active ? colors.blue.bg : 'var(--hover-subtle)')}; }
`
const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0; align-items: stretch;`
// The left nav has its own scroll container so a long list (think 347 dictionary entries) doesn't
// drag the whole page along when you wheel through it. The search row and the "+ Add" button stay
// pinned outside the scroller, the items live in NavList. The flex chain (Shell → Split → NavCol →
// NavList) caps the list height to the page; no max-height hack needed.
const NavCol = styled.div`flex: 0 0 220px; display: flex; flex-direction: column; gap: 4px; min-width: 0; min-height: 0;`
const NavList = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding-right: 4px;`
const NavSearch = styled.div`
  display: flex; align-items: center; gap: 6px; height: 32px; padding: 0 10px; margin-bottom: 2px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.muted};
  flex-shrink: 0;
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; &::placeholder { color: ${colors.text.muted}; } }
`
const NavItem = styled.button<{ $active?: boolean }>`
  display: flex; flex-direction: column; align-items: stretch; gap: 1px; padding: 6px 10px;
  border-radius: ${radius.md}; text-align: left;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  cursor: pointer;
  & .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: ${fontSize.sm}; font-family: ${fonts.mono}; }
  & .sub  { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.sans}; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const FormCol = styled(Card)`flex: 1; min-width: 0; min-height: 0; overflow-y: auto;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`

const KIND_TO_DEF: Record<DictionaryKind, string> = {
  entries: 'DictionaryEntry',
  enums: 'EnumDef',
  lookups: 'LookupDef',
  sequences: 'SequenceDef',
  framework_enums: 'EnumDef',  // operator overrides reuse the EnumDef shape (label + values)
}

const KIND_ORDER: DictionaryKind[] = ['entries', 'enums', 'lookups', 'sequences', 'framework_enums']
/** Framework enums are top-level only — there's no per-connector overlay. The scope chip strip is
 *  hidden when this kind is active, and the section getters refuse the connector path. */
const SHARED_ONLY: ReadonlySet<DictionaryKind> = new Set(['framework_enums'])

/** A blank record for the chosen kind. Lookup needs `query`/`value`/`label` to validate, so we
 *  leave them empty strings (the user fills them in; SchemaForm flags required fields with `*`). */
function newRecord(kind: DictionaryKind): Record<string, unknown> {
  if (kind === 'lookups') return { query: '', value: '', label: '' }
  if (kind === 'sequences') return { query: '' }  // SequenceDef requires `query`
  if (kind === 'framework_enums') return { values: [] }
  return {}
}

/** The current `entries` / `enums` / `lookups` / `framework_enums` map for `scope` (`''` = shared,
 *  else a connector). Framework-enum overrides live at the top level only. */
function getSection(dict: DictionaryData, scope: string, kind: DictionaryKind): Record<string, Record<string, unknown>> {
  if (SHARED_ONLY.has(kind) || !scope) {
    return (dict[kind] ?? {}) as Record<string, Record<string, unknown>>
  }
  const sec = (dict.connectors ?? {})[scope]
  return ((sec?.[kind as Exclude<DictionaryKind, 'framework_enums'>]) ?? {}) as Record<string, Record<string, unknown>>
}

/** Write back a section map; drops empty sections + empty connector scopes so the file stays terse. */
function setSection(
  dict: DictionaryData,
  scope: string,
  kind: DictionaryKind,
  next: Record<string, Record<string, unknown>>,
): DictionaryData {
  const out: DictionaryData = { ...dict }
  if (SHARED_ONLY.has(kind) || !scope) {
    if (Object.keys(next).length === 0) delete out[kind]; else out[kind] = next
    return out
  }
  const k = kind as Exclude<DictionaryKind, 'framework_enums'>
  const connectors: Record<string, DictionarySection> = { ...(out.connectors ?? {}) }
  const cur: DictionarySection = { ...(connectors[scope] ?? {}) }
  if (Object.keys(next).length === 0) delete cur[k]; else cur[k] = next
  if (!cur.entries && !cur.enums && !cur.lookups && !cur.sequences) delete connectors[scope]
  else connectors[scope] = cur
  if (Object.keys(connectors).length === 0) delete out.connectors
  else out.connectors = connectors
  return out
}

export default function DictionaryBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const { currentApp, refresh: refreshWorkspace } = useWorkspace()
  const [schemas, setSchemas] = useState<ConfigSchemas | null>(null)
  const [dict, setDict] = useState<DictionaryData | null>(null)
  // Read-only — the *Lookups* form's query / value / label dropdowns read from here. We need to know
  // each connector's read queries (LOOKUP_QUERIES) and dictionary fields (LOOKUP_DD_FIELDS).
  const [connectors, setConnectors] = useState<Record<string, Record<string, unknown>> | null>(null)
  const [original, setOriginal] = useState('')
  const [kind, setKind] = useState<DictionaryKind>('entries')
  const [scope, setScope] = useState<string>(SCOPE_SHARED)
  const [sel, setSel] = useState<string | null>(null)
  const [q, setQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [scanOpen, setScanOpen] = useState(false)
  const [addScopeOpen, setAddScopeOpen] = useState(false)
  const [frameworkAddOpen, setFrameworkAddOpen] = useState(false)
  const [usagesTarget, setUsagesTarget] = useState<FindUsagesTarget | null>(null)
  // Edit-query modal target — opened from the in-line edit button next to a
  // LookupDef.query / SequenceDef.query dropdown (SchemaForm renders the button when
  // ``onEditQuery`` is wired). null = closed.
  const [editQueryTarget, setEditQueryTarget] = useState<{ connector: string; queryName: string } | null>(null)

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<DictionaryDoc>('/admin/config/dictionary/parsed'),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed'),
    ])
      .then(([s, d, c]) => {
        setSchemas(s); setDict(d.dictionary); setOriginal(JSON.stringify(d.dictionary))
        setConnectors(c.connectors)
        // On first open, default the scope to the workspace's selected app when it has its own
        // dictionary overlay (else stay on the shared scope). Only when scope is untouched.
        if (currentApp && (d.dictionary.connectors ?? {})[currentApp]) {
          setScope((cur) => (cur === SCOPE_SHARED ? currentApp : cur))
        }
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])
  // when the user changes section or scope, drop the (now-stale) selection + search
  useEffect(() => { setSel(null); setQ('') }, [kind, scope])

  const dirty = useMemo(() => dict != null && JSON.stringify(dict) !== original, [dict, original])

  // Augment the bundled framework enums with the *dynamic* sources this builder owns — so a
  // dictionary entry's `rules_values` field (whose `x_enum_ref_when` swaps between ENUM_IDS and
  // LOOKUP_IDS based on `rules`) renders as a proper dropdown of the ids that *actually exist* in
  // the current dict. Both shared + per-connector overlay ids are included when a connector scope
  // is active (the per-connector one wins on collision, the same way the runtime resolves them).
  // NOTE: this useMemo MUST stay above the early returns below — moving it after them would change
  // the hook count between renders (load → early return / loaded → full render) and trip React's
  // rules-of-hooks check (#310).
  const augmentedEnums: FrameworkEnums = useMemo(() => {
    const base: FrameworkEnums = { ...(schemas?.framework_enums ?? {}) }
    // `priority` is the chain of fields to try for the dropdown's *display label*. The two record
    // shapes have different "display name" fields — for an EnumDef it's `label` ("Status"); for a
    // LookupDef it's `description` ("Get UDC Description"), since `label` there is the result
    // column whose value to *display* (e.g. "DL01") — a column name, not a human name.
    const mkValues = (
      priority: ReadonlyArray<string>,
      ...maps: (Record<string, Record<string, unknown>> | undefined)[]
    ): { value: string; label: string }[] => {
      const out = new Map<string, string>()
      for (const m of maps) {
        if (!m) continue
        for (const [id, rec] of Object.entries(m)) {
          const r = rec as Record<string, unknown>
          let lbl = id
          for (const key of priority) {
            const v = r?.[key]
            if (typeof v === 'string' && v.trim()) { lbl = v; break }
          }
          out.set(id, lbl)
        }
      }
      return [...out.entries()].map(([value, label]) => ({ value, label }))
    }
    const overlay = scope && dict ? (dict.connectors ?? {})[scope] : undefined
    base.ENUM_IDS = { label: 'Enums (current scope)', values: mkValues(['label'], dict?.enums, overlay?.enums) }
    base.LOOKUP_IDS = { label: 'Lookups (current scope)', values: mkValues(['description'], dict?.lookups, overlay?.lookups) }
    // SEQUENCE_IDS — the named sequences in scope (shared + per-connector overlay). Drives the
    // DictionaryEntry.rules_values dropdown when `rules = "SEQUENCE"` / `"NN"` (the SQL connector
    // resolves the id to the sequence's query at INSERT time via DictionaryFile.find_sequence).
    base.SEQUENCE_IDS = { label: 'Sequences (current scope)', values: mkValues(['description'], dict?.sequences, overlay?.sequences) }

    // CONNECTOR_NAMES — every connector in connectors.toml. Drives LookupDef.connector (which in
    // turn picks which connector's queries / dd entries fill the next two dropdowns).
    const connNames = connectors ? Object.keys(connectors).sort() : []
    base.CONNECTOR_NAMES = { label: 'Connectors', values: connNames.map((n) => ({ value: n, label: n })) }

    // LOOKUP_QUERIES — the read queries (tables) of the *effective* connector for the currently
    // selected lookup record. Effective = the lookup's `connector` field (when explicitly set) →
    // else the current scope (if it's a connector overlay) → else nothing (a shared lookup with no
    // explicit connector resolves at runtime to the "asking" connector, which the builder can't
    // know about ahead of time). Same table-grouping as MenusBuilder: mono=base, value=base_get.
    // LOOKUP_DD_FIELDS — the dictionary entries available to that effective connector (shared +
    // per-connector overlay). Drives `value` / `label` since columns are dd-named in practice.
    let effectiveConn = ''
    if (kind === 'lookups' && sel) {
      const rec = (overlay?.lookups?.[sel] ?? dict?.lookups?.[sel]) as Record<string, unknown> | undefined
      const explicit = typeof rec?.connector === 'string' ? rec.connector : ''
      effectiveConn = explicit || (scope || '')
    }
    const conn = effectiveConn && connectors ? connectors[effectiveConn] : undefined
    const lookupQueries: { value: string; label: string; mono?: string }[] = []
    if (conn?.type === 'sql') {
      const qs = Array.isArray(conn.queries) ? (conn.queries as Record<string, unknown>[]) : []
      const grouped = groupQueriesByTable(qs)
      for (const g of grouped.tables) {
        if (!g.slots.get) continue
        const qg = g.slots.get.query
        const desc = typeof qg?.description === 'string' ? qg.description : (typeof qg?.label === 'string' ? qg.label : '')
        lookupQueries.push({ value: g.slots.get.name, label: desc || g.base, mono: g.base })
      }
      for (const ls of grouped.loose) {
        if (!ls.name) continue
        const desc = typeof ls.query?.description === 'string' ? ls.query.description : (typeof ls.query?.label === 'string' ? ls.query.label : '')
        lookupQueries.push({ value: ls.name, label: desc || ls.name, mono: ls.name })
      }
    }
    base.LOOKUP_QUERIES = {
      label: effectiveConn ? `Read queries — ${effectiveConn}` : 'Read queries (pick a connector first)',
      values: lookupQueries,
    }
    // LOOKUP_DD_FIELDS = union of dd entries from three places, ordered last-wins:
    //   1. Shared bucket
    //   2. The lookup's `connector` (where the query lives) — its columns are typically dd-named
    //   3. The current SCOPE (where the lookup record itself lives) — same domain, often the same set
    // Using the union covers the post-re-migration case where lookups live under one connector scope
    // (e.g. jdedwards) but target queries on another (e.g. nomajde) — pulling from only one would
    // leave the dropdown empty.
    const ddOut = new Map<string, string>()
    const ingestEntries = (m: Record<string, unknown> | undefined) => {
      for (const [id, rec] of Object.entries(m ?? {})) {
        const lbl = typeof (rec as Record<string, unknown>)?.label === 'string'
          ? ((rec as Record<string, unknown>).label as string)
          : id
        ddOut.set(id, lbl)
      }
    }
    ingestEntries(dict?.entries)
    if (effectiveConn) ingestEntries(((dict?.connectors ?? {})[effectiveConn]?.entries) as Record<string, unknown> | undefined)
    if (scope && scope !== effectiveConn) ingestEntries(((dict?.connectors ?? {})[scope]?.entries) as Record<string, unknown> | undefined)
    const ddLabelBits = ['shared', effectiveConn, scope].filter((s, i, arr) => s && arr.indexOf(s) === i)
    base.LOOKUP_DD_FIELDS = {
      label: ddLabelBits.length > 0
        ? `Dictionary entries — ${ddLabelBits.join(' / ')}`
        : 'Dictionary entries',
      values: [...ddOut.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([value, label]) => ({ value, label })),
    }

    // CURRENT_LOOKUP_PARAMS — used as `x_key_enum_ref` on DictionaryEntry.lookup_params. When the
    // selected entry has `rules = "LOOKUP"`, this lists the param names declared by the chosen
    // lookup (its `params` field from v1's ly_lkp_params). The translations-style key dropdown in
    // StringMapEditor reads this and suggests the right names; param values stay free-text.
    let lkpParamNames: string[] = []
    if (kind === 'entries' && sel) {
      const ent = (overlay?.entries?.[sel] ?? dict?.entries?.[sel]) as Record<string, unknown> | undefined
      const ruleKind = typeof ent?.rules === 'string' ? ent.rules.toUpperCase() : ''
      const ruleArg = typeof ent?.rules_values === 'string' ? ent.rules_values : ''
      if (ruleKind === 'LOOKUP' && ruleArg) {
        // Lookups follow the same resolution path as entries — scope's overlay first, then shared
        const lkpRec = (overlay?.lookups?.[ruleArg] ?? dict?.lookups?.[ruleArg]) as Record<string, unknown> | undefined
        const params = lkpRec?.params
        if (Array.isArray(params)) lkpParamNames = params.filter((p): p is string => typeof p === 'string' && !!p)
      }
    }
    base.CURRENT_LOOKUP_PARAMS = {
      label: lkpParamNames.length > 0
        ? `Lookup params — ${lkpParamNames.join(', ')}`
        : 'Lookup params (pick a LOOKUP rule first)',
      values: lkpParamNames.map((p) => ({ value: p, label: p })),
    }
    return base
  }, [schemas, dict, scope, kind, sel, connectors])

  // Other dictionary kinds in the current scope — drives the cross-kind warning in the
  // validator (e.g. "id '1' is also used as a lookup in this scope — runtime works but
  // it's confusing"). Lives ABOVE the early-return guards (React rules-of-hooks: hooks
  // must run in the same order every render — moving this past the `if (!dict)` short-
  // circuit triggers error #310 once the dict loads). Handles the not-yet-loaded case
  // internally by returning an empty record.
  const crossKindIds = useMemo<Record<string, string[]>>(() => {
    if (!dict) return {}
    const out: Record<string, string[]> = {}
    for (const k of ['entries', 'enums', 'lookups', 'sequences'] as const) {
      if (k === kind) continue
      out[k] = Object.keys(getSection(dict, scope, k))
    }
    return out
  }, [dict, kind, scope])

  if (error && !dict) return <Banner $tone="error">{error}</Banner>
  if (!dict || !schemas) return <Centered />

  const dictSchema = schemas.dictionary
  const defs = (dictSchema?.$defs ?? {}) as Record<string, JsonSchema>
  const recordSchema: JsonSchema | null = defs[KIND_TO_DEF[kind]] ?? null

  // scopes = the shared bucket + any connector that already has a section + the just-added scope
  // (so it has a chip to click before its first record materialises it in dict.connectors).
  const scopeKeys = new Set([SCOPE_SHARED, ...Object.keys(dict.connectors ?? {})])
  if (scope) scopeKeys.add(scope)
  const scopes: string[] = [SCOPE_SHARED, ...[...scopeKeys].filter((s) => s !== SCOPE_SHARED).sort()]
  const section = getSection(dict, scope, kind)
  // Alphabetical sort — v1's per-row `*_seq` columns aren't carried over, so we surface
  // dictionary entries / enums / lookups in a stable name-sorted order. ``localeCompare``
  // handles the mix of underscore-snake and PascalCase ids you see in NOMASX1 / NOMAJDE.
  const keys = Object.keys(section).sort((a, b) => a.localeCompare(b))
  const needle = q.trim().toLowerCase()
  const shown = needle ? keys.filter((k) => k.toLowerCase().includes(needle)) : keys

  const setRecord = (key: string, v: Record<string, unknown>) => {
    setDict(setSection(dict, scope, kind, { ...section, [key]: v }))
    setStatus(null)
  }

  const addRecord = async () => {
    // Framework enums are a closed set defined in liberty/framework_enums.py — operators can't
    // invent a new id, they can only override an existing one. So skip the free-text prompt and
    // open a picker showing the bundled ids that aren't already overridden. The chosen id seeds
    // the override with the framework's bundled values (the runtime REPLACES the bundled set
    // when an override exists — see DictionaryFile.framework_enums docstring — so starting from
    // an empty values list would wipe every built-in choice).
    if (kind === 'framework_enums') {
      setFrameworkAddOpen(true)
      return
    }
    const existing = Object.keys(section)
    const raw = (await modals.prompt({
      title: t(`settings.dictionary.${kind}.add`),
      message: t(`settings.dictionary.${kind}.namePrompt`),
      placeholder: kind === 'entries' || kind === 'enums' ? 'UPPER_SNAKE_CASE' : 'snake_case',
      validate: (v) => {
        // entries are uppercased on save — validate the upper form so the duplicate
        // check matches what'll actually land.
        const proposed = kind === 'entries' ? v.toUpperCase() : v
        return validateId({ kind, proposed, existing, crossKindIds, mode: 'add' })
      },
    }))?.trim()
    if (!raw) return
    const key = kind === 'entries' ? raw.toUpperCase() : raw
    // Duplicate guard already in the validator, but defend against a race / stale state.
    if (key in section) { setSel(key); return }
    setDict(setSection(dict, scope, kind, { ...section, [key]: newRecord(kind) }))
    setSel(key); setStatus(null)
  }
  const addFrameworkOverride = (key: string) => {
    if (!dict) return
    if (key in section) { setSel(key); return }
    const bundled = (schemas?.framework_enums ?? {})[key]
    const seed: Record<string, unknown> = {
      label: typeof bundled?.label === 'string' ? bundled.label : key,
      // Clone so subsequent edits don't mutate the bundled defaults the rest of the UI reads.
      values: Array.isArray(bundled?.values) ? JSON.parse(JSON.stringify(bundled.values)) : [],
    }
    setDict(setSection(dict, scope, kind, { ...section, [key]: seed }))
    setSel(key); setStatus(null)
  }
  const removeRecord = async (key: string) => {
    const ok = await modals.confirm({
      title: t(`settings.dictionary.${kind}.delete`),
      message: t(`settings.dictionary.${kind}.confirmDelete`, { name: key }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    // Persist the delete immediately (write + reload), not just locally. A confirmed destructive
    // action shouldn't sit as a pending edit that a reload silently discards (or a separate Save
    // step might miss) — that read as "delete doesn't work". Commits the current doc minus the
    // entry; a failure surfaces inline instead of the entry quietly reappearing on reload.
    const next = { ...section }; delete next[key]
    const nextDict = setSection(dict, scope, kind, next)
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/dictionary/parsed', { dictionary: nextDict })
      await api.post<{ connectors: string[] }>('/admin/reload')
      setSel((s) => (s === key ? null : s))
      setStatus(t('settings.dictionary.saved'))
      load()  // re-fetch so dict + original baseline match disk (entry gone)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }
  // Rename a record's dict key. Routes through the backend endpoint
  // ``POST /admin/config/rename`` for the three kinds the backend supports — ``entries``
  // (→ ``dictionary_entry``: also walks screens.toml for ``ColumnHint.dd`` /
  // ``PromptField.dd`` references), ``lookups`` (→ ``lookup``: walks DictionaryEntry rules_values
  // in the same scope), and ``sequences`` (→ ``sequence``: same). For ``enums`` and
  // ``framework_enums`` (no backend endpoint), keeps the local in-memory rename + intra-scope
  // cascade — same behaviour as before. Refuses to fire with unsaved local edits so the disk
  // rewrite + reload doesn't clobber pending changes.
  const ENDPOINT_KINDS: Record<string, string> = {
    entries: 'dictionary_entry',
    lookups: 'lookup',
    sequences: 'sequence',
  }
  const renameRecord = async (oldKey: string) => {
    if (!dict) return
    // Exclude the current name from "existing" so renaming "X" → "X" is a no-op (caller
    // bails on next === oldKey just below), not a duplicate-error.
    const existing = Object.keys(section).filter((k) => k !== oldKey)
    const next = (await modals.prompt({
      title: t('settings.rename.button'),
      message: t(`settings.dictionary.${kind}.renamePrompt`, { name: oldKey }),
      defaultValue: oldKey,
      submitLabel: t('settings.rename.button'),
      validate: (v) => {
        const proposed = kind === 'entries' ? v.toUpperCase() : v
        return validateId({ kind, proposed, existing, crossKindIds, mode: 'rename', currentName: oldKey })
      },
    }))?.trim()
    if (!next || next === oldKey) return

    const endpointKind = ENDPOINT_KINDS[kind]
    if (endpointKind) {
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
        if (dirty) return
      }
      setBusy(true)
      try {
        const body: Record<string, unknown> = { kind: endpointKind, old_name: oldKey, new_name: next }
        // Backend's "shared" scope = no scope arg; the builder uses '' (SCOPE_SHARED) for that.
        if (scope !== SCOPE_SHARED) body.scope = scope
        const result = await api.post<{ files: Record<string, number>; warnings: string[]; total_refs: number }>(
          '/admin/config/rename', body,
        )
        await api.post('/admin/reload')
        refreshWorkspace()
        setSel(next)
        load()
        const filesTouched = Object.values(result.files).filter((n) => n > 0).length
        const tail = result.warnings.length ? ` · ${result.warnings.join(' · ')}` : ''
        setStatus(t('settings.dictionary.renamedAcross', {
          from: oldKey, to: next, refs: result.total_refs, files: filesTouched,
        }) + tail)
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : String(e)
        setError(t('settings.dictionary.renameFailed', { name: oldKey, error: msg }))
      } finally {
        setBusy(false)
      }
      return
    }

    // Enums / framework_enums — no backend endpoint, keep the local in-memory rename
    // with the same intra-scope cascade for enum refs.
    let updated = setSection(dict, scope, kind, renameKey(section, oldKey, next))
    let cascaded = 0
    if (kind === 'enums') {
      const entries = getSection(updated, scope, 'entries')
      const nextEntries: Record<string, Record<string, unknown>> = {}
      for (const [eid, rec] of Object.entries(entries)) {
        const r = typeof (rec as Record<string, unknown>)?.rules === 'string'
          ? ((rec as Record<string, unknown>).rules as string).toUpperCase()
          : ''
        const rv = (rec as Record<string, unknown>)?.rules_values
        if (r === 'ENUM' && rv === oldKey) {
          nextEntries[eid] = { ...(rec as Record<string, unknown>), rules_values: next }
          cascaded++
        } else {
          nextEntries[eid] = rec as Record<string, unknown>
        }
      }
      if (cascaded > 0) updated = setSection(updated, scope, 'entries', nextEntries)
    }
    setDict(updated)
    setSel(next)
    setStatus(
      cascaded > 0
        ? t('settings.dictionary.renamedCascaded', { from: oldKey, to: next, count: cascaded })
        : t('settings.dictionary.renamed', { from: oldKey, to: next }),
    )
  }
  // "Add scope" = start a per-connector dictionary overlay. Offer the connectors that don't already
  // have an overlay (a scope materialises in `dict.connectors` as soon as a record is added; an
  // empty one naturally disappears on the next round-trip). Picking one just switches to it.
  const addScopeCandidates = Object.keys(connectors ?? {}).filter((c) => !scopes.includes(c)).sort()
  const onPickScope = (name: string) => { setScope(name); setStatus(null) }
  // Revert local edits to the last loaded state — same pattern as Pools / Connectors / Menus.
  const discard = () => {
    if (!original) return
    setDict(JSON.parse(original) as DictionaryData)
    setStatus(null); setError(null)
  }
  // Clone the selected record under a new key — same prompt-and-validate flow as Add.
  const cloneRecord = async (oldKey: string) => {
    if (!sel) return
    const src = section[oldKey]
    if (!src) return
    const existing = Object.keys(section)
    const raw = (await modals.prompt({
      title: t('common.clone', 'Clone'),
      message: t(`settings.dictionary.${kind}.namePrompt`),
      // suggestCloneId picks a non-colliding suffix (_copy, _copy2, _copy3, …) so the
      // operator doesn't have to manually find a free name.
      defaultValue: suggestCloneId(oldKey, existing),
      submitLabel: t('common.clone', 'Clone'),
      validate: (v) => {
        const proposed = kind === 'entries' ? v.toUpperCase() : v
        if (proposed === oldKey) return { error: t('settings.tables.duplicateSameName', 'Pick a different name.') }
        return validateId({ kind, proposed, existing, crossKindIds, mode: 'clone' })
      },
    }))?.trim()
    if (!raw) return
    const key = kind === 'entries' ? raw.toUpperCase() : raw
    // Deep-copy so the editor doesn't share references between the source and the clone.
    setDict(setSection(dict, scope, kind, { ...section, [key]: JSON.parse(JSON.stringify(src)) }))
    setSel(key); setStatus(null)
  }

  async function save() {
    if (!dict) return
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/dictionary/parsed', { dictionary: dict })
      await api.post<{ connectors: string[] }>('/admin/reload')
      setStatus(t('settings.dictionary.saved'))
      load()  // re-fetch — the backend strips defaults and may have re-rendered subsections
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const scopeLabel = (s: string) => (s ? s : t('settings.dictionary.scope.shared'))

  return (
    <FrameworkEnumsContext.Provider value={augmentedEnums}>
    <Shell>
      {/* One consolidated top toolbar — config path + status + language input on the left, Save +
          Reload on the right. Replaces the old "header at top + bottom Save Row" split — the
          operator never has to scroll past a long entries list to reach Save / Reload. */}
      {/* Single top row — same shape as the other editors: status on the left, scope chips +
          Add scope in the middle, Scan / Discard / Save on the far right. */}
      <ScopeRow
        scopes={SHARED_ONLY.has(kind)
          ? [{ value: SCOPE_SHARED, label: scopeLabel(SCOPE_SHARED) }]
          : scopes.map((s) => ({ value: s, label: scopeLabel(s) }))}
        value={scope}
        onChange={setScope}
        onAddScope={() => setAddScopeOpen(true)}
        addScopeDisabled={SHARED_ONLY.has(kind)}
        addScopeTitle={SHARED_ONLY.has(kind) ? t('settings.dictionary.framework_enums.scopeNote') : t('settings.dictionary.scope.addPrompt')}
        leading={(dirty || status || error) ? (
          <>
            {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
            {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
            {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
          </>
        ) : undefined}
        right={
          <>
            <Button $variant="ghost" $size="sm" onClick={() => setScanOpen(true)} disabled={busy}>
              <BookText size={13} /> {t('settings.dictscan.scanTable', 'Scan a table')}
            </Button>
            <Button $variant="ghost" $size="sm" onClick={discard} disabled={busy || !dirty}>
              <Undo2 size={13} /> {t('common.discard', 'Discard')}
            </Button>
            <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
              {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
            </Button>
          </>
        }
      />
      <SubTabs>
        {KIND_ORDER.map((k) => (
          <SubTab key={k} $active={kind === k} type="button" onClick={() => { setKind(k); if (SHARED_ONLY.has(k)) setScope(SCOPE_SHARED) }}>{t(`settings.dictionary.${k}.tab`)}</SubTab>
        ))}
        <div style={{ flex: 1 }} />
        <Button $variant="ghost" $size="sm" onClick={addRecord} title={t(`settings.dictionary.${kind}.add`)}>
          <Plus size={13} /> {t(`settings.dictionary.${kind}.add`)}
        </Button>
      </SubTabs>
      <Split>
        <NavCol>
          {/* Always render so the sub-tab switch doesn't shift the NavList up/down — every
              dictionary kind (entries / enums / lookups / sequences / framework_enums) shows
              the same filter row. Empty-list state still gets it; the placeholder reads "0". */}
          <NavSearch>
            <Search size={13} />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`filter ${keys.length}…`} />
          </NavSearch>
          <NavList>
            {shown.map((k) => {
              // Help the user find the right record at a glance — show its label/description below
              // the key (entries/enums → `label`; lookups → `description`). Falls back to nothing when
              // the record has no human-readable side-name yet (in that case only the key shows).
              const rec = section[k] as Record<string, unknown> | undefined
              // sub-label per kind: entries/enums → `label`; lookups/sequences → `description`.
              const sub = (kind === 'lookups' || kind === 'sequences') ? rec?.description : rec?.label
              const subStr = typeof sub === 'string' && sub.trim() ? sub : null
              return (
                <NavItem key={k} $active={k === sel} onClick={() => { setSel(k); setStatus(null) }}>
                  <span className="name">{k}</span>
                  {subStr && <span className="sub">{subStr}</span>}
                </NavItem>
              )
            })}
            {shown.length === 0 && (
              <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '4px 4px' }}>
                {keys.length ? t('common.noMatches') : t(`settings.dictionary.${kind}.empty`)}
              </div>
            )}
          </NavList>
          {/* "Add <kind>" lives in the scope bar above — keeps every per-scope action in one
              place at the top of the page. The list footer here would otherwise compete with it
              for the operator's eye, and gets buried under a long list. */}
        </NavCol>
        <FormCol>
          {sel && section[sel] && recordSchema ? (
            <Stack gap={12}>
              <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>
                  {scope ? `[connectors.${scope}.${kind}.${sel}]` : `[${kind}.${sel}]`}
                </strong>
                <Row gap={6}>
                  {/* Find usages: applies to every dictionary kind except framework_enums (those
                      are bundled overrides, not externally referenced from screens / connectors). */}
                  {kind !== 'framework_enums' && (
                    <Button $variant="ghost" $size="sm" onClick={() => setUsagesTarget({
                      kind: kind === 'entries' ? 'dictionary_entry'
                          : kind === 'lookups' ? 'lookup'
                          : kind === 'sequences' ? 'sequence'
                          : 'enum',
                      name: sel,
                      scope: scope || 'shared',
                      label: `${kind === 'entries' ? 'dictionary entry' : kind.slice(0, -1)} ${sel}`,
                    })} disabled={busy}>
                      <GitBranch size={13} /> {t('findUsages.button', 'Find usages')}
                    </Button>
                  )}
                  <Button $variant="ghost" $size="sm" onClick={() => renameRecord(sel)} disabled={busy}>
                    <Edit3 size={13} /> {t('settings.rename.button')}
                  </Button>
                  <Button $variant="ghost" $size="sm" onClick={() => cloneRecord(sel)} disabled={busy}>
                    <Copy size={13} /> {t('common.clone', 'Clone')}
                  </Button>
                  <Button $variant="ghost" $size="sm" onClick={() => removeRecord(sel)} disabled={busy} style={{ color: colors.red.main }}>
                    <Trash2 size={13} /> {t('common.delete', 'Delete')}
                  </Button>
                </Row>
              </Row>
              <SchemaNavigator
                root={{
                  label: sel,
                  schema: { ...recordSchema, $defs: defs },
                  value: section[sel],
                  onChange: (v) => setRecord(sel, v),
                }}
                // LookupDef + SequenceDef have a ``connector`` field — SchemaForm
                // resolves it from siblings; when blank, fall back to the dictionary
                // scope (per-connector overlay = scope IS the connector). For shared-
                // scope records the operator must set the connector explicitly.
                onEditQuery={(conn, queryName) => {
                  const resolved = conn || (scope || null)
                  if (!resolved) return
                  setEditQueryTarget({ connector: resolved, queryName })
                }}
              />
            </Stack>
          ) : (
            <Empty>{keys.length ? t(`settings.dictionary.${kind}.pickOne`) : t(`settings.dictionary.${kind}.empty`)}</Empty>
          )}
        </FormCol>
      </Split>
      {scanOpen && <DictionaryScan onClose={() => setScanOpen(false)} onSaved={load} />}
      {addScopeOpen && (
        <AddScopeModal
          candidates={addScopeCandidates}
          title={t('settings.dictionary.scope.add', 'Add a connector scope')}
          emptyHint={t('settings.dictionary.scope.addEmpty', 'Every connector already has a dictionary overlay.')}
          onPick={onPickScope}
          onClose={() => setAddScopeOpen(false)}
        />
      )}
      {frameworkAddOpen && (
        <FrameworkEnumPickerModal
          schema={schemas?.framework_enums}
          existing={Object.keys(section)}
          onPick={addFrameworkOverride}
          onClose={() => setFrameworkAddOpen(false)}
        />
      )}
      {usagesTarget && <FindUsagesModal target={usagesTarget} onClose={() => setUsagesTarget(null)} />}
      {editQueryTarget && (
        <EditQueryModal
          connector={editQueryTarget.connector}
          queryName={editQueryTarget.queryName}
          onClose={() => setEditQueryTarget(null)}
          // Reload the dictionary doc after a save so any query rename propagates into
          // the LookupDef.query / SequenceDef.query references showing in the picker.
          onSaved={load}
        />
      )}
    </Shell>
    </FrameworkEnumsContext.Provider>
  )
}

// Picker for which bundled framework enum to override. The set is closed (defined in
// liberty/framework_enums.py); only ids the operator hasn't overridden yet are offered. Each
// option shows the human label ("Dictionary Type") with the underlying id ("DICTIONARY_TYPE")
// as a secondary mono column — so the operator knows both what they're customising and the
// stable id the override is keyed by on disk.
function FrameworkEnumPickerModal({
  schema, existing, onPick, onClose,
}: {
  schema: FrameworkEnums | undefined
  existing: string[]
  onPick: (key: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const candidates = Object.entries(schema ?? {})
    .filter(([k]) => !existing.includes(k))
    .sort(([, a], [, b]) => (a?.label ?? '').localeCompare(b?.label ?? ''))
  const opts: SearchSelectOption[] = candidates.map(([k, def]) => ({
    value: k,
    label: def?.label ?? k,
    mono: k,
  }))
  const none = opts.length === 0
  return (
    <Overlay onClick={onClose}>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(480px, 94vw)' }}>
        <ModalHeader>{t('settings.dictionary.framework_enums.add', 'Customize framework enum')}</ModalHeader>
        <ModalBody>
          {none ? (
            <Banner $tone="info">
              {t('settings.dictionary.framework_enums.allOverridden', 'Every bundled framework enum already has an override here.')}
            </Banner>
          ) : (
            <Field label={t('settings.dictionary.framework_enums.pick', 'Which framework enum to customize')}>
              <SearchSelect value={value} onChange={setValue} options={opts}
                placeholder={t('common.pick', 'Pick…')} />
            </Field>
          )}
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button $size="sm" $variant="primary" disabled={none || !value}
            onClick={() => { if (value) { onPick(value); onClose() } }}>
            {t('common.ok', 'OK')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
