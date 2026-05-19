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
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import {
  AlertTriangle, AlignLeft, Calendar, CheckSquare, ChevronRight as ChevronRightIcon, Code2,
  Edit3, Eye, EyeOff, FileText, Filter, Hash, Key, Layers, List, Lock, Plus, Search, Trash2, Zap,
  type LucideIcon,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Field, Input, Row, SchemaForm, SearchSelect, Stack, useModals, type JsonSchema, type SearchSelectOption } from '../../common'
import type { ConnectorsDoc, DictionaryDoc } from '../../types/config'
import type { Column, QueryResult } from '../../types/connectors'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { EditQueryModal } from './EditQueryModal'
import { colors, fontSize, fonts, radius } from '../../theme'
import { pickSchemaProperties } from './connectorTables'

type Row = Record<string, unknown>

// Pydantic field bucket subsets — same keys the Schema-mode editor uses (so SchemaForm renders
// identical-looking inputs in both modes). Split into Basic (always visible) + Advanced
// (collapsed by default) so the inspector isn't a wall of every property; operators rarely set
// colspan / default but need ``dd`` / ``label`` / ``required`` on every field.
// ``hidden`` / ``disabled`` / ``required`` are *inheritable* overrides — rendered through
// ``OverrideToggle`` (3-state: Inherit / Yes / No) below, not through SchemaForm. They live
// on ScreenField as ``bool | None`` (null = inherit the column's value), and the canvas badges
// + dialog runtime read the effective value (field value, else column default).
const FIELD_BASIC_KEYS = ['dd', 'label'] as const
const FIELD_ADVANCED_KEYS = ['colspan', 'default'] as const
const FIELD_BINDS_KEY = 'lookup_param_binds'
const FIELD_CONDITION_KEYS = ['visible_when', 'required_when', 'disabled_when'] as const

// Three-state override control for ``ScreenField`` flags that can inherit from their matching
// ``ColumnHint``. Renders three pills: "Inherit (col: yes/no)" / "Yes" / "No" so the operator
// can clearly see the current state, the column's default, and how to reset to inherit.
// ``value`` semantics:
//   * ``true``  → field explicitly overrides to *yes*
//   * ``false`` → field explicitly overrides to *no*
//   * ``null`` / ``undefined`` → field inherits the column's default
const OverrideWrap = styled.div`display: flex; gap: 4px; flex-wrap: wrap; align-items: center;`
const OverridePill = styled.button<{ $active?: boolean }>`
  display: inline-flex; align-items: center; gap: 4px; padding: 3px 9px; height: 24px;
  border-radius: ${({ theme }) => (theme as { radius?: { sm: string } })?.radius?.sm ?? '6px'};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-family: ${fonts.sans}; font-size: ${fontSize.micro}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`
const InheritedHint = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.micro}; font-style: italic; margin-left: 4px;`
function OverrideToggle(props: {
  label: string
  value: boolean | null | undefined
  inherited: boolean
  onChange: (next: boolean | null) => void
  inheritedYesLabel?: string   // e.g. "hidden" / "required" / "read-only"
  inheritedNoLabel?: string    // e.g. "shown"  / "optional" / "editable"
}) {
  const { t } = useTranslation()
  const { label, value, inherited, onChange, inheritedYesLabel = 'yes', inheritedNoLabel = 'no' } = props
  const isOverridden = value === true || value === false
  const inheritedWord = inherited ? inheritedYesLabel : inheritedNoLabel
  return (
    <Field label={label}>
      <OverrideWrap>
        <OverridePill type="button" $active={!isOverridden} onClick={() => onChange(null)}>
          {t('settings.screens.visual.override.inherit', 'Inherit')}
        </OverridePill>
        <OverridePill type="button" $active={value === true} onClick={() => onChange(true)}>
          {t('common.yes', 'Yes')}
        </OverridePill>
        <OverridePill type="button" $active={value === false} onClick={() => onChange(false)}>
          {t('common.no', 'No')}
        </OverridePill>
        {!isOverridden && (
          <InheritedHint>
            {t('settings.screens.visual.override.fromColumn', 'from column: {{val}}', { val: inheritedWord })}
          </InheritedHint>
        )}
      </OverrideWrap>
    </Field>
  )
}

// ─── styled bits ────────────────────────────────────────────────────────────────────────────
// Shell + columns assume they're mounted inside a fixed-height container (a Modal body or a
// constrained Stack). The `min-height: 0` on Col is the flex-child quirk that lets each
// column's PaletteList / CanvasBody / inspector content scroll on its own — without it the
// children's natural content height would force the whole row to grow and overflow the modal.
const Shell = styled.div`
  display: grid; grid-template-columns: 240px 1fr 340px; gap: 12px;
  flex: 1 1 auto; min-height: 0; height: 100%;
