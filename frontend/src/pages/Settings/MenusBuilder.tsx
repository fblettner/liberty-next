// Structured editor for `config/menus.toml` — v2's port of v1's ``ly_menus`` (per-app nav trees).
// Left = the apps list (one chip per `[menus.<app>]`). Right = the selected app's tree + a per-item
// inspector. The tree is built on the fly from the flat `items[]` linked by `parent`; clicking a
// row selects it for editing in the inspector, the action buttons reorder (↑↓), reparent
// (indent / outdent) or delete (recursive — drops the subtree). Add buttons create a new folder
// or leaf, indented under the current selection. The inspector is a SchemaForm over the MenuItem
// schema (so `type` becomes the MENU_ITEM_TYPE dropdown, translations land in their tab, etc.) —
// hidden parent field is what tree ops mutate, the user shouldn't normally touch it.
// Save validates the whole MenusFile + rewrites `menus.toml` via tomlkit (the `[menus]` table is
// re-rendered wholesale — flat items round-trip cleanly), then reloads. No rename of an app's key
// yet (delete + re-add) but the inspector lets you rename an item's `id` and the tree updates
// every child's `parent` reference to match.
import { useEffect, useMemo, useState, type ReactElement } from 'react'
import styled from '@emotion/styled'
import { Save, RefreshCw, Plus, Trash2, Search, FolderTree, FolderOpen, Folder, FileText, ChevronRight, ChevronDown, ArrowUp, ArrowDown, ArrowLeft, ArrowRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Button, Banner, Centered, Row, Stack, SpinnerRing, SchemaForm, SearchSelect, FrameworkEnumsContext, useModals, type FrameworkEnums, type JsonSchema } from '../../common'
import type { AppMenu, ConfigSchemas, ConnectorsDoc, MenuItem, MenusDoc } from '../../types/config'
import { groupQueriesByTable } from './connectorTables'
import { colors, fontSize, fonts, radius } from '../../theme'

type AppsMap = Record<string, AppMenu>

// ── styled bits ──────────────────────────────────────────────────────────────
// (Split / NavCol / NavList / NavItem / FormCol removed — the outer two-column layout collapsed
// when app selection moved to the scope-chip strip. The TreeSplit below is now the top-level
// layout for the selected app's content: tree on the left, inspector on the right.)
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px;`
const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.5; margin: 0;`

