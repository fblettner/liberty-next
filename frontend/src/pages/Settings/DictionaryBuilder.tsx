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
import { Save, RefreshCw, Plus, Trash2, Search, Globe, Layers, Edit3 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Card, Row, Stack, SpinnerRing, Mono, SchemaNavigator, Input, FrameworkEnumsContext, useModals, type FrameworkEnums, type JsonSchema } from '../../common'
import type { ConfigSchemas, ConnectorsDoc, DictionaryDoc, DictionaryKind, DictionarySection } from '../../types/config'
import { renameKey, validateRename } from '../../services/keyRename'
import { useWorkspace } from '../../workspace/WorkspaceContext'
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
const ToolbarDivider = styled.span`
  display: inline-block; width: 1px; height: 18px; background: ${colors.border}; margin: 0 2px;
`
const LangBox = styled.label`
  display: inline-flex; align-items: center; gap: 6px; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & input { width: 60px; }
`
const SubTabs = styled.div`display: flex; gap: 4px; border-bottom: 1px solid ${colors.border}; padding-bottom: 6px;`
const SubTab = styled.button<{ $active?: boolean }>`
  height: 30px; padding: 0 14px; border-radius: ${radius.sm}; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  &:hover { color: ${colors.text.primary}; background: ${({ $active }) => ($active ? colors.blue.bg : 'var(--hover-subtle)')}; }
`
const ScopeBar = styled.div`display: flex; flex-wrap: wrap; gap: 4px; align-items: center;`
const Chip = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & svg { color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)}; }
  &:hover { color: ${colors.text.primary}; }