`
const Col = styled.div`
  display: flex; flex-direction: column; gap: 10px; min-width: 0; min-height: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.card}; padding: 12px;
  overflow: hidden;
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
// Tab settings panel — sits at the top of the canvas body, above the field grid. Lets the
// operator set the tab type (form / nested_form / nested_table) and the kind-specific properties
// (queries + param_binds for nested_form, target screen + param_binds for nested_table). For
// plain form tabs it's collapsed by default (just label / cols / hide-on-* are inside); for
// nested kinds it expands by default so the queries are visible at a glance.
const TabSettingsBox = styled.details`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 10px 12px;
  background: ${colors.bg.input};
  & summary {
    list-style: none; cursor: pointer; color: ${colors.text.secondary}; font-size: ${fontSize.sm};
    font-family: ${fonts.sans}; font-weight: 600; padding: 2px 0;
    display: flex; align-items: center; gap: 8px;
  }
  & summary::-webkit-details-marker { display: none; }
  & summary > svg:first-of-type { color: ${colors.text.muted}; transition: transform 0.15s; }
  &[open] summary > svg:first-of-type { transform: rotate(90deg); }
  & summary .kind {
    margin-left: auto; font-family: ${fonts.mono}; font-size: ${fontSize.micro};
    color: ${colors.text.muted}; font-weight: 400;
    padding: 2px 7px; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
    background: ${colors.bg.card};
  }
  & > div { padding-top: 10px; display: flex; flex-direction: column; gap: 10px; }
`
const BindList = styled.div`
  display: flex; flex-direction: column; gap: 4px; border: 1px solid ${colors.border};
  border-radius: ${radius.sm}; padding: 6px; background: ${colors.bg.card};
`
const BindRow = styled.div`
  display: grid; grid-template-columns: 1fr auto 1fr 32px; gap: 6px; align-items: center;
  & .arrow { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const RemoveBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: transparent;
  color: ${colors.text.muted}; cursor: pointer;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; }
`
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
// Collapsible group for hidden fields — closed by default so the 20+ migration-kept columns
// don't flood the visible canvas. Same <details>/<summary> pattern as the inspector's Advanced
// block; chevron rotates when the group opens.
const HiddenGroup = styled.details`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 10px 12px;
  background: ${colors.bg.input}; margin-top: 6px;
  & summary {
    list-style: none; cursor: pointer; color: ${colors.text.secondary}; font-size: ${fontSize.sm};
    font-family: ${fonts.sans}; font-weight: 600; padding: 2px 0;
    display: flex; align-items: center; gap: 6px;
  }
  & summary::-webkit-details-marker { display: none; }
  & summary > svg:first-of-type { color: ${colors.text.muted}; transition: transform 0.15s; }
  &[open] summary > svg:first-of-type { transform: rotate(90deg); }
`
const TabActionBadge = styled.div`
  display: inline-flex; align-items: center; gap: 5px; padding: 4px 8px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border}; background: ${colors.bg.card}; color: ${colors.text.secondary};
  font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  & svg { color: ${colors.orange.main}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 20px 4px; text-align: center;`
// Inspector wrapper that owns the scroll — gap-stacks the sections inside; ``min-height: 0`` is
// the flex-child quirk that lets it shrink + scroll inside the modal's body row.
const InspBody = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; padding-right: 4px; display: flex; flex-direction: column; gap: 12px;`
const InspSection = styled.div`display: flex; flex-direction: column; gap: 8px; padding-top: 8px; border-top: 1px solid ${colors.border}; &:first-of-type { border-top: 0; padding-top: 0; }`
const InspTitle = styled.div`color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-weight: 600; display: flex; align-items: center; gap: 6px; & svg { color: ${colors.text.muted}; }`
// "Dialog › <field>" breadcrumb at the top of the inspector — clicking "Dialog" deselects the
// field and brings the dialog-level inspector back (title editor + events). The chevron tile
// between segments looks like the URL-style breadcrumbs operators are used to.
const Crumbs = styled.div`display: flex; align-items: center; gap: 5px; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; padding-bottom: 6px; border-bottom: 1px solid ${colors.border};`
const CrumbBtn = styled.button<{ $active?: boolean }>`
  background: transparent; border: none; cursor: ${({ $active }) => ($active ? 'default' : 'pointer')};
  padding: 0; color: ${({ $active }) => ($active ? colors.text.primary : colors.blue.main)};
  font: inherit;
  &:hover { text-decoration: ${({ $active }) => ($active ? 'none' : 'underline')}; }
`
// Collapsible "Advanced" group — uses a styled <details>/<summary> pair so the toggle works
// without any JS state. Closed by default; the operator opens it when they need colspan /
// default / per-field flags.
const AdvDetails = styled.details`
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; padding: 6px 10px;
  background: ${colors.bg.input};
  & summary {
    list-style: none; cursor: pointer; color: ${colors.text.secondary}; font-size: ${fontSize.sm};
    font-family: ${fonts.sans}; font-weight: 600; padding: 2px 0;
    display: flex; align-items: center; gap: 6px;
  }
  & summary::-webkit-details-marker { display: none; }
  & summary svg { color: ${colors.text.muted}; transition: transform 0.15s; }
  &[open] summary svg { transform: rotate(90deg); }
  & > div { padding-top: 10px; display: flex; flex-direction: column; gap: 10px; }