// Layout: outer Shell flex-fills the page, top toolbar is fixed, the per-app stack fills the
// remaining vertical space, and only the inner panels scroll. Same pattern as PoolsBuilder.
const Shell = styled.div`
  display: flex; flex-direction: column; gap: 12px;
  flex: 1; min-height: 0; height: 100%;
`
const ToolbarDivider = styled.span`
  display: inline-block; width: 1px; height: 18px; background: ${colors.border}; margin: 0 2px;
`
// The split flex-fills the remaining vertical space — expanding/collapsing folders never
// resizes the column — TreeBox / InspectorInner scroll internally. `align-items: stretch` makes
// both columns the same height (so the empty space below a short tree stays put instead of being
// pushed up by the inspector's content).
const TreeSplit = styled.div`
  display: flex; gap: 14px; align-items: stretch; min-height: 0;
  flex: 1 1 auto;
`
const TreeCol = styled.div`
  flex: 0 0 400px; display: flex; flex-direction: column; gap: 4px; min-width: 0; min-height: 0;
`
const TreeBox = styled.div`
  flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 4px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
`
const InspectorCol = styled.div`
  flex: 1; min-width: 0; min-height: 0; overflow-y: auto;
`
// Cap the inspector form's width so on a wide screen the inputs aren't half-a-page wide. The
// container still flexes (it can be narrower); this is just the upper bound.
const InspectorInner = styled.div`max-width: 720px;`
// The row shows [chev] [icon] [label] [id]  · on hover an overlay [actions] fades in. The id and
// actions are LAYOUT-stable — actions are absolutely positioned over the right edge with
// `opacity` for show/hide, the id fades the opposite way. No `display` toggles, so the row
// doesn't reflow on hover (which would cause the flicker / "elements jumping" effect).
const TreeRow = styled.button<{ $active?: boolean; $depth: number }>`
  position: relative;
  display: flex; align-items: center; gap: 4px; width: 100%; padding: 5px 8px; padding-left: calc(${({ $depth }) => $depth * 16}px + 8px);
  text-align: left; border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; border-radius: ${radius.sm}; cursor: pointer;
  & .chev { width: 14px; flex-shrink: 0; color: ${colors.text.muted}; display: inline-flex; align-items: center; justify-content: center; }
  & .icon { flex-shrink: 0; color: ${colors.text.muted}; }
  & .lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .id  {
    font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.mono}; margin-left: 6px; flex-shrink: 0;
    transition: opacity 0.12s ease;
  }
  & .actions {
    position: absolute; right: 6px; top: 50%; transform: translateY(-50%);
    display: inline-flex; align-items: center; gap: 2px; flex-shrink: 0;
    opacity: 0; pointer-events: none;
    background: var(--hover-subtle); border-radius: ${radius.sm}; padding: 2px;
    transition: opacity 0.12s ease;
  }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
  &:hover .id, &:focus-within .id { opacity: 0; }
  &:hover .actions, &:focus-within .actions { opacity: 1; pointer-events: auto; }
`
const IconBtn = styled.span`
  display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px;
  border-radius: ${radius.sm}; color: ${colors.text.muted}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
`
const NavSearch = styled.div`
  display: flex; align-items: center; gap: 6px; height: 28px; padding: 0 8px; margin-bottom: 2px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: ${colors.bg.input}; color: ${colors.text.muted};
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; &::placeholder { color: ${colors.text.muted}; } }
`
const AppHeader = styled(Row)`align-items: center; justify-content: space-between; gap: 10px;`
const AppLabel = styled.strong`font-family: ${fonts.mono}; color: ${colors.text.primary};`
// Scope row — matches DictionaryBuilder + ScreensBuilder pattern: horizontal chip strip above
// the split, sans-serif label, 26px-high pill chips. Replaces the old left-column app list so
// every builder shares the same "config path → scope chips → [list | detail]" layout.
const ScopeBar = styled.div`display: flex; flex-wrap: wrap; gap: 4px; align-items: center;`
const ScopeChip = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 10px; border-radius: 999px; cursor: pointer;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & svg { color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)}; }
  &:hover { color: ${colors.text.primary}; }
