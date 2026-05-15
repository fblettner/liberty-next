// Visual builder for a screen's Dialog — Figma-style canvas + inspector + palette.
//
// Layout (3 columns):
//   [Palette]           [Canvas]                                  [Inspector]
//   Dictionary entries  Tabs strip · field grid · per-tab actions  Selected field properties
//   Read-query columns  Click-to-select · drag-to-reorder          + lookup binds + conditions
//                                                                  + events panel (which hooks
//                                                                    fire for this dialog/row)
//
// This is the WYSIWYG counterpart to ScreenEditor's Schema mode. The data model is unchanged —
// every mutation flows through the same `onChange(nextScreen)` callback the schema editor uses,
// so toggling between Visual and Schema during edits keeps the work intact.
//
// Components in this file (all internal — exported only at the bottom):
//   - FieldCard      : one card on the canvas, with a small widget preview based on the dd rule
//   - TabsStrip      : the dialog's tabs at the top of the canvas, click to switch
//   - Palette        : left column with the two add-sources (Dictionary + Columns)
//   - Inspector      : right column with field props + binds + conditional rules + events
//   - EventsPanel    : a compact summary of which hooks fire for this dialog (uses screen.value)
//
// External data fetched on mount: dictionary.toml entries (for the connector scope) + the
// read_query's column names (via /api/sql with _limit=0). Both cached in component state; the
// dictionary feeds both the palette and the field-widget preview.
import { useCallback, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import {
  AlertTriangle, AlignLeft, Calendar, CheckSquare, Code2, Eye, EyeOff, FileText, Filter,
  Hash, Key, Layers, List, Lock, Plus, Search, Trash2, Zap, type LucideIcon,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Input, Row, SchemaForm, Stack, type JsonSchema } from '../../common'
import type { ConnectorsDoc, DictionaryDoc } from '../../types/config'
import type { Column, QueryResult } from '../../types/connectors'
import { colors, fontSize, fonts, radius } from '../../theme'
import { pickSchemaProperties } from './connectorTables'

type Row = Record<string, unknown>

// Pydantic field bucket subsets — same keys the Schema-mode editor uses (so SchemaForm renders
// identical-looking inputs in both modes). Kept here as constants so the Inspector reuses them
// without coupling back to ScreenEditor.
const FIELD_PROPS_KEYS = ['dd', 'label', 'hidden', 'disabled', 'required', 'colspan', 'default'] as const
const FIELD_BINDS_KEY = 'lookup_param_binds'
const FIELD_CONDITION_KEYS = ['visible_when', 'required_when', 'disabled_when'] as const

// ─── styled bits ────────────────────────────────────────────────────────────────────────────
const Shell = styled.div`
  display: grid; grid-template-columns: 240px 1fr 320px; gap: 12px; align-items: stretch;
  min-height: 480px;
`
const Col = styled.div`
  display: flex; flex-direction: column; gap: 10px; min-width: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.card}; padding: 12px;
`
const ColTitle = styled.div`
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; color: ${colors.text.muted};
  text-transform: uppercase; letter-spacing: 0.04em;
`
const SubTabs = styled.div`display: inline-flex; gap: 3px; padding: 3px; border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: ${colors.bg.input};`
const SubTab = styled.button<{ $active?: boolean }>`
  height: 24px; padding: 0 9px; border-radius: ${radius.sm}; border: none; cursor: pointer;
  background: ${({ $active }) => ($active ? colors.bg.card : 'transparent')};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  &:hover { color: ${colors.text.primary}; }
`
const SearchBar = styled.div`
  display: flex; align-items: center; gap: 6px; height: 26px; padding: 0 8px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: ${colors.bg.input}; color: ${colors.text.muted};
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; &::placeholder { color: ${colors.text.muted}; } }
`
const PaletteList = styled.div`
  flex: 1 1 auto; min-height: 0; overflow-y: auto; display: flex; flex-direction: column; gap: 4px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; padding: 4px;
  background: ${colors.bg.input};
`
const PaletteItem = styled.button`
  display: flex; align-items: center; gap: 6px; padding: 6px 8px; text-align: left;
  border: 1px solid transparent; border-radius: ${radius.sm}; background: transparent; cursor: grab;
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  & .lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .sub { color: ${colors.text.muted}; font-size: ${fontSize.micro}; font-family: ${fonts.sans}; }
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; border-color: ${colors.border}; }
  &:active { cursor: grabbing; }
`
const CanvasTabsStrip = styled.div`
  display: flex; gap: 4px; padding: 4px 0; border-bottom: 1px solid ${colors.border}; flex-wrap: wrap;
`
const CanvasTab = styled.button<{ $active?: boolean; $dropTarget?: boolean }>`
  display: inline-flex; align-items: center; gap: 6px; height: 28px; padding: 0 12px;
  border: 1px solid ${({ $active, $dropTarget }) => ($dropTarget ? colors.green.border : $active ? colors.blue.border : 'transparent')};
  border-bottom: 2px solid ${({ $active, $dropTarget }) => ($dropTarget ? colors.green.main : $active ? colors.blue.main : 'transparent')};
  background: ${({ $active, $dropTarget }) => ($dropTarget ? colors.green.bg : $active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer; border-radius: ${radius.sm} ${radius.sm} 0 0;
  & svg { color: ${colors.text.muted}; }
  &:hover { color: ${colors.text.primary}; }
`
const CanvasBody = styled.div`flex: 1; overflow-y: auto; padding: 14px 4px; display: flex; flex-direction: column; gap: 12px;`
const FieldGrid = styled.div<{ $cols: number }>`
  display: grid; grid-template-columns: repeat(${({ $cols }) => $cols}, 1fr); gap: 10px;
`
const Card = styled.div<{ $selected?: boolean; $hidden?: boolean; $span?: number; $dragOver?: boolean }>`
  grid-column: span ${({ $span }) => Math.max(1, $span ?? 1)};
  border: 1px solid ${({ $selected, $dragOver }) => ($dragOver ? colors.green.border : $selected ? colors.blue.main : colors.border)};
  border-radius: ${radius.md};
  background: ${({ $selected, $dragOver }) => ($dragOver ? colors.green.bg : $selected ? colors.blue.bg : colors.bg.input)};
  padding: 10px 12px; cursor: grab; display: flex; flex-direction: column; gap: 6px;
  opacity: ${({ $hidden }) => ($hidden ? 0.55 : 1)};
  position: relative;
  & .lbl { font-size: ${fontSize.sm}; color: ${colors.text.primary}; font-family: ${fonts.sans}; display: flex; align-items: center; gap: 6px; }
  & .icon { color: ${colors.text.muted}; flex-shrink: 0; }
  & .name { color: ${colors.text.muted}; font-family: ${fonts.mono}; font-size: ${fontSize.micro}; }
  & .preview { padding: 5px 8px; border-radius: ${radius.sm}; background: ${colors.bg.card};
    border: 1px solid ${colors.border}; color: ${colors.text.muted}; font-size: ${fontSize.micro};
    font-family: ${fonts.mono}; min-height: 22px; display: flex; align-items: center; gap: 6px; }
  & .badges { display: inline-flex; gap: 4px; flex-wrap: wrap; }
  &:hover { border-color: ${({ $selected }) => ($selected ? colors.blue.main : colors.blue.border)}; }
`
const Badge = styled.span<{ $tone?: 'orange' | 'red' | 'muted' | 'green' }>`
  display: inline-block; padding: 1px 5px; border-radius: ${radius.sm}; font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  border: 1px solid ${({ $tone }) => ($tone === 'orange' ? colors.orange.border : $tone === 'red' ? colors.red.border : $tone === 'green' ? colors.green.border : colors.border)};
  color: ${({ $tone }) => ($tone === 'orange' ? colors.orange.main : $tone === 'red' ? colors.red.main : $tone === 'green' ? colors.green.main : colors.text.muted)};
  background: ${({ $tone }) => ($tone === 'orange' ? colors.orange.bg : $tone === 'red' ? colors.red.bg : $tone === 'green' ? colors.green.bg : 'transparent')};
`
const AddSlot = styled.button`
  grid-column: span 1; display: flex; align-items: center; justify-content: center; gap: 6px;
  border: 1px dashed ${colors.border}; border-radius: ${radius.md}; padding: 14px; cursor: pointer;
  background: transparent; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`
const TabActionsRow = styled.div`
  display: flex; flex-wrap: wrap; gap: 6px; padding-top: 8px; border-top: 1px dashed ${colors.border};
`
const TabActionBadge = styled.div`
  display: inline-flex; align-items: center; gap: 5px; padding: 4px 8px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border}; background: ${colors.bg.card}; color: ${colors.text.secondary};
  font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  & svg { color: ${colors.orange.main}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 20px 4px; text-align: center;`
const InspSection = styled.div`display: flex; flex-direction: column; gap: 8px; padding-top: 8px; border-top: 1px solid ${colors.border}; &:first-of-type { border-top: 0; padding-top: 0; }`
const InspTitle = styled.div`color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-weight: 600; display: flex; align-items: center; gap: 6px; & svg { color: ${colors.text.muted}; }`
const EventRow = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: ${radius.sm};
  background: ${colors.bg.input}; color: ${colors.text.secondary}; font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  & .label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .count { color: ${colors.blue.main}; font-family: ${fonts.mono}; }
`
const NoEvents = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro}; font-family: ${fonts.sans}; font-style: italic;`

// ─── widget-preview helpers ─────────────────────────────────────────────────────────────────
// Pick the right icon + preview text for a field based on its dictionary rule (BOOLEAN / ENUM /
// LOOKUP), its format hint, and its name. Mirrors the runtime FieldRow widget choice so the
// canvas approximates what the user will see.
interface DictEntry { id: string; label?: string; format?: string; rules?: string; rules_values?: string }
interface FieldPreview { Icon: LucideIcon; sample: string; kindLabel: string }

function previewFor(field: Row, ddEntry: DictEntry | null): FieldPreview {
  // Password format = masked input (icon hint only — never shows real value).
  if ((field.format ?? ddEntry?.format ?? '').toString().toLowerCase() === 'password') {
    return { Icon: Lock, sample: '••••••••', kindLabel: 'password' }
  }
  // Dictionary rules drive the widget — BOOLEAN → checkbox, ENUM → dropdown, LOOKUP → search.
  const rule = (ddEntry?.rules ?? '').toUpperCase()
  if (rule === 'BOOLEAN') return { Icon: CheckSquare, sample: '☑ / ☐', kindLabel: 'boolean' }
  if (rule === 'ENUM')    return { Icon: List, sample: ddEntry?.rules_values ? `enum: ${ddEntry.rules_values}` : 'choose…', kindLabel: 'enum' }
  if (rule === 'LOOKUP')  return { Icon: Search, sample: ddEntry?.rules_values ? `lookup: ${ddEntry.rules_values}` : 'search…', kindLabel: 'lookup' }
  // Format-driven fallbacks. The migration emits `format` on entries that don't carry a `rules`.
  const fmt = ((field.format ?? ddEntry?.format ?? '') as string).toLowerCase()
  if (fmt === 'date' || fmt === 'datetime' || fmt === 'timestamp') return { Icon: Calendar, sample: 'YYYY-MM-DD', kindLabel: 'date' }
  if (fmt === 'number' || fmt === 'integer' || /int|numeric|decimal|float/.test(fmt)) {
    return { Icon: Hash, sample: '123', kindLabel: 'number' }
  }
  // Default — plain text.
  return { Icon: AlignLeft, sample: '…', kindLabel: 'text' }
}

// ─── component ──────────────────────────────────────────────────────────────────────────────
export interface ScreenVisualBuilderProps {
  app: string
  id: string
  value: Row
  schema: JsonSchema
  onChange: (next: Row) => void
}

export default function ScreenVisualBuilder({ app, value, schema, onChange }: ScreenVisualBuilderProps) {
  const { t } = useTranslation()
  const defs = (schema.$defs ?? {}) as Record<string, JsonSchema>

  // The selected screen's connector (explicit, else app). All palette fetches scope to this.
  const connector = (value.connector as string | undefined) ?? app
  const readQuery = (value.read_query as string | undefined) ?? ''

  // Dialog accessors — same shape ScreenEditor uses. Sub-state: which tab is active, which
  // field card is selected on that tab. Both reset to safe defaults when the dialog shape
  // changes.
  const dialog = (value.dialog && typeof value.dialog === 'object' ? value.dialog : null) as { title?: string; tabs?: Row[] } | null
  const tabs: Row[] = useMemo(() => (Array.isArray(dialog?.tabs) ? dialog!.tabs : []), [dialog])
  const [tabIdx, setTabIdx] = useState(0)
  useEffect(() => { if (tabIdx >= tabs.length) setTabIdx(Math.max(0, tabs.length - 1)) }, [tabs, tabIdx])
  const selTab = tabs[tabIdx]
  const fields: Row[] = useMemo(() => (Array.isArray(selTab?.fields) ? (selTab!.fields as Row[]) : []), [selTab])
  const tabActions: Row[] = useMemo(() => (Array.isArray(selTab?.actions) ? (selTab!.actions as Row[]) : []), [selTab])
  const cols = Math.max(1, Number(selTab?.cols ?? 2))
  const [selFieldIdx, setSelFieldIdx] = useState<number | null>(null)
  useEffect(() => { setSelFieldIdx(null) }, [tabIdx])

  // ── external data: dictionary entries scoped to the connector + read-query columns ───────
  const [ddEntries, setDdEntries] = useState<Map<string, DictEntry> | null>(null)
  const [readColumns, setReadColumns] = useState<Column[] | null>(null)
  const [externalError, setExternalError] = useState<string | null>(null)
  useEffect(() => {
    let cancelled = false
    setExternalError(null); setDdEntries(null); setReadColumns(null)
    // Both fetches run in parallel — palette renders whichever loads first.
    Promise.all([
      api.get<DictionaryDoc>('/admin/config/dictionary/parsed').catch((e) => {
        // Surface but don't abort — the canvas still works without the palette.
        console.warn('dictionary fetch failed', e)  // eslint-disable-line no-console
        return null
      }),
      api.get<ConnectorsDoc>('/admin/config/connectors/parsed').catch((e) => {
        console.warn('connectors fetch failed', e)  // eslint-disable-line no-console
        return null
      }),
    ]).then(([dd, _conns]) => {
      if (cancelled) return
      // Merge shared + per-connector entries — per-connector wins, same precedence the runtime uses.
      const map = new Map<string, DictEntry>()
      const ingest = (m: Record<string, Record<string, unknown>> | undefined) => {
        if (!m) return
        for (const [eid, rec] of Object.entries(m)) {
          map.set(eid, {
            id: eid,
            label: typeof rec.label === 'string' ? rec.label : undefined,
            format: typeof rec.format === 'string' ? rec.format : undefined,
            rules: typeof rec.rules === 'string' ? rec.rules : undefined,
            rules_values: typeof rec.rules_values === 'string' ? rec.rules_values : undefined,
          })
        }
      }
      ingest(dd?.dictionary.entries as Record<string, Record<string, unknown>> | undefined)
      const overlay = (dd?.dictionary.connectors ?? {})[connector] as { entries?: Record<string, Record<string, unknown>> } | undefined
      ingest(overlay?.entries)
      setDdEntries(map)
    })
    // Columns: fetch the read query with _limit=0 so we get the column metadata without rows. A
    // failure here is silent — the palette's Columns tab just stays empty.
    if (connector && readQuery) {
      api.get<QueryResult>(
        `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(readQuery)}?_limit=0`,
      ).then((r) => { if (!cancelled) setReadColumns(r.columns) })
        .catch((e) => {
          if (cancelled) return
          // Common failure modes: connector permission missing, query has required params we
          // don't have. Surface in a banner so the operator knows; don't crash the builder.
          setExternalError(e instanceof ApiError ? e.message : String(e))
        })
    }
    return () => { cancelled = true }
  }, [connector, readQuery])

  // ── canvas mutations ────────────────────────────────────────────────────────────────────
  const setDialog = useCallback((next: { title?: string; tabs?: Row[] } | null) => {
    if (!next || (!next.title && (!next.tabs || next.tabs.length === 0))) {
      const v = { ...value }; delete v.dialog
      onChange(v)
    } else {
      onChange({ ...value, dialog: next })
    }
  }, [value, onChange])
  const updateTab = useCallback((idx: number, patch: Row) => {
    const next = tabs.slice()
    next[idx] = { ...next[idx], ...patch }
    setDialog({ ...(dialog ?? {}), tabs: next })
  }, [tabs, dialog, setDialog])
  const updateFields = useCallback((nextFields: Row[]) => updateTab(tabIdx, { fields: nextFields }), [tabIdx, updateTab])
  const addFieldFromName = useCallback((fieldName: string, ddId?: string | null) => {
    if (!fieldName) return
    if (fields.some((f) => f.name === fieldName)) {
      // duplicate — just select the existing one rather than silently doing nothing.
      const ex = fields.findIndex((f) => f.name === fieldName)
      setSelFieldIdx(ex)
      return
    }
    const entry: Row = { name: fieldName }
    if (ddId && ddId !== fieldName) entry.dd = ddId
    const nextFields = [...fields, entry]
    updateFields(nextFields)
    setSelFieldIdx(nextFields.length - 1)
  }, [fields, updateFields])
  const updateField = useCallback((idx: number, patch: Row) => {
    const next = fields.slice()
    next[idx] = { ...next[idx], ...patch }
    // Drop falsy optional keys so the saved TOML stays terse (matches the schema-mode editor).
    for (const k of Object.keys(patch)) {
      if (patch[k] === undefined || patch[k] === null || patch[k] === '' || patch[k] === false) {
        delete (next[idx] as Row)[k]
      }
    }
    updateFields(next)
  }, [fields, updateFields])
  const deleteField = useCallback((idx: number) => {
    if (!window.confirm(t('settings.screens.field.confirmDelete', { name: fields[idx]?.name }))) return
    const next = fields.slice(); next.splice(idx, 1)
    updateFields(next)
    setSelFieldIdx((cur) => (cur === idx ? null : cur != null && cur > idx ? cur - 1 : cur))
  }, [fields, updateFields, t])
  // Drag-to-reorder within the same tab. HTML5 native DnD; the dragged index lives in state so
  // the drop target knows what to splice. Cross-tab drop = move the field to the target tab.
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [dragOverIdx, setDragOverIdx] = useState<number | null>(null)
  const [dragOverTab, setDragOverTab] = useState<number | null>(null)
  const onDragStart = (idx: number) => (e: React.DragEvent) => {
    setDragIdx(idx); e.dataTransfer.effectAllowed = 'move'
    // The data is set just so the browser's "this is a draggable" semantics kick in — we read
    // from state, not from the event payload.
    e.dataTransfer.setData('text/plain', String(idx))
  }
  const onDragEnter = (idx: number) => (e: React.DragEvent) => { e.preventDefault(); setDragOverIdx(idx) }
  const onDragLeave = () => setDragOverIdx(null)
  const onDragOver = (e: React.DragEvent) => { e.preventDefault(); e.dataTransfer.dropEffect = 'move' }
  const onDrop = (idx: number) => (e: React.DragEvent) => {
    e.preventDefault()
    if (dragIdx == null || dragIdx === idx) { setDragIdx(null); setDragOverIdx(null); return }
    const next = fields.slice()
    const [moved] = next.splice(dragIdx, 1)
    next.splice(idx, 0, moved)
    updateFields(next)
    setSelFieldIdx(idx)
    setDragIdx(null); setDragOverIdx(null)
  }
  // Cross-tab drop on the tabs strip — moves a field from the current tab to a different one.
  // Skipped when the user lets go on the same tab.
  const onTabDragOver = (targetTabIdx: number) => (e: React.DragEvent) => {
    if (dragIdx == null || targetTabIdx === tabIdx) return
    e.preventDefault(); e.dataTransfer.dropEffect = 'move'
    setDragOverTab(targetTabIdx)
  }
  const onTabDrop = (targetTabIdx: number) => (e: React.DragEvent) => {
    e.preventDefault(); setDragOverTab(null)
    if (dragIdx == null || targetTabIdx === tabIdx) { setDragIdx(null); return }
    const movedField = fields[dragIdx]
    if (!movedField) return
    const next = tabs.slice()
    // Remove from the source tab + append to the target tab's field list.
    next[tabIdx] = { ...next[tabIdx], fields: fields.filter((_, i) => i !== dragIdx) }
    const targetFields = Array.isArray(next[targetTabIdx]?.fields) ? (next[targetTabIdx].fields as Row[]) : []
    next[targetTabIdx] = { ...next[targetTabIdx], fields: [...targetFields, movedField] }
    setDialog({ ...(dialog ?? {}), tabs: next })
    setTabIdx(targetTabIdx); setSelFieldIdx(targetFields.length); setDragIdx(null)
  }

  // ── inspector sub-schemas ────────────────────────────────────────────────────────────────
  // Pick subsets of ScreenField so each SchemaForm renders just the section it owns. Same
  // approach the Schema-mode editor uses; pulled here so the Inspector is self-contained.
  const fieldDef = useMemo<JsonSchema>(() => ({ ...((defs.ScreenField as JsonSchema | undefined) ?? { type: 'object' }), $defs: defs }), [defs])
  const fieldPropsSchema = useMemo<JsonSchema>(() => pickSchemaProperties(fieldDef, FIELD_PROPS_KEYS as unknown as string[]), [fieldDef])
  const bindsSchema = useMemo<JsonSchema>(() => {
    const out = pickSchemaProperties(fieldDef, [FIELD_BINDS_KEY] as unknown as string[])
    return { ...out, $defs: defs }
  }, [fieldDef, defs])
  const conditionsSchema = useMemo<JsonSchema>(() => {
    const out = pickSchemaProperties(fieldDef, FIELD_CONDITION_KEYS as unknown as string[])
    return { ...out, $defs: defs }
  }, [fieldDef, defs])

  // ── palette + canvas state ───────────────────────────────────────────────────────────────
  const [paletteSrc, setPaletteSrc] = useState<'dict' | 'cols'>('dict')
  const [paletteQ, setPaletteQ] = useState('')

  const ddItems = useMemo(() => {
    if (!ddEntries) return [] as DictEntry[]
    const all = [...ddEntries.values()]
    const needle = paletteQ.trim().toLowerCase()
    const filtered = needle
      ? all.filter((e) => e.id.toLowerCase().includes(needle) || (e.label ?? '').toLowerCase().includes(needle))
      : all
    return filtered.sort((a, b) => a.id.localeCompare(b.id))
  }, [ddEntries, paletteQ])
  const colItems = useMemo(() => {
    if (!readColumns) return [] as Column[]
    const needle = paletteQ.trim().toLowerCase()
    return needle ? readColumns.filter((c) => c.name.toLowerCase().includes(needle) || (c.label ?? '').toLowerCase().includes(needle)) : readColumns
  }, [readColumns, paletteQ])

  // Adding from each palette source. Dictionary: use the entry id as the field's name (matches
  // v1's convention where dd_id == col_target). Columns: use the column name; if a matching DD
  // entry exists, auto-link it via `dd` for free.
  const addFromDict = (e: DictEntry) => addFieldFromName(e.id, e.id)
  const addFromCol = (c: Column) => {
    const matchedDd = ddEntries?.get(c.name) ? c.name : null
    addFieldFromName(c.name, matchedDd)
  }

  // ── tabs strip helpers ───────────────────────────────────────────────────────────────────
  const addTab = () => {
    const id = window.prompt(t('settings.screens.tab.namePrompt'))?.trim()
    if (!id) return
    if (tabs.some((tt) => tt.id === id)) { window.alert(t('settings.screens.tab.idExists', { id })); return }
    const next = [...tabs, { id, fields: [] }]
    setDialog({ ...(dialog ?? {}), tabs: next })
    setTabIdx(next.length - 1)
  }
  const createDialog = () => {
    setDialog({ tabs: [{ id: 'general', label: 'General', fields: [] }] })
    setTabIdx(0)
  }

  // ── events panel: which hooks are wired on this screen ───────────────────────────────────
  // We don't enumerate per-field events (fields don't have direct hooks); we show the dialog-
  // and screen-level hooks so the operator sees the *contextual* events that fire while their
  // selected field is on-screen / being saved / being deleted. The hook lists come straight off
  // the screen value — operators edit them via the Schema mode's renderActionList editor.
  const eventBuckets = useMemo(() => ([
    { key: 'dialog.on_load',    label: t('settings.screens.onLoad.heading'),   count: dialogHookCount(value, 'on_load') },
    { key: 'dialog.on_save',    label: t('settings.screens.action.heading'),  count: dialogHookCount(value, 'on_save') },
    { key: 'dialog.on_cancel',  label: t('settings.screens.onCancel.heading'),count: dialogHookCount(value, 'on_cancel') },
    { key: 'screen.on_insert',  label: t('settings.screens.onInsert.heading'),count: hookCount(value, 'on_insert') },
    { key: 'screen.on_update',  label: t('settings.screens.onUpdate.heading'),count: hookCount(value, 'on_update') },
    { key: 'screen.on_delete',  label: t('settings.screens.onDelete.heading'),count: hookCount(value, 'on_delete') },
    { key: 'tab.actions',       label: t('settings.screens.tabActions.heading'), count: tabActions.length },
  ]), [value, tabActions.length, t])

  // ── render ───────────────────────────────────────────────────────────────────────────────
  if (!dialog) {
    return (
      <Stack gap={14}>
        <Empty>
          <Stack gap={12} style={{ alignItems: 'center' }}>
            <div>{t('settings.screens.editor.dialogEmpty')}</div>
            <Button $variant="primary" $size="sm" onClick={createDialog}>
              <Plus size={13} /> {t('settings.screens.editor.dialogCreate')}
            </Button>
          </Stack>
        </Empty>
      </Stack>
    )
  }

  const selField: Row | null = selFieldIdx != null ? fields[selFieldIdx] ?? null : null

  return (
    <Shell>
      {/* ─── PALETTE (left) ─── */}
      <Col>
        <ColTitle>{t('settings.screens.visual.palette.title')}</ColTitle>
        <SubTabs>
          <SubTab type="button" $active={paletteSrc === 'dict'} onClick={() => setPaletteSrc('dict')}>
            {t('settings.screens.visual.palette.dict')}
          </SubTab>
          <SubTab type="button" $active={paletteSrc === 'cols'} onClick={() => setPaletteSrc('cols')}>
            {t('settings.screens.visual.palette.cols')}
          </SubTab>
        </SubTabs>
        <SearchBar>
          <Search size={12} />
          <input value={paletteQ} onChange={(e) => setPaletteQ(e.target.value)} placeholder={t('settings.screens.visual.palette.search')} />
        </SearchBar>
        <PaletteList>
          {paletteSrc === 'dict' ? (
            ddEntries == null ? (
              <Empty>{t('common.loading')}</Empty>
            ) : ddItems.length === 0 ? (
              <Empty>{t('common.noMatches')}</Empty>
            ) : ddItems.map((e) => (
              <PaletteItem key={e.id} type="button" onClick={() => addFromDict(e)}>
                <Key size={11} />
                <span className="lbl">{e.id}</span>
                {e.label && <span className="sub">{e.label}</span>}
              </PaletteItem>
            ))
          ) : (
            readColumns == null ? (
              externalError
                ? <Empty>{externalError}</Empty>
                : <Empty>{t('common.loading')}</Empty>
            ) : colItems.length === 0 ? (
              <Empty>{t('common.noMatches')}</Empty>
            ) : colItems.map((c) => (
              <PaletteItem key={c.name} type="button" onClick={() => addFromCol(c)} title={c.type ?? ''}>
                <Code2 size={11} />
                <span className="lbl">{c.name}</span>
                {c.label && <span className="sub">{c.label}</span>}
              </PaletteItem>
            ))
          )}
        </PaletteList>
        <div style={{ color: colors.text.muted, fontSize: fontSize.micro, lineHeight: 1.4 }}>
          {t('settings.screens.visual.palette.hint')}
        </div>
      </Col>

      {/* ─── CANVAS (center) ─── */}
      <Col>
        <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
          <ColTitle>{t('settings.screens.visual.canvas.title')}</ColTitle>
          <div style={{ color: colors.text.muted, fontSize: fontSize.micro, fontFamily: fonts.mono }}>
            cols: {cols} · fields: {fields.length}
          </div>
        </Row>
        {externalError && paletteSrc === 'cols' && <Banner $tone="info">{externalError}</Banner>}
        <CanvasTabsStrip>
          {tabs.map((tab, i) => (
            <CanvasTab
              key={`${tab.id}_${i}`}
              type="button"
              $active={i === tabIdx}
              $dropTarget={dragOverTab === i}
              onClick={() => setTabIdx(i)}
              onDragOver={onTabDragOver(i)}
              onDragLeave={() => setDragOverTab(null)}
              onDrop={onTabDrop(i)}
            >
              <Layers size={11} />
              {String(tab.label || tab.id)}
              {Array.isArray(tab.fields) && <span style={{ color: colors.text.muted, fontSize: fontSize.micro }}>· {(tab.fields as unknown[]).length}</span>}
            </CanvasTab>
          ))}
          <Button $variant="ghost" $size="sm" onClick={addTab}>
            <Plus size={11} /> {t('settings.screens.tab.add')}
          </Button>
        </CanvasTabsStrip>
        <CanvasBody>
          {selTab ? (
            <>
              <FieldGrid $cols={cols}>
                {fields.map((f, i) => {
                  const name = String(f.name ?? '')
                  const ddId = (f.dd as string | undefined) || name
                  const dd = ddEntries?.get(ddId) ?? null
                  const preview = previewFor(f, dd)
                  const label = (f.label as string | undefined) ?? dd?.label ?? name
                  const span = Math.max(1, Number(f.colspan ?? 1))
                  const Icon = preview.Icon
                  return (
                    <Card
                      key={`${name}_${i}`}
                      $selected={selFieldIdx === i}
                      $hidden={!!f.hidden}
                      $span={span}
                      $dragOver={dragOverIdx === i && dragIdx !== i}
                      draggable
                      onDragStart={onDragStart(i)}
                      onDragEnter={onDragEnter(i)}
                      onDragLeave={onDragLeave}
                      onDragOver={onDragOver}
                      onDrop={onDrop(i)}
                      onClick={() => setSelFieldIdx(i)}
                    >
                      <div className="lbl">
                        <Icon size={13} className="icon" />
                        <span>{label}</span>
                        {(f.required as boolean) && <span style={{ color: colors.orange.main }}>*</span>}
                      </div>
                      <div className="preview"><Icon size={11} /> {preview.sample}</div>
                      <div className="badges">
                        <span className="name">{name}</span>
                        {f.dd != null && f.dd !== '' && f.dd !== name && <Badge>dd:{String(f.dd)}</Badge>}
                        {!!f.hidden && <Badge $tone="muted"><EyeOff size={10} style={{ verticalAlign: 'middle' }} /> {t('settings.screens.field.hidden')}</Badge>}
                        {!!f.disabled && <Badge $tone="muted"><Lock size={10} style={{ verticalAlign: 'middle' }} /> {t('settings.screens.field.disabled')}</Badge>}
                        {(Array.isArray(f.visible_when) && (f.visible_when as unknown[]).length > 0)
                          || (Array.isArray(f.required_when) && (f.required_when as unknown[]).length > 0)
                          || (Array.isArray(f.disabled_when) && (f.disabled_when as unknown[]).length > 0)
                          ? <Badge $tone="orange"><Filter size={10} style={{ verticalAlign: 'middle' }} /> {t('settings.screens.field.conditional')}</Badge>
                          : null}
                        {Array.isArray(f.lookup_param_binds) && (f.lookup_param_binds as unknown[]).length > 0 && (
                          <Badge $tone="green">{t('settings.screens.field.binds', { count: (f.lookup_param_binds as unknown[]).length })}</Badge>
                        )}
                      </div>
                    </Card>
                  )
                })}
                <AddSlot onClick={() => {
                  const name = window.prompt(t('settings.screens.field.namePrompt'))?.trim()
                  if (name) addFieldFromName(name)
                }}>
                  <Plus size={13} /> {t('settings.screens.field.add')}
                </AddSlot>
              </FieldGrid>
              {tabActions.length > 0 && (
                <TabActionsRow>
                  {tabActions.map((a, i) => (
                    <TabActionBadge key={`${a.id}_${i}`} title={String(a.id ?? '')}>
                      <Zap size={11} />
                      {String(a.label ?? a.id ?? '?')}
                      <span style={{ color: colors.text.muted, fontFamily: fonts.mono, fontSize: fontSize.micro }}>· {String(a.type ?? 'run_query')}</span>
                    </TabActionBadge>
                  ))}
                </TabActionsRow>
              )}
            </>
          ) : (
            <Empty>{t('settings.screens.tab.pickOne')}</Empty>
          )}
        </CanvasBody>
      </Col>

      {/* ─── INSPECTOR (right) ─── */}
      <Col>
        <ColTitle>{t('settings.screens.visual.inspector.title')}</ColTitle>
        {selField ? (
          <>
            <InspSection>
              <InspTitle><FileText size={13} /> {String(selField.name ?? '')}</InspTitle>
              <SchemaForm
                schema={fieldPropsSchema}
                defs={defs}
                value={selField}
                onChange={(v) => {
                  const patch: Row = {}
                  for (const k of FIELD_PROPS_KEYS) patch[k] = v[k]
                  updateField(selFieldIdx!, patch)
                }}
              />
            </InspSection>
            <InspSection>
              <InspTitle><Search size={13} /> {t('settings.screens.visual.bindsTitle')}</InspTitle>
              <SchemaForm
                schema={bindsSchema}
                defs={defs}
                value={selField}
                onChange={(v) => updateField(selFieldIdx!, { [FIELD_BINDS_KEY]: v[FIELD_BINDS_KEY] })}
              />
            </InspSection>
            <InspSection>
              <InspTitle><Filter size={13} /> {t('settings.screens.field.conditional')}</InspTitle>
              <SchemaForm
                schema={conditionsSchema}
                defs={defs}
                value={selField}
                onChange={(v) => {
                  const patch: Row = {}
                  for (const k of FIELD_CONDITION_KEYS) patch[k] = v[k]
                  updateField(selFieldIdx!, patch)
                }}
              />
            </InspSection>
            <InspSection>
              <InspTitle><Zap size={13} /> {t('settings.screens.visual.events.title')}</InspTitle>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {eventBuckets.every((b) => b.count === 0) ? (
                  <NoEvents>{t('settings.screens.visual.events.none')}</NoEvents>
                ) : eventBuckets.filter((b) => b.count > 0).map((b) => (
                  <EventRow key={b.key}>
                    <Zap size={10} />
                    <span className="label">{b.label}</span>
                    <span className="count">{b.count}</span>
                  </EventRow>
                ))}
              </div>
              <div style={{ color: colors.text.muted, fontSize: fontSize.micro, lineHeight: 1.4 }}>
                {t('settings.screens.visual.events.hint')}
              </div>
            </InspSection>
            <Row>
              <Button $variant="danger" $size="sm" onClick={() => deleteField(selFieldIdx!)}>
                <Trash2 size={13} /> {t('settings.screens.field.delete')}
              </Button>
            </Row>
          </>
        ) : (
          <Stack gap={10}>
            <Empty>{t('settings.screens.visual.inspector.empty')}</Empty>
            <InspSection>
              <InspTitle><Eye size={13} /> {t('settings.screens.visual.dialog.title')}</InspTitle>
              <Input
                value={(dialog.title as string | undefined) ?? ''}
                onChange={(e) => setDialog({ ...dialog, title: e.target.value || undefined })}
                placeholder={t('settings.screens.visual.dialog.titlePlaceholder')}
              />
            </InspSection>
            <InspSection>
              <InspTitle><Zap size={13} /> {t('settings.screens.visual.events.title')}</InspTitle>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {eventBuckets.every((b) => b.count === 0) ? (
                  <NoEvents>{t('settings.screens.visual.events.none')}</NoEvents>
                ) : eventBuckets.filter((b) => b.count > 0).map((b) => (
                  <EventRow key={b.key}><Zap size={10} /><span className="label">{b.label}</span><span className="count">{b.count}</span></EventRow>
                ))}
              </div>
              {externalError && paletteSrc === 'cols' && (
                <div style={{ color: colors.orange.main, fontSize: fontSize.micro, display: 'flex', alignItems: 'center', gap: 4 }}>
                  <AlertTriangle size={11} /> {t('settings.screens.visual.events.colsWarning')}
                </div>
              )}
            </InspSection>
          </Stack>
        )}
      </Col>
    </Shell>
  )
}

// ── helpers (module-level) ─────────────────────────────────────────────────────────────────
function dialogHookCount(screen: Row, hook: 'on_load' | 'on_save' | 'on_cancel'): number {
  const d = screen.dialog as Row | undefined
  return Array.isArray(d?.[hook]) ? (d![hook] as unknown[]).length : 0
}
function hookCount(screen: Row, hook: 'on_insert' | 'on_update' | 'on_delete'): number {
  return Array.isArray(screen[hook]) ? (screen[hook] as unknown[]).length : 0
}