`
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

function previewFor(field: Row, ddEntry: DictEntry | null, column: Row | null): FieldPreview {
  // Phase 2 — per-column rule override (v1's col_rules) lives on the screen's ColumnHint.
  // The dictionary entry's rule is the fallback. Same precedence on format.
  const colRule = (column?.rules as string | undefined) ?? ''
  const colRulesValues = (column?.rules_values as string | undefined) ?? ''
  const colFormat = (column?.format as string | undefined) ?? ''
  // Password format = masked input (icon hint only — never shows real value).
  if ((colFormat || ddEntry?.format || '').toString().toLowerCase() === 'password') {
    return { Icon: Lock, sample: '••••••••', kindLabel: 'password' }
  }
  // Rules drive the widget — column override wins; else dictionary entry's rule.
  const rule = (colRule || ddEntry?.rules || '').toUpperCase()
  const ruleVals = colRulesValues || ddEntry?.rules_values
  if (rule === 'BOOLEAN') return { Icon: CheckSquare, sample: '☑ / ☐', kindLabel: 'boolean' }
  if (rule === 'ENUM')    return { Icon: List, sample: ruleVals ? `enum: ${ruleVals}` : 'choose…', kindLabel: 'enum' }
  if (rule === 'LOOKUP')  return { Icon: Search, sample: ruleVals ? `lookup: ${ruleVals}` : 'search…', kindLabel: 'lookup' }
  // Format-driven fallbacks. The migration emits `format` on entries that don't carry a `rules`.
  void field   // kept for signature stability — field-level format is gone in Phase 2.
  const fmt = (colFormat || ddEntry?.format || '').toLowerCase()
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
  const modals = useModals()
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
  // ``editQuery`` raises ``EditQueryModal`` from a nested-form tab's query SearchSelect's
  // pencil button — same pattern the Screen Editor's Queries tab uses. Cleared when the
  // modal closes.
  const [editQuery, setEditQuery] = useState<{ connector: string; queryName: string } | null>(null)
  useEffect(() => { if (tabIdx >= tabs.length) setTabIdx(Math.max(0, tabs.length - 1)) }, [tabs, tabIdx])
  const selTab = tabs[tabIdx]
  const fields: Row[] = useMemo(() => (Array.isArray(selTab?.fields) ? (selTab!.fields as Row[]) : []), [selTab])
  const tabActions: Row[] = useMemo(() => (Array.isArray(selTab?.actions) ? (selTab!.actions as Row[]) : []), [selTab])
  const cols = Math.max(1, Number(selTab?.cols ?? 2))
  const [selFieldIdx, setSelFieldIdx] = useState<number | null>(null)
  useEffect(() => { setSelFieldIdx(null) }, [tabIdx])
  // Phase 2 — display metadata (dd / label / format / lookup_param_binds / rules / default)
  // lives on ``Screen.columns`` (single source of truth, shared between grid + dialog). Build
  // a case-insensitive lookup once so each field card can resolve its matching ColumnHint.
  const screenColumns: Row[] = useMemo(
    () => (Array.isArray(value.columns) ? (value.columns as Row[]) : []),
    [value.columns],
  )
  const columnByName = useMemo(() => {
    const m = new Map<string, Row>()
    for (const c of screenColumns) {
      const n = String(c.name ?? '').toLowerCase()
      if (n) m.set(n, c)
    }
    return m
  }, [screenColumns])
  const colFor = useCallback(
    (fieldName: unknown): Row | null => columnByName.get(String(fieldName ?? '').toLowerCase()) ?? null,
    [columnByName],
  )
  // The selected tab's `type` discriminator — drives which Tab Settings fields render. A tab
  // missing ``type`` is treated as 'form' (matches the backend's `parse_screens` default).
  const tabType = (selTab && typeof selTab.type === 'string' ? selTab.type : 'form') as 'form' | 'nested_form' | 'nested_table'

  // ── workspace + cross-screen pickers ──────────────────────────────────────────────────────
  // Dropdowns inside the Tab Settings panel read from the workspace catalog (same source the
  // ScreenEditor's General + Queries tabs use). Per-tab connector picker over SQL connectors,
  // queries scoped to the chosen connector, target-screen picker (for nested_table) over the
  // screens defined under the current app.
  const { connectors: wsConnectors, screens: wsScreens } = useWorkspace()
  const sqlConnectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? []).filter((c) => c.type === 'sql').map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [wsConnectors],
  )
  // Per-tab effective connector: the tab's explicit ``connector`` field, else the screen's own
  // effective connector (which itself falls back to the app name). This is the source for the
  // tab's queries / endpoints picker — and what the tab's read_query / update_query / etc.
  // actually run against at runtime.
  const tabEffectiveConnector = (selTab && typeof selTab.connector === 'string' && selTab.connector.trim() ? selTab.connector : connector)
  const tabConnectorMeta = useMemo(
    () => (wsConnectors ?? []).find((c) => c.name === tabEffectiveConnector),
    [wsConnectors, tabEffectiveConnector],
  )
  const tabQueryOptions = useMemo<SearchSelectOption[]>(() => {
    if (!tabConnectorMeta || tabConnectorMeta.type !== 'sql') return []
    return tabConnectorMeta.queries.map((q) => ({
      value: q.name,
      label: q.description || q.label || q.name,
      mono: q.name,
    }))
  }, [tabConnectorMeta])
  // Helper: nested-form query picker (read_query / update_query / insert_query) — SearchSelect
  // over the tab's effective connector + an adjacent "Edit query" pencil that raises
  // EditQueryModal so the operator can tweak the SQL without leaving the visual designer.
  // Same shape the Screen Editor's Queries tab uses. ``patchTab`` is bound from the closure.
  const renderTabQueryPicker = (
    key: 'read_query' | 'update_query' | 'insert_query',
    opts: { required: boolean; anyLabel: string | undefined },
  ): ReactNode => {
    const val = (selTab && typeof selTab[key] === 'string' ? (selTab[key] as string) : '') || ''
    return (
      <Row gap={6} style={{ alignItems: 'center' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <SearchSelect
            value={val}
            options={tabQueryOptions}
            onChange={(v) => patchTab({ [key]: v || (opts.required ? '' : null) })}
            anyLabel={opts.anyLabel}
            placeholder={tabConnectorMeta ? t('common.pick') : t('settings.screens.editor.queries.pickConnectorFirst')}
            loading={!tabConnectorMeta}
          />
        </div>
        {val && tabEffectiveConnector && (
          <Button
            $variant="ghost"
            $size="sm"
            onClick={() => setEditQuery({ connector: tabEffectiveConnector, queryName: val })}
            title={t('settings.editQuery.edit', 'Edit query')}
          >
            <Edit3 size={13} />
          </Button>
        )}
      </Row>
    )
  }
  // Target-screen picker for nested_table — same-app screens, alpha sort. Cross-app references
  // aren't allowed at runtime (the nested TableView fetches against /api/screens/{app}/{id}).
  const sameAppScreenOptions = useMemo<SearchSelectOption[]>(() => {
    const list = (wsScreens ?? {})[app] ?? []
    return [...list]
      .filter((s) => s.id !== value.id)   // don't let an operator nest a screen into itself
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((s) => ({ value: s.id, label: s.label || s.id, mono: s.id }))
  }, [wsScreens, app, value.id])

  // ── tab mutators (called from the Tab Settings panel) ─────────────────────────────────────
  const patchTab = (patch: Row) => {
    if (selTab == null) return
    const nextTabs = tabs.slice()
    const cur = { ...nextTabs[tabIdx], ...patch }
    // Drop falsy optional keys so the saved TOML stays terse.
    for (const k of Object.keys(patch)) {
      const v = patch[k]
      if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) delete cur[k]
    }
    nextTabs[tabIdx] = cur
    setDialog({ ...(dialog ?? {}), tabs: nextTabs })
  }
  const changeTabType = (newType: 'form' | 'nested_form' | 'nested_table') => {
    if (selTab == null || tabType === newType) return
    // Seed kind-specific required fields so the validator doesn't immediately reject.
    // Common fields (id / label / l / hide_on_add / hide_on_edit / actions) survive the switch.
    const nextTabs = tabs.slice()
    const keep: Row = {
      id: selTab.id,
      ...(selTab.label != null ? { label: selTab.label } : {}),
      ...(selTab.l != null ? { l: selTab.l } : {}),
      ...(selTab.hide_on_add ? { hide_on_add: true } : {}),
      ...(selTab.hide_on_edit ? { hide_on_edit: true } : {}),
      ...(Array.isArray(selTab.actions) && (selTab.actions as unknown[]).length ? { actions: selTab.actions } : {}),
    }
    if (newType === 'form') {
      keep.type = 'form'
      keep.fields = Array.isArray(selTab.fields) ? selTab.fields : []
    } else if (newType === 'nested_form') {
      keep.type = 'nested_form'
      keep.read_query = (selTab.read_query as string | undefined) ?? ''
      keep.fields = Array.isArray(selTab.fields) ? selTab.fields : []
      if (selTab.connector) keep.connector = selTab.connector
      if (selTab.update_query) keep.update_query = selTab.update_query
      if (selTab.insert_query) keep.insert_query = selTab.insert_query
      if (Array.isArray(selTab.param_binds)) keep.param_binds = selTab.param_binds
    } else if (newType === 'nested_table') {
      keep.type = 'nested_table'
      keep.screen = (selTab.screen as string | undefined) ?? ''
      if (selTab.connector) keep.connector = selTab.connector
      if (Array.isArray(selTab.param_binds)) keep.param_binds = selTab.param_binds
    }
    nextTabs[tabIdx] = keep
    setDialog({ ...(dialog ?? {}), tabs: nextTabs })
  }
  // ParamBind editor — one row per bind. Each bind has a ``param`` (target query placeholder)
  // and either a ``value`` (literal) or a ``source`` (a column on the parent's read query).
  // For nested tabs we use ``source`` almost exclusively (binding the parent's PK / FK columns
  // into the nested query's :placeholder), so the editor surfaces a column dropdown for source
  // and a free-text input for the target param name.
  type Bind = { param?: string; value?: string; source?: string }
  const tabBinds: Bind[] = useMemo(
    () => (Array.isArray(selTab?.param_binds) ? (selTab!.param_binds as Bind[]) : []),
    [selTab],
  )
  const setTabBinds = (next: Bind[]) => {
    // patchTab drops empty arrays — so passing [] cleanly removes the param_binds key.
    patchTab({ param_binds: next as unknown as Row[] })
  }
  const updateBind = (idx: number, patch: Partial<Bind>) => {
    const next = tabBinds.slice()
    next[idx] = { ...next[idx], ...patch }
    setTabBinds(next)
  }
  const removeBind = (idx: number) => {
    const next = tabBinds.slice(); next.splice(idx, 1)
    setTabBinds(next)
  }
  const addBind = () => setTabBinds([...tabBinds, { param: '', source: '' }])
  // (``parentColumnOptions`` — the source-column picker for ParamBinds — is declared further
  // down, after the ``readColumns`` state is in scope. Out-of-order here would TDZ.)

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
  // Parent column suggestions for the Tab Settings ParamBind editor — declared HERE (not next
  // to the other tab-options memos above) because it reads from ``readColumns``, which is the
  // result of the external-data fetch above. Listing it up there would TDZ.
  const parentColumnOptions = useMemo<SearchSelectOption[]>(
    () => (readColumns ?? []).map((c) => ({ value: c.name, label: c.label || c.name, mono: c.name })),
    [readColumns],
  )
  // Target-param suggestions for the bind editor — the ``:NAME`` placeholders on the nested
  // query (the query the binds feed into). For ``nested_form`` that's the tab's own
  // ``read_query``; for ``nested_table`` it's the read query of the *target screen* (one
  // indirection further, looked up in the workspace's screen list).
  //
  // The list is a union of:
  //   - ``params`` — operator-declared placeholders (with their default + bind hints)
  //   - ``bind_params`` — every ``:NAME`` actually scanned in the SQL text (catches any
  //     placeholder the operator forgot to declare)
  //
  // Then filtered to drop ``_op``-suffixed names — those are the FilterPanel's operator
  // pickers (``equals``, ``contains``, …) wired up by the v1 migrator on filter-flagged
  // columns. They're set by the runtime TableView's filter UI, not by static param_binds, so
  // surfacing them in the bind picker just clutters it with confusing duplicates.
  //
  // Sorted alpha. ``allowCustom`` on the picker keeps the free-text escape hatch for any
  // placeholder we couldn't surface (e.g. one buried inside a dialect-specific SQL variant
  // the workspace hasn't loaded).
  const nestedTargetParams = useMemo<SearchSelectOption[]>(() => {
    if (tabType !== 'nested_form' && tabType !== 'nested_table') return []
    let nestedReadQuery: string | undefined
    let nestedConnector: string | undefined
    if (tabType === 'nested_form') {
      nestedReadQuery = selTab?.read_query as string | undefined
      nestedConnector = (selTab?.connector as string | undefined) || connector
    } else {
      const targetScreenId = selTab?.screen as string | undefined
      if (!targetScreenId) return []
      // Look in the parent screen's app first, then fall back to scanning every app — the
      // target screen could legitimately live elsewhere (operator pointing the nested_table
      // at a cross-app screen, e.g. NOMAJDE pointing into a jdedwards screen). The same-app
      // picker upstream filters strictly, but for the *binds* we want to resolve wherever the
      // target actually sits.
      const sameApp = (wsScreens ?? {})[app] ?? []
      let target = sameApp.find((s) => s.id === targetScreenId)
      if (!target) {
        for (const [otherApp, list] of Object.entries(wsScreens ?? {})) {
          if (otherApp === app) continue
          const hit = list.find((s) => s.id === targetScreenId)
          if (hit) { target = hit; break }
        }
      }
      if (!target) return []
      nestedReadQuery = target.read_query
      // The bind sends to the read query *on the target screen's effective connector* —
      // honouring an explicit tab override first (rare but valid), else the target screen's.
      nestedConnector = (selTab?.connector as string | undefined) || target.connector
    }
    if (!nestedReadQuery || !nestedConnector) return []
    const meta = (wsConnectors ?? []).find((c) => c.name === nestedConnector)
    if (!meta || meta.type !== 'sql') return []
    const q = meta.queries.find((qq) => qq.name === nestedReadQuery)
    if (!q) return []
    const names = new Set<string>()
    for (const p of q.params ?? []) names.add(p.name)
    for (const b of q.bind_params ?? []) names.add(b)
    // Drop the FilterPanel's operator-picker placeholders — they're paired with each
    // filter-flagged column as ``:COL`` + ``:COL_op``, set by the TableView's per-column
    // operator combobox at runtime, never by a static param_bind.
    return [...names]
      .filter((n) => !n.endsWith('_op'))
      .sort()
      .map((n) => ({ value: n, label: n, mono: n }))
  }, [tabType, selTab, wsScreens, wsConnectors, connector, app])
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
  const deleteField = useCallback(async (idx: number) => {
    const ok = await modals.confirm({
      title: t('settings.screens.field.delete'),
      message: t('settings.screens.field.confirmDelete', { name: fields[idx]?.name }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const next = fields.slice(); next.splice(idx, 1)
    updateFields(next)
    setSelFieldIdx((cur) => (cur === idx ? null : cur != null && cur > idx ? cur - 1 : cur))
  }, [fields, updateFields, t, modals])
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
  // Pick subsets of ScreenField so each SchemaForm renders just the section it owns. **Split
  // Basic (always visible) from Advanced (collapsible)** so the inspector isn't a wall of every
  // property — operators rarely set colspan / default / per-field flags but always set
  // ``dd`` / ``label`` / ``required``.
  const fieldDef = useMemo<JsonSchema>(() => ({ ...((defs.ScreenField as JsonSchema | undefined) ?? { type: 'object' }), $defs: defs }), [defs])
  const basicPropsSchema = useMemo<JsonSchema>(() => pickSchemaProperties(fieldDef, FIELD_BASIC_KEYS as unknown as string[]), [fieldDef])
  const advancedPropsSchema = useMemo<JsonSchema>(() => pickSchemaProperties(fieldDef, FIELD_ADVANCED_KEYS as unknown as string[]), [fieldDef])
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
  const addTab = async () => {
    const existing = tabs.map((tt) => tt.id)
    const id = (await modals.prompt({
      title: t('settings.screens.tab.add'),
      message: t('settings.screens.tab.namePrompt'),
      validate: (v) => {
        if (!v) return null   // empty → close as if cancelled
        if (existing.includes(v)) return t('settings.screens.tab.idExists', { id: v })
        return null
      },
    }))?.trim()
    if (!id) return
    const next = [...tabs, { id, fields: [] }]
    setDialog({ ...(dialog ?? {}), tabs: next })
    setTabIdx(next.length - 1)
  }
  const createDialog = () => {
    setDialog({ tabs: [{ id: 'general', label: 'General', fields: [] }] })
    setTabIdx(0)
  }

  // ── Tab Settings panel render ─────────────────────────────────────────────────────────────
  // Sits at the top of the canvas body, above the field grid. Collapsible <details> — opens by
  // default for nested kinds (where the operator NEEDS to see the queries / target screen to
  // make sense of the tab); collapsed by default for plain FormTab (where there's nothing
  // critical inside — just label / cols / hide-on-*).
  const renderTabSettings = (): React.ReactNode => {
    if (!selTab) return null
    const isNested = tabType !== 'form'
    // Inconsistency check: if any field's colspan exceeds the tab's cols, the layout falls
    // back to browser implicit-grid behaviour at render time (unpredictable). Surface a
    // warning above the cols input so the operator notices + bumps cols up.
    const maxColspan = (() => {
      const list = Array.isArray(selTab.fields) ? (selTab.fields as Row[]) : []
      let m = 1
      for (const f of list) {
        const cs = Number(f.colspan ?? 1)
        if (cs > m) m = cs
      }
      return m
    })()
    const colsInconsistent = tabType !== 'nested_table' && cols < maxColspan
    return (
      <TabSettingsBox open={isNested || colsInconsistent}>
        <summary>
          <ChevronRightIcon size={12} />
          <span>{t('settings.screens.visual.tabSettings.title')}</span>
          {colsInconsistent && (
            <span className="kind" style={{ color: colors.orange.main, borderColor: colors.orange.border, background: colors.orange.bg }}>
              <AlertTriangle size={10} style={{ verticalAlign: 'middle', marginRight: 3 }} />
              {t('settings.screens.visual.tabSettings.colsWarningChip')}
            </span>
          )}
          <span className="kind">{tabType}</span>
        </summary>
        <div>
          {colsInconsistent && (
            <Banner $tone="info">
              {t('settings.screens.visual.tabSettings.colsWarning', { cols, max: maxColspan })}
            </Banner>
          )}
          {/* Common fields — label, type picker, plus ``cols`` (the CSS grid column count for
              the tab's field layout, v1's ``tab_cols``). Only meaningful for form / nested_form;
              hidden for nested_table since that variant has no own fields. */}
          <Row gap={10}>
            <div style={{ flex: 2 }}>
              <Field label={t('settings.screens.visual.tabSettings.label')}>
                <Input
                  value={(selTab.label as string | undefined) ?? ''}
                  onChange={(e) => patchTab({ label: e.target.value })}
                  placeholder={String(selTab.id)}
                />
              </Field>
            </div>
            <div style={{ flex: 2 }}>
              <Field label={t('settings.screens.visual.tabSettings.type')}>
                <SearchSelect
                  value={tabType}
                  options={[
                    { value: 'form', label: t('settings.screens.visual.tabSettings.typeForm'), mono: 'form' },
                    { value: 'nested_form', label: t('settings.screens.visual.tabSettings.typeNestedForm'), mono: 'nested_form' },
                    { value: 'nested_table', label: t('settings.screens.visual.tabSettings.typeNestedTable'), mono: 'nested_table' },
                  ]}
                  onChange={(v) => v && changeTabType(v as 'form' | 'nested_form' | 'nested_table')}
                />
              </Field>
            </div>
            {tabType !== 'nested_table' && (
              <div style={{ flex: 1 }}>
                <Field label={t('settings.screens.visual.tabSettings.cols')}>
                  <Input
                    type="number"
                    min={1}
                    max={6}
                    value={String(selTab.cols ?? '')}
                    onChange={(e) => {
                      const raw = e.target.value.trim()
                      // Empty / 0 = clear the override (the canvas falls back to its 2-column
                      // default). Otherwise coerce to an int 1..6 — anything wider gets cramped
                      // on the canvas.
                      if (raw === '' || raw === '0') patchTab({ cols: null })
                      else patchTab({ cols: Math.max(1, Math.min(6, Number(raw))) })
                    }}
                    placeholder="2"
                  />
                </Field>
              </div>
            )}
          </Row>
          {/* nested_form: connector + read/update/insert query pickers + ParamBinds. */}
          {tabType === 'nested_form' && (
            <>
              <Row gap={10}>
                <div style={{ flex: 1 }}>
                  <Field label={t('settings.screens.visual.tabSettings.connector')}>
                    <SearchSelect
                      value={(selTab.connector as string | undefined) ?? ''}
                      options={sqlConnectorOptions}
                      onChange={(v) => patchTab({ connector: v && v !== connector ? v : null })}
                      anyLabel={t('settings.screens.editor.connectorUseApp', { app: connector })}
                      placeholder={connector}
                    />
                  </Field>
                </div>
                <div style={{ flex: 1 }}>
                  <Field label={`${t('settings.screens.editor.queries.read_query')} *`}>
                    {renderTabQueryPicker('read_query', { required: true, anyLabel: undefined })}
                  </Field>
                </div>
              </Row>
              <Row gap={10}>
                <div style={{ flex: 1 }}>
                  <Field label={t('settings.screens.editor.queries.update_query')}>
                    {renderTabQueryPicker('update_query', { required: false, anyLabel: t('common.none') })}
                  </Field>
                </div>
                <div style={{ flex: 1 }}>
                  <Field label={t('settings.screens.editor.queries.insert_query')}>
                    {renderTabQueryPicker('insert_query', { required: false, anyLabel: t('common.none') })}
                  </Field>
                </div>
              </Row>
              {renderTabBindsEditor()}
            </>
          )}
          {/* nested_table: target screen + connector + ParamBinds. */}
          {tabType === 'nested_table' && (
            <>
              <Row gap={10}>
                <div style={{ flex: 1 }}>
                  <Field label={`${t('settings.screens.visual.tabSettings.targetScreen')} *`}>
                    <SearchSelect
                      value={(selTab.screen as string | undefined) ?? ''}
                      options={sameAppScreenOptions}
                      onChange={(v) => patchTab({ screen: v || '' })}
                      placeholder={t('common.pick')}
                    />
                  </Field>
                </div>
                <div style={{ flex: 1 }}>
                  <Field label={t('settings.screens.visual.tabSettings.connector')}>
                    <SearchSelect
                      value={(selTab.connector as string | undefined) ?? ''}
                      options={sqlConnectorOptions}
                      onChange={(v) => patchTab({ connector: v && v !== connector ? v : null })}
                      anyLabel={t('settings.screens.editor.connectorUseApp', { app: connector })}
                      placeholder={connector}
                    />
                  </Field>
                </div>
              </Row>
              {renderTabBindsEditor()}
            </>
          )}
        </div>
      </TabSettingsBox>
    )
  }
  // ParamBinds editor for nested tabs — one row per bind: source-column dropdown + → arrow +
  // target-param free-text + remove button. The source dropdown is filled from the parent
  // screen's read-query result columns; the target param is the placeholder name on the nested
  // query (free-text since we can't introspect the nested query's :placeholders without parsing
  // its SQL — operator knows them from the migrated query).
  function renderTabBindsEditor(): React.ReactNode {
    return (
      <Field label={t('settings.screens.visual.tabSettings.paramBinds')}>
        <BindList>
          {tabBinds.length === 0 && (
            <div style={{ color: colors.text.muted, fontSize: fontSize.micro, padding: '4px 2px' }}>
              {t('settings.screens.visual.tabSettings.bindsEmpty')}
            </div>
          )}
          {tabBinds.map((b, i) => (
            <BindRow key={i}>
              <SearchSelect
                value={b.source ?? ''}
                options={parentColumnOptions}
                onChange={(v) => updateBind(i, { source: v, value: undefined })}
                allowCustom
                placeholder={t('settings.screens.visual.tabSettings.bindSourcePlaceholder')}
              />
              <span className="arrow">→</span>
              {/* Target ``:NAME`` placeholder on the nested query — picker filled from the
                  query's declared params + scanned bind_params (see ``nestedTargetParams``).
                  ``allowCustom`` keeps the free-text escape hatch for placeholders we couldn't
                  surface (e.g. one buried inside a dialect-specific SQL variant). */}
              <SearchSelect
                value={b.param ?? ''}
                options={nestedTargetParams}
                onChange={(v) => updateBind(i, { param: v })}
                allowCustom
                placeholder={t('settings.screens.visual.tabSettings.bindTargetPlaceholder')}
              />
              <RemoveBtn type="button" onClick={() => removeBind(i)} title={t('common.remove')}>
                <Trash2 size={11} />
              </RemoveBtn>
            </BindRow>
          ))}
          <Button $variant="ghost" $size="sm" onClick={addBind} style={{ alignSelf: 'flex-start', marginTop: 4 }}>
            <Plus size={12} /> {t('settings.screens.visual.tabSettings.bindAdd')}
          </Button>
        </BindList>
      </Field>
    )
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
          {selTab ? (() => {
            // Split fields into visible vs hidden so the canvas doesn't drown in the 20+ hidden
            // columns the v1 migrator carries through (every column the read query returns has to
            // sit somewhere in the dialog — otherwise the migrated _put/_post queries' :NAME
            // binds resolve to NULL on save, nulling out columns the operator never touched).
            // Hidden fields stay in the data model (no behaviour change), they just visually
            // group into a collapsible block at the bottom of the canvas.
            // ``hidden`` / ``disabled`` / ``required`` are inherited from the column unless the
            // field explicitly overrides (bool | null). Compute the effective value here so the
            // canvas / badges reflect what the operator will see at runtime.
            const effective = (f: Row, key: 'hidden' | 'disabled' | 'required'): boolean => {
              const own = f[key]
              if (own === true || own === false) return own
              const col = colFor(f.name)
              return col?.[key] === true
            }
            const visibleFields: { f: Row; idx: number }[] = []
            const hiddenFields: { f: Row; idx: number }[] = []
            fields.forEach((f, idx) => {
              if (effective(f, 'hidden')) hiddenFields.push({ f, idx })
              else visibleFields.push({ f, idx })
            })
            // Per-field card renderer — factored so the visible group + hidden group reuse the
            // same chrome (selection, drag, badges, previews). ``idx`` is the field's index in
            // the original ``fields`` array; drag/select/delete all key off that — moving a
            // field between groups means flipping its ``hidden`` flag in the inspector, not
            // moving it inside the array.
            const renderCard = ({ f, idx: i }: { f: Row; idx: number }) => {
              const name = String(f.name ?? '')
              const column = colFor(name)
              // Display metadata (dd / label / format / lookup binds) lives on the column.
              const ddId = (column?.dd as string | undefined) || name
              const dd = ddEntries?.get(ddId) ?? null
              const preview = previewFor(f, dd, column)
              const label = (column?.label as string | undefined) ?? dd?.label ?? name
              const span = Math.max(1, Number(f.colspan ?? 1))
              // Effective flags: field's explicit override wins; otherwise inherit the column.
              const isHidden = effective(f, 'hidden')
              const isDisabled = effective(f, 'disabled')
              const isRequired = effective(f, 'required')
              const Icon = preview.Icon
              return (
                <Card
                  key={`${name}_${i}`}
                  $selected={selFieldIdx === i}
                  $hidden={isHidden}
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
                    {isRequired && <span style={{ color: colors.orange.main }}>*</span>}
                  </div>
                  <div className="preview"><Icon size={11} /> {preview.sample}</div>
                  <div className="badges">
                    <span className="name">{name}</span>
                    {column?.dd != null && column.dd !== '' && column.dd !== name && <Badge>dd:{String(column.dd)}</Badge>}
                    {/* "key" badge — the column is part of the row's primary key (drives the
                        Excel-import match + edit-mode lock). Surfaces here so the operator
                        sees at-a-glance which columns are keys without opening each one. */}
                    {Boolean(column?.key) && <Badge $tone="green">{t('settings.screens.field.key', 'key')}</Badge>}
                    {isHidden && <Badge $tone="muted"><EyeOff size={10} style={{ verticalAlign: 'middle' }} /> {t('settings.screens.field.hidden')}</Badge>}
                    {isDisabled && <Badge $tone="muted"><Lock size={10} style={{ verticalAlign: 'middle' }} /> {t('settings.screens.field.disabled')}</Badge>}
                    {(Array.isArray(f.visible_when) && (f.visible_when as unknown[]).length > 0)
                      || (Array.isArray(f.required_when) && (f.required_when as unknown[]).length > 0)
                      || (Array.isArray(f.disabled_when) && (f.disabled_when as unknown[]).length > 0)
                      ? <Badge $tone="orange"><Filter size={10} style={{ verticalAlign: 'middle' }} /> {t('settings.screens.field.conditional')}</Badge>
                      : null}
                    {Array.isArray(column?.lookup_param_binds) && (column!.lookup_param_binds as unknown[]).length > 0 && (
                      <Badge $tone="green">{t('settings.screens.field.binds', { count: (column!.lookup_param_binds as unknown[]).length })}</Badge>
                    )}
                  </div>
                </Card>
              )
            }
            return (
              <>
                {/* Tab Settings panel — collapsible, expanded by default for nested kinds so
                    the operator sees the queries / target screen at a glance. v1's settings_
                    applications JDE tab is the canonical example: a nested_form bound to the
                    parent's APPS_ID via param_binds — previously invisible in the visual
                    builder, now front-and-centre. */}
                {renderTabSettings()}
                {/* Field grid — only meaningful for form and nested_form tabs (a nested_table
                    has no own fields; its target screen carries them). For nested_table we
                    just show a hint pointing at the Tab Settings panel above. */}
                {tabType !== 'nested_table' && (
                  <FieldGrid $cols={cols}>
                    {visibleFields.map(renderCard)}
                    <AddSlot onClick={async () => {
                      const name = (await modals.prompt({
                        title: t('settings.screens.field.add'),
                        message: t('settings.screens.field.namePrompt'),
                      }))?.trim()
                      if (name) addFieldFromName(name)
                    }}>
                      <Plus size={13} /> {t('settings.screens.field.add')}
                    </AddSlot>
                  </FieldGrid>
                )}
                {tabType === 'nested_table' && (
                  <Empty>{t('settings.screens.visual.tabSettings.nestedTableNoFields')}</Empty>
                )}
                {/* Hidden fields — collapsible group at the bottom. Migrated v1 screens often
                    have 20+ hidden columns kept around so the _put / _post queries' :NAME binds
                    don't null out untouched columns; this lets the operator see them without
                    flooding the visible canvas. Click a card to edit; in the inspector flip
                    ``hidden`` off to promote it into the visible group above. */}
                {tabType !== 'nested_table' && hiddenFields.length > 0 && (
                  <HiddenGroup>
                    <summary>
                      <ChevronRightIcon size={12} />
                      <EyeOff size={12} />
                      {t('settings.screens.visual.canvas.hidden', { count: hiddenFields.length })}
                    </summary>
                    <FieldGrid $cols={cols} style={{ marginTop: 10 }}>
                      {hiddenFields.map(renderCard)}
                    </FieldGrid>
                    <div style={{ color: colors.text.muted, fontSize: fontSize.micro, lineHeight: 1.4, marginTop: 8 }}>
                      {t('settings.screens.visual.canvas.hiddenHint')}
                    </div>
                  </HiddenGroup>
                )}
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
            )
          })() : (
            <Empty>{t('settings.screens.tab.pickOne')}</Empty>
          )}
        </CanvasBody>
      </Col>

      {/* ─── INSPECTOR (right) ─── */}
      <Col>
        <ColTitle>{t('settings.screens.visual.inspector.title')}</ColTitle>
        {/* Breadcrumb: "Dialog ▸ <field>" — clicking "Dialog" deselects so the dialog-level
            view comes back (title editor + events). Always rendered; "Dialog" is muted when
            already there. The breadcrumb sits ABOVE the scroll body so it doesn't disappear
            when the operator scrolls a long inspector. */}
        <Crumbs>
          <CrumbBtn type="button" $active={!selField} onClick={() => setSelFieldIdx(null)}>
            {t('settings.screens.visual.dialog.title')}
          </CrumbBtn>
          {selField && (
            <>
              <ChevronRightIcon size={11} />
              <CrumbBtn type="button" $active>{String(selField.name ?? '')}</CrumbBtn>
            </>
          )}
        </Crumbs>
        <InspBody>
        {selField ? (
          <>
            {/* Basic — always visible, the fields operators set on every field. */}
            <InspSection>
              <InspTitle><FileText size={13} /> {String(selField.name ?? '')}</InspTitle>
              <SchemaForm
                schema={basicPropsSchema}
                defs={defs}
                value={selField}
                onChange={(v) => {
                  const patch: Row = {}
                  for (const k of FIELD_BASIC_KEYS) patch[k] = v[k]
                  updateField(selFieldIdx!, patch)
                }}
              />
              {/* Override toggle: required on this dialog. Inherits the column's grid-level
                  default unless the operator explicitly sets it. */}
              <OverrideToggle
                label={t('settings.screens.field.required', 'Required')}
                value={selField.required as boolean | null | undefined}
                inherited={Boolean(colFor(selField.name)?.required)}
                onChange={(v) => updateField(selFieldIdx!, { required: v })}
                inheritedYesLabel={t('settings.screens.visual.override.required', 'required')}
                inheritedNoLabel={t('settings.screens.visual.override.optional', 'optional')}
              />
            </InspSection>
            {/* Advanced — collapsible. Display flags + cascading lookup binds + per-field
                conditional rules. Closed by default so the inspector isn't a wall of toggles. */}
            <AdvDetails>
              <summary>
                <ChevronRightIcon size={12} />
                {t('settings.screens.visual.inspector.advanced')}
              </summary>
              <div>
                <OverrideToggle
                  label={t('settings.screens.field.hidden', 'Hidden')}
                  value={selField.hidden as boolean | null | undefined}
                  inherited={Boolean(colFor(selField.name)?.hidden)}
                  onChange={(v) => updateField(selFieldIdx!, { hidden: v })}
                  inheritedYesLabel={t('settings.screens.visual.override.hidden', 'hidden')}
                  inheritedNoLabel={t('settings.screens.visual.override.shown', 'shown')}
                />
                <OverrideToggle
                  label={t('settings.screens.field.disabled', 'Read-only')}
                  value={selField.disabled as boolean | null | undefined}
                  inherited={Boolean(colFor(selField.name)?.disabled)}
                  onChange={(v) => updateField(selFieldIdx!, { disabled: v })}
                  inheritedYesLabel={t('settings.screens.visual.override.readOnly', 'read-only')}
                  inheritedNoLabel={t('settings.screens.visual.override.editable', 'editable')}
                />
                <SchemaForm
                  schema={advancedPropsSchema}
                  defs={defs}
                  value={selField}
                  onChange={(v) => {
                    const patch: Row = {}
                    for (const k of FIELD_ADVANCED_KEYS) patch[k] = v[k]
                    updateField(selFieldIdx!, patch)
                  }}
                />
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
              </div>
            </AdvDetails>
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
        </InspBody>
      </Col>
      {/* Edit query — raised from a nested-form tab's pencil button. Self-contained: fetches
          its own connectors copy + PUTs it back on Save (the visual builder doesn't track
          query edits itself). */}
      {editQuery && (
        <EditQueryModal
          connector={editQuery.connector}
          queryName={editQuery.queryName}
          onClose={() => setEditQuery(null)}
        />
      )}
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