`

// ── helpers ──────────────────────────────────────────────────────────────────
const isFolder = (it: MenuItem) => !it.type

/** Group children by parent id (and a special `null` bucket for top-level). Preserves the order
 *  of `items[]`, which is what's rendered top-down — every move operation mutates that order. */
function groupByParent(items: MenuItem[]): Map<string | null, MenuItem[]> {
  const out = new Map<string | null, MenuItem[]>()
  for (const it of items) {
    const k = it.parent ?? null
    if (!out.has(k)) out.set(k, [])
    out.get(k)!.push(it)
  }
  return out
}

/** Find the indices of `id`'s direct neighbours among siblings — `previous` and `next` (`null`
 *  when at the start/end). Used by move ↑/↓ / indent (which reparents under the previous sibling). */
function findSiblings(items: MenuItem[], id: string): { current: number; previous: string | null; next: string | null } {
  const idx = items.findIndex((it) => it.id === id)
  if (idx < 0) return { current: -1, previous: null, next: null }
  const cur = items[idx]
  const parent = cur.parent ?? null
  let prev: string | null = null
  let nxt: string | null = null
  let found = false
  for (let i = 0; i < items.length; i++) {
    const it = items[i]
    if ((it.parent ?? null) !== parent) continue
    if (it.id === id) { found = true; continue }
    if (!found) prev = it.id
    else if (nxt === null) nxt = it.id
  }
  return { current: idx, previous: prev, next: nxt }
}

/** Move `id`'s row in `items[]` so it lands immediately after `targetId` (or before, for `dir=before`).
 *  We move the *whole subtree* together — collect descendants first, splice them as one block.
 *  Without this, moving a folder up would leave its children behind in the flat order. */
function moveBlock(items: MenuItem[], id: string, targetId: string, dir: 'before' | 'after'): MenuItem[] {
  const block = collectSubtree(items, id)
  const blockIds = new Set(block.map((b) => b.id))
  const without = items.filter((it) => !blockIds.has(it.id))
  const ti = without.findIndex((it) => it.id === targetId)
  if (ti < 0) return items
  const insertAt = dir === 'after' ? ti + 1 : ti
  return [...without.slice(0, insertAt), ...block, ...without.slice(insertAt)]
}

function collectSubtree(items: MenuItem[], rootId: string): MenuItem[] {
  const out: MenuItem[] = []
  const root = items.find((it) => it.id === rootId)
  if (!root) return out
  out.push(root)
  const queue: string[] = [rootId]
  const seen = new Set<string>([rootId])
  while (queue.length) {
    const pid = queue.shift()!
    for (const it of items) {
      if (it.parent === pid && !seen.has(it.id)) {
        out.push(it)
        seen.add(it.id)
        queue.push(it.id)
      }
    }
  }
  return out
}

function removeSubtree(items: MenuItem[], rootId: string): MenuItem[] {
  const drop = new Set(collectSubtree(items, rootId).map((b) => b.id))
  return items.filter((it) => !drop.has(it.id))
}

/** All ids that would become circular if we reparent `id` to them — `id` itself + its descendants. */
function descendantIds(items: MenuItem[], id: string): Set<string> {
  return new Set(collectSubtree(items, id).map((b) => b.id))
}

/** A fresh, unique id within `items`, derived from `seed` (a label or just "new"). */
function freshId(items: MenuItem[], seed: string): string {
  const base = seed.toLowerCase().replace(/[^a-z0-9_]/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '') || 'new'
  if (!items.some((it) => it.id === base)) return base
  for (let i = 2; i < 1000; i++) {
    const cand = `${base}_${i}`
    if (!items.some((it) => it.id === cand)) return cand
  }
  return `${base}_${Date.now()}`
}

// ── component ────────────────────────────────────────────────────────────────
export default function MenusBuilder() {
  const { t } = useTranslation()
  const modals = useModals()
  const [schemas, setSchemas] = useState<ConfigSchemas | null>(null)
  // Read-only — the inspector's CONNECTOR / TARGET dropdowns pull from here. Loaded alongside
  // the menus + schema so the framework-enum augmentation can offer real query / endpoint names.
  const [connectors, setConnectors] = useState<Record<string, Record<string, unknown>> | null>(null)
  const [apps, setApps] = useState<AppsMap | null>(null)
  const [original, setOriginal] = useState('')
  const [selApp, setSelApp] = useState<string | null>(null)
  const [selItem, setSelItem] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [appQ, setAppQ] = useState('')
  const [treeQ, setTreeQ] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = () => {
    setError(null); setStatus(null)
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<MenusDoc>('/admin/config/menus/parsed'),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed'),
    ])
      .then(([s, d, c]) => {
        setSchemas(s); setApps(d.menus); setOriginal(JSON.stringify(d.menus))
        setConnectors(c.connectors)
        setSelApp((cur) => (cur && d.menus[cur] ? cur : Object.keys(d.menus)[0] ?? null))
      })
      .catch((e) => setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)))
  }
  useEffect(load, [t])
  useEffect(() => { setSelItem(null); setTreeQ(''); setExpanded(new Set()) }, [selApp])

  const dirty = useMemo(() => apps != null && JSON.stringify(apps) !== original, [apps, original])

  // Tree filter: matched rows + their ancestors. Hook MUST stay above the early returns below so
  // it runs on every render (otherwise React #310 — the hook count changes between the loading
  // and loaded renders). Source from raw state so it handles the null cases.
  const matchedIds = useMemo(() => {
    if (!apps || !selApp) return null
    const items: MenuItem[] = apps[selApp]?.items ?? []
    const needle = treeQ.trim().toLowerCase()
    if (!needle) return null
    const direct = new Set(
      items.filter((it) => it.id.toLowerCase().includes(needle) || it.label.toLowerCase().includes(needle)).map((it) => it.id),
    )
    let changed = true
    while (changed) {
      changed = false
      for (const it of items) {
        if (direct.has(it.id) && it.parent && !direct.has(it.parent)) {
          direct.add(it.parent); changed = true
        }
      }
    }
    return direct
  }, [apps, selApp, treeQ])

  // Augment the framework enums with the three menus-specific data-driven dropdowns the inspector
  // needs. All three are recomputed when the selected item changes (because `target` depends on
  // *that* item's `type` + `connector`, `parent` excludes the item's own descendants, etc.).
  // Kept above the early returns so the hook count stays stable across loading / loaded renders.
  const augmentedEnums: FrameworkEnums | null = useMemo(() => {
    if (!schemas) return null
    const base: FrameworkEnums = { ...(schemas.framework_enums ?? {}) }

    // CONNECTOR_NAMES — every connector defined in connectors.toml, ordered. Used by the menu
    // item's `connector` field (blank → the app's own connector, which is the convention).
    const connNames = connectors ? Object.keys(connectors).sort() : []
    base.CONNECTOR_NAMES = {
      label: 'Connectors',
      values: connNames.map((n) => ({ value: n, label: n })),
    }

    // MENU_PARENT_IDS — every item in the current app *except* the selected one's descendants
    // (which would form a cycle) and the item itself. Folders are surfaced first (they're the
    // intended parents); leaves still appear because v2 lets any item host children.
    const appItems: MenuItem[] = (apps && selApp) ? (apps[selApp]?.items ?? []) : []
    const forbidden = selItem ? descendantIds(appItems, selItem) : new Set<string>()
    const parentValues: { value: string; label: string }[] = []
    for (const it of appItems) {
      if (forbidden.has(it.id)) continue
      const isFolderRow = !it.type
      parentValues.push({ value: it.id, label: it.label || it.id, ...(isFolderRow ? {} : { /* leaf */ }) })
    }
    base.MENU_PARENT_IDS = { label: 'Available parents', values: parentValues }

    // MENU_TARGETS — *tables* (the read query for each base name, grouped by suffix) plus loose
    // non-CRUD queries, OR endpoints. type=query → group the SQL connector's queries by table
    // (the same `<base>_<get|put|post|delete>` rule the connector Tables view uses); the dropdown
    // row shows mono="F0005" + label="Address Book Master" but stores value="f0005_get" — so the
    // user picks the *screen* not a specific CRUD slot. Loose queries (no CRUD suffix — bespoke
    // SELECTs that don't fit the table convention) are listed after the tables. type=endpoint →
    // a flat list of endpoints (no table grouping there). Empty for folders.
    const selRec = appItems.find((it) => it.id === selItem)
    const connKey = (typeof selRec?.connector === 'string' && selRec.connector) ? selRec.connector : selApp
    const conn = connectors && connKey ? connectors[connKey] : undefined
    const targets: { value: string; label: string; mono?: string }[] = []
    if (selRec?.type === 'query' && conn?.type === 'sql') {
      const qs = Array.isArray(conn.queries) ? (conn.queries as Record<string, unknown>[]) : []
      const grouped = groupQueriesByTable(qs)
      for (const g of grouped.tables) {
        if (!g.slots.get) continue   // a table without a _get isn't a viewable target
        const q = g.slots.get.query
        const desc = typeof q?.description === 'string' ? q.description : (typeof q?.label === 'string' ? q.label : '')
        targets.push({ value: g.slots.get.name, label: desc || g.base, mono: g.base })
      }
      for (const ls of grouped.loose) {
        if (!ls.name) continue
        const desc = typeof ls.query?.description === 'string' ? ls.query.description : (typeof ls.query?.label === 'string' ? ls.query.label : '')
        targets.push({ value: ls.name, label: desc || ls.name, mono: ls.name })
      }
    } else if (selRec?.type === 'endpoint' && conn?.type === 'api') {
      const es = Array.isArray(conn.endpoints) ? (conn.endpoints as Record<string, unknown>[]) : []
      for (const e of es) {
        const name = typeof e?.name === 'string' ? e.name : ''
        if (!name) continue
        const desc = typeof e?.description === 'string' ? e.description : (typeof e?.label === 'string' ? e.label : '')
        targets.push({ value: name, label: desc || name })
      }
    }
    base.MENU_TARGETS = {
      label: selRec?.type === 'endpoint' ? `Endpoints — ${connKey ?? ''}` : `Queries — ${connKey ?? ''}`,
      values: targets,
    }

    // MENU_HOME_ITEMS — every leaf item in the current app (any item with a ``type`` +
    // ``target``). Powers the per-app ``home`` SearchSelect so the operator picks an existing
    // menu item id rather than typing one. Folders are filtered out (a folder home resolves
    // to no path on the backend; we hide them here to avoid the confusing pick).
    const homeValues: { value: string; label: string; mono?: string }[] = appItems
      .filter((it) => it.type && it.target)
      .map((it) => ({
        value: it.id,
        label: it.label || it.id,
        mono: it.id,
      }))
    base.MENU_HOME_ITEMS = {
      label: selApp ? `Items in ${selApp}` : 'Pick an app first',
      values: homeValues,
    }
    return base
  }, [schemas, connectors, apps, selApp, selItem])

  if (error && !apps) return <Banner $tone="error">{error}</Banner>
  if (!apps || !schemas) return <Centered />

  const menusSchema = schemas.menus
  const defs = (menusSchema?.$defs ?? {}) as Record<string, JsonSchema>
  const menuItemSchema: JsonSchema | null = defs.MenuItem ?? null
  // Alphabetical sort for the scope-chip strip — operators expect a stable, predictable order.
  // **Tree items WITHIN an app are NOT sorted** — operators set the menu order explicitly via
  // the up / down / indent / outdent buttons on each row (the byParent groups + renderRow walks
  // keep their authored position).
  const appNames = Object.keys(apps).sort((a, b) => a.localeCompare(b))
  const appNeedle = appQ.trim().toLowerCase()
  const shownApps = appNeedle ? appNames.filter((n) => n.toLowerCase().includes(appNeedle)) : appNames

  const currentApp: AppMenu | null = selApp ? apps[selApp] ?? null : null
  const items: MenuItem[] = currentApp?.items ?? []
  const byParent = groupByParent(items)

  const selItemRec = items.find((it) => it.id === selItem) ?? null

  // ── mutations: every helper returns a new apps map ────────────────────────
  const updateApp = (name: string, next: AppMenu) =>
    setApps((p) => ({ ...(p ?? {}), [name]: next }))
  const updateItems = (next: MenuItem[]) =>
    selApp && currentApp && updateApp(selApp, { ...currentApp, items: next })

  const addApp = async () => {
    const name = (await modals.prompt({
      title: t('settings.menus.app.add'),
      message: t('settings.menus.app.namePrompt'),
    }))?.trim()
    if (!name) return
    if (apps[name]) { setSelApp(name); return }
    setApps((p) => ({ ...(p ?? {}), [name]: { items: [] } }))
    setSelApp(name); setStatus(null)
  }
  const removeApp = async (name: string) => {
    const ok = await modals.confirm({
      title: t('settings.menus.app.delete'),
      message: t('settings.menus.app.confirmDelete', { name }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    setApps((p) => { const n = { ...(p ?? {}) }; delete n[name]; return n })
    setSelApp((s) => (s === name ? null : s)); setStatus(null)
  }
  const renameItem = async (oldId: string, newId: string) => {
    if (!newId || newId === oldId) return
    if (items.some((it) => it.id === newId)) {
      await modals.alert({
        title: t('settings.menus.app.add'),
        message: t('settings.menus.item.idExists', { id: newId }),
        variant: 'danger',
      })
      return
    }
    updateItems(items.map((it) => {
      const next: MenuItem = { ...it }
      if (next.id === oldId) next.id = newId
      if (next.parent === oldId) next.parent = newId
      return next
    }))
    setSelItem(newId)
  }
  const patchItem = (id: string, patch: Partial<MenuItem>) => {
    if ('id' in patch && patch.id !== id) { renameItem(id, patch.id!); return }
    updateItems(items.map((it) => (it.id === id ? { ...it, ...patch } : it)))
  }
  const addItem = (kind: 'folder' | 'leaf', parent: string | null = null) => {
    const seed = kind === 'folder' ? 'folder' : 'item'
    const newId = freshId(items, seed)
    const next: MenuItem = kind === 'folder'
      ? { id: newId, label: seed, parent: parent ?? undefined }
      : { id: newId, label: seed, type: 'query', target: '', parent: parent ?? undefined }
    // Insert right after the last child of `parent` so it appears in place; otherwise at the end.
    const lastChild = [...items].reverse().find((it) => (it.parent ?? null) === parent)
    if (lastChild) {
      updateItems(moveBlock([...items, next], newId, lastChild.id, 'after'))
    } else {
      updateItems([...items, next])
    }
    if (parent) setExpanded((s) => new Set([...s, parent]))
    setSelItem(newId)
  }
  const removeItem = async (id: string) => {
    const rec = items.find((it) => it.id === id)
    if (!rec) return
    const hasChildren = items.some((it) => it.parent === id)
    if (hasChildren) {
      const ok = await modals.confirm({
        title: t('common.delete'),
        message: t('settings.menus.item.confirmDeleteSubtree', { name: rec.label || rec.id }),
        variant: 'danger',
        confirmLabel: t('common.delete'),
      })
      if (!ok) return
    }
    updateItems(removeSubtree(items, id))
    if (selItem === id) setSelItem(null)
  }
  const moveItem = (id: string, dir: 'up' | 'down') => {
    const sib = findSiblings(items, id)
    if (dir === 'up' && sib.previous) updateItems(moveBlock(items, id, sib.previous, 'before'))
    else if (dir === 'down' && sib.next) updateItems(moveBlock(items, id, sib.next, 'after'))
  }
  const indentItem = (id: string) => {
    // Reparent under the previous sibling. The previous sibling becomes a folder *de facto* —
    // we don't auto-clear its type, so an item with type "query" + children is still allowed
    // by the schema (the runtime treats it as a leaf, but the builder shows nested rows
    // anyway — leave the validation to the user).
    const sib = findSiblings(items, id)
    if (!sib.previous) return
    patchItem(id, { parent: sib.previous })
    setExpanded((s) => new Set([...s, sib.previous!]))
  }
  const outdentItem = (id: string) => {
    const rec = items.find((it) => it.id === id); if (!rec || !rec.parent) return
    const parent = items.find((it) => it.id === rec.parent); if (!parent) return
    patchItem(id, { parent: parent.parent ?? undefined })
  }

  async function save() {
    if (!apps) return
    setBusy(true); setError(null); setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/menus/parsed', { menus: apps })
      await api.post<{ menu_apps: string[] }>('/admin/reload')
      setStatus(t('settings.menus.saved'))
      load()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  // ── tree rendering ────────────────────────────────────────────────────────
  const renderRow = (it: MenuItem, depth: number): ReactElement | null => {
    if (matchedIds && !matchedIds.has(it.id)) return null
    const folder = isFolder(it)
    const kids = byParent.get(it.id) ?? []
    const open = expanded.has(it.id)
    const sib = findSiblings(items, it.id)
    const isSel = it.id === selItem
    const icon = folder
      ? (open ? <FolderOpen size={14} /> : <Folder size={14} />)
      : <FileText size={14} />
    return (
      <div key={it.id}>
        <TreeRow type="button" $active={isSel} $depth={depth}
          onClick={() => setSelItem(it.id)}
          onDoubleClick={() => { if (folder) setExpanded((s) => { const n = new Set(s); n.has(it.id) ? n.delete(it.id) : n.add(it.id); return n }) }}
        >
          <span className="chev" onClick={(e) => { e.stopPropagation(); if (kids.length) setExpanded((s) => { const n = new Set(s); n.has(it.id) ? n.delete(it.id) : n.add(it.id); return n }) }}>
            {kids.length > 0 ? (open ? <ChevronDown size={12} /> : <ChevronRight size={12} />) : null}
          </span>
          <span className="icon">{icon}</span>
          <span className="lbl">{it.label || <em style={{ color: colors.text.muted }}>(no label)</em>}</span>
          <span className="id">{it.id}</span>
          <span className="actions" onClick={(e) => e.stopPropagation()}>
            <IconBtn role="button" title={t('settings.menus.tree.up')} aria-disabled={!sib.previous} onClick={() => sib.previous && moveItem(it.id, 'up')} style={{ opacity: sib.previous ? 1 : 0.3 }}><ArrowUp size={12} /></IconBtn>
            <IconBtn role="button" title={t('settings.menus.tree.down')} aria-disabled={!sib.next} onClick={() => sib.next && moveItem(it.id, 'down')} style={{ opacity: sib.next ? 1 : 0.3 }}><ArrowDown size={12} /></IconBtn>
            <IconBtn role="button" title={t('settings.menus.tree.indent')} aria-disabled={!sib.previous} onClick={() => sib.previous && indentItem(it.id)} style={{ opacity: sib.previous ? 1 : 0.3 }}><ArrowRight size={12} /></IconBtn>
            <IconBtn role="button" title={t('settings.menus.tree.outdent')} aria-disabled={!it.parent} onClick={() => it.parent && outdentItem(it.id)} style={{ opacity: it.parent ? 1 : 0.3 }}><ArrowLeft size={12} /></IconBtn>
            <IconBtn role="button" title={t('settings.menus.tree.addChild')} onClick={() => addItem('leaf', it.id)}><Plus size={12} /></IconBtn>
            <IconBtn role="button" title={t('common.delete')} onClick={() => removeItem(it.id)}><Trash2 size={12} /></IconBtn>
          </span>
        </TreeRow>
        {kids.length > 0 && (open || !!matchedIds) && (
          <div>
            {kids.map((c) => renderRow(c, depth + 1))}
          </div>
        )}
      </div>
    )
  }

  return (
    <FrameworkEnumsContext.Provider value={augmentedEnums}>
    <Shell>
      {/* One consolidated top toolbar — config path + status on the left, scope chips (apps) and
          every action (Add app · Delete app · Save · Reload) inline on the right. Replaces the
          old "scope bar at top + bottom Save Row" split — the operator never has to scroll past a
          long tree to reach Save / Reload. The optional search box appears next to the chips when
          there are more than ~6 apps. */}
      <ScopeBar>
        {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
        {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
        {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        <span style={{ color: colors.text.muted, fontSize: fontSize.sm, marginLeft: 8 }}>{t('settings.menus.scopeLabel')}</span>
        {appNames.length > 6 && (
          <NavSearch style={{ marginBottom: 0, height: 26 }}>
            <Search size={12} />
            <input value={appQ} onChange={(e) => setAppQ(e.target.value)} placeholder={`filter ${appNames.length}…`} />
          </NavSearch>
        )}
        {shownApps.map((n) => (
          <ScopeChip key={n} $active={n === selApp} type="button" onClick={() => { setSelApp(n); setStatus(null) }}>
            <FolderTree size={12} /> {n}
          </ScopeChip>
        ))}
        <ScopeChip type="button" onClick={addApp}>
          <Plus size={12} /> {t('settings.menus.app.add')}
        </ScopeChip>
        {selApp && (
          <ScopeChip type="button" onClick={() => removeApp(selApp)} title={t('settings.menus.app.deleteOne', { name: selApp })} style={{ marginLeft: 'auto', color: colors.red.main, borderColor: colors.red.border }}>
            <Trash2 size={12} /> {t('settings.menus.app.deleteOne', { name: selApp })}
          </ScopeChip>
        )}
        <ToolbarDivider style={{ marginLeft: selApp ? 0 : 'auto' }} />
        <Button $variant="primary" $size="sm" onClick={save} disabled={busy || !dirty}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
        </Button>
        <Button $variant="ghost" $size="sm" onClick={load} disabled={busy} title={t('settings.pools.reloadFromDisk')}>
          {busy ? <SpinnerRing size={13} thickness={2} /> : <RefreshCw size={13} />} {t('settings.pools.reloadFromDisk')}
        </Button>
      </ScopeBar>
      {selApp && currentApp ? (
        <Stack gap={12} style={{ flex: 1, minHeight: 0 }}>
          <AppHeader style={{ flexShrink: 0 }}>
            <AppLabel>[menus.{selApp}] <span style={{ color: colors.text.muted, fontWeight: 400 }}>· {items.length} {t('settings.menus.item.count', { count: items.length })}</span></AppLabel>
            {/* Per-app home page — SearchSelect over the app's leaf items (any with a
                ``type`` + ``target``). When set, picking this app from the workspace picker
                navigates straight to the matching path (e.g. ``/dashboard/nomasx1_overview``
                for nomasx1's overview). Defaults to unset → falls through to the connector
                index. The list comes from the ``MENU_HOME_ITEMS`` augmented enum above. */}
            <Row gap={8} style={{ alignItems: 'center' }}>
              <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>
                {t('settings.menus.app.home', 'Home page')}
              </span>
              <div style={{ minWidth: 240 }}>
                <SearchSelect
                  value={(currentApp.home as string | undefined) ?? ''}
                  options={(augmentedEnums?.MENU_HOME_ITEMS?.values ?? []) as Array<{ value: string; label: string; mono?: string }>}
                  onChange={(v: string) => updateApp(selApp, { ...currentApp, home: v || null })}
                  anyLabel={t('settings.menus.app.homeNone', 'No home page (blank landing)')}
                  placeholder={t('common.pick')}
                />
              </div>
            </Row>
          </AppHeader>
          <TreeSplit>
                <TreeCol>
                  <NavSearch>
                    <Search size={13} />
                    <input value={treeQ} onChange={(e) => setTreeQ(e.target.value)} placeholder={t('settings.menus.tree.filterPlaceholder', { count: items.length })} />
                  </NavSearch>
                  <TreeBox>
                    {(byParent.get(null) ?? []).map((it) => renderRow(it, 0))}
                    {items.length === 0 && <Empty>{t('settings.menus.tree.empty')}</Empty>}
                  </TreeBox>
                  <Row gap={6} style={{ marginTop: 6 }}>
                    <Button $variant="ghost" $size="sm" onClick={() => addItem('folder')} style={{ flex: 1, justifyContent: 'flex-start' }}>
                      <Plus size={13} /> {t('settings.menus.tree.addFolder')}
                    </Button>
                    <Button $variant="ghost" $size="sm" onClick={() => addItem('leaf')} style={{ flex: 1, justifyContent: 'flex-start' }}>
                      <Plus size={13} /> {t('settings.menus.tree.addLeaf')}
                    </Button>
                  </Row>
                </TreeCol>
                <InspectorCol>
                  <InspectorInner>
                    {selItemRec && menuItemSchema ? (
                      <Stack gap={10}>
                        <Hint>{t('settings.menus.inspector.hint')}</Hint>
                        <SchemaForm
                          schema={{ ...menuItemSchema, $defs: defs }}
                          defs={defs}
                          value={selItemRec as unknown as Record<string, unknown>}
                          onChange={(v) => {
                            // SchemaForm gives the whole picked value back. Detect id rename so we
                            // can also update children's `parent` refs (see renameItem).
                            const nextId = typeof v.id === 'string' ? v.id : selItemRec.id
                            if (nextId !== selItemRec.id) { renameItem(selItemRec.id, nextId); return }
                            updateItems(items.map((it) => (it.id === selItemRec.id ? (v as unknown as MenuItem) : it)))
                          }}
                        />
                        {/* No "Allowed parents:" hint here — the ``parent`` field already renders
                            as a SearchSelect populated with the valid ids via the
                            ``MENU_PARENT_IDS`` framework enum (set up in this builder, just
                            below). Dumping the same list as a hint added a wall of text
                            without telling the operator anything they couldn't see in the
                            dropdown. */}
                      </Stack>
                    ) : (
                      <Empty>{items.length ? t('settings.menus.inspector.pickOne') : t('settings.menus.inspector.empty')}</Empty>
                    )}
                  </InspectorInner>
                </InspectorCol>
              </TreeSplit>
        </Stack>
      ) : (
        <Empty>{appNames.length ? t('settings.menus.app.pickOne') : t('settings.menus.app.empty')}</Empty>
      )}
    </Shell>
    </FrameworkEnumsContext.Provider>
  )
}