`
const Split = styled.div`display: flex; gap: 14px; flex: 1; min-height: 0; align-items: stretch;`
// The left nav has its own scroll container so a long list (think 347 dictionary entries) doesn't
// drag the whole page along when you wheel through it. The search row and the "+ Add" button stay
// pinned outside the scroller, the items live in NavList. The flex chain (Shell → Split → NavCol →
// NavList) caps the list height to the page; no max-height hack needed.
const NavCol = styled.div`flex: 0 0 220px; display: flex; flex-direction: column; gap: 4px; min-width: 0; min-height: 0;`
const NavList = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px; padding-right: 4px;`
const NavSearch = styled.div`
  display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 8px; margin-bottom: 2px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: ${colors.bg.input}; color: ${colors.text.muted};
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
const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.5; margin: 0;`

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
  const { refresh: refreshWorkspace } = useWorkspace()
  const [schemas, setSchemas] = useState<ConfigSchemas | null>(null)
  const [path, setPath] = useState('')
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

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<DictionaryDoc>('/admin/config/dictionary/parsed'),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed'),
    ])
      .then(([s, d, c]) => {
        setSchemas(s); setPath(d.path); setDict(d.dictionary); setOriginal(JSON.stringify(d.dictionary))
        setConnectors(c.connectors)
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
    const key = (await modals.prompt({
      title: t(`settings.dictionary.${kind}.add`),
      message: t(`settings.dictionary.${kind}.namePrompt`),
    }))?.trim()
    if (!key) return
    if (key in section) { setSel(key); return }
    setDict(setSection(dict, scope, kind, { ...section, [key]: newRecord(kind) }))
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
    const next = { ...section }; delete next[key]
    setDict(setSection(dict, scope, kind, next))
    setSel((s) => (s === key ? null : s)); setStatus(null)
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
    const existing = Object.keys(section)
    const next = (await modals.prompt({
      title: t('settings.rename.button'),
      message: t(`settings.dictionary.${kind}.renamePrompt`, { name: oldKey }),
      defaultValue: oldKey,
      submitLabel: t('settings.rename.button'),
      validate: (v) => {
        const err = validateRename(oldKey, v, existing)
        if (err === 'unchanged') return null
        if (err === 'empty') return t('settings.rename.empty')
        if (err === 'exists') return t('settings.rename.exists', { name: v })
        // entries are typically uppercase (USR_ID, APPS_ID, …); the rest are lowercase v2 ids.
        // Both shapes accepted by the backend; the regex enforces letters/digits/underscores
        // with a leading letter.
        if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(v)) return t('settings.rename.invalidIdentifier')
        return null
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
  const addScope = async () => {
    const name = (await modals.prompt({
      title: t('settings.dictionary.scope.add'),
      message: t('settings.dictionary.scope.addPrompt'),
    }))?.trim()
    if (!name) return
    if (scopes.includes(name)) { setScope(name); return }
    // We just switch — the scope materialises in `dict.connectors` as soon as a record is added
    // (the section-cleanup in setSection won't drop it then). If the user adds nothing and saves,
    // the empty scope naturally disappears on the next round-trip.
    setScope(name)
  }
  const setLang = (lang: string) => {
    const trimmed = lang.trim()
    const next: DictionaryData = { ...dict }
    if (!trimmed || trimmed === 'en') delete next.default_language
    else next.default_language = trimmed
    setDict(next); setStatus(null)
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
      <ScopeBar style={{ flexShrink: 0 }}>
        <Mono>{path}</Mono>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
        {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        <LangBox title={t('settings.dictionary.langHint')} style={{ marginLeft: 'auto' }}>
          {t('settings.dictionary.lang')}
          <Input type="text" value={dict.default_language ?? ''} placeholder="en" onChange={(e) => setLang(e.target.value)} />
        </LangBox>
        <ToolbarDivider />
        <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <RefreshCw size={13} />} {t('settings.pools.reloadFromDisk')}
        </Button>
      </ScopeBar>
      <SubTabs>
        {KIND_ORDER.map((k) => (
          <SubTab key={k} $active={kind === k} type="button" onClick={() => { setKind(k); if (SHARED_ONLY.has(k)) setScope(SCOPE_SHARED) }}>{t(`settings.dictionary.${k}.tab`)}</SubTab>
        ))}
      </SubTabs>
      {SHARED_ONLY.has(kind) ? (
        // Framework enums live only in the shared scope; show a hint instead of the chip strip,
        // but still surface "Add <kind>" on the right so every kind has a discoverable add action
        // at the top (entries / enums / lookups / sequences / framework all follow the same UX).
        <ScopeBar>
          <Hint style={{ flex: 1, margin: 0 }}>{t('settings.dictionary.framework_enums.scopeNote')}</Hint>
          <Chip type="button" onClick={addRecord} title={t(`settings.dictionary.${kind}.add`)}>
            <Plus size={12} /> {t(`settings.dictionary.${kind}.add`)}
          </Chip>
        </ScopeBar>
      ) : (
        // Scope chips on the left, scope-level actions on the right. "Add <kind>" was at the
        // bottom of the record list before — promoting it here keeps every scope-level action
        // (add scope · add record) visible without scrolling past a long list.
        <ScopeBar>
          <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.dictionary.scope.label')}</span>
          {scopes.map((s) => (
            <Chip key={s || '_shared'} $active={scope === s} type="button" onClick={() => setScope(s)}>
              {s ? <Globe size={12} /> : <Layers size={12} />}{scopeLabel(s)}
            </Chip>
          ))}
          <Chip type="button" onClick={addScope} title={t('settings.dictionary.scope.addPrompt')}>
            <Plus size={12} /> {t('settings.dictionary.scope.add')}
          </Chip>
          <Chip type="button" onClick={addRecord} title={t(`settings.dictionary.${kind}.add`)} style={{ marginLeft: 'auto' }}>
            <Plus size={12} /> {t(`settings.dictionary.${kind}.add`)}
          </Chip>
        </ScopeBar>
      )}
      <Split>
        <NavCol>
          {keys.length > 6 && (
            <NavSearch>
              <Search size={13} />
              <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`filter ${keys.length}…`} />
            </NavSearch>
          )}
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
                  <Button $variant="ghost" $size="sm" onClick={() => renameRecord(sel)} disabled={busy}>
                    <Edit3 size={13} /> {t('settings.rename.button')}
                  </Button>
                  <Button $variant="danger" $size="sm" onClick={() => removeRecord(sel)} disabled={busy}>
                    <Trash2 size={13} /> {t(`settings.dictionary.${kind}.delete`)}
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
              />
            </Stack>
          ) : (
            <Empty>{keys.length ? t(`settings.dictionary.${kind}.pickOne`) : t(`settings.dictionary.${kind}.empty`)}</Empty>
          )}
        </FormCol>
      </Split>
    </Shell>
    </FrameworkEnumsContext.Provider>
  )
}
