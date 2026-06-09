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
  AlertTriangle, AlignLeft, Calendar, CheckSquare, ChevronLeft, ChevronRight as ChevronRightIcon, Code2,
  Edit3, Eye, EyeOff, FileText, Filter, Hash, Key, Layers, List, Lock, PanelLeftOpen, PanelRightOpen,
  Plus, Search, Trash2, Zap,
  type LucideIcon,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Field, Input, Row, SchemaForm, SearchSelect, Stack, useModals, type JsonSchema, type SearchSelectOption } from '../../common'
import type { ConnectorsDoc, DictionaryDoc } from '../../types/config'
import type { Column, QueryResult } from '../../types/connectors'
import type { ScreenDetail } from '../../types/screens'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { EditQueryModal } from './EditQueryModal'
import { colors, fontSize, fonts, radius } from '../../theme'
import { pickSchemaProperties } from './connectorTables'
import ActionTreeView from './ActionTreeView'
import EmbeddedFormsEditor from './EmbeddedFormsEditor'
import { breadcrumbCrumbs, type ActionPath } from './actionPath'

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
const FIELD_ADVANCED_KEYS = ['colspan', 'default', 'format'] as const
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
//
// Layout: flex row with three Cols. The left + right Cols carry a width set from state +
// localStorage so the operator can drag a Splitter between them to resize. Either side
// panel can collapse to a thin CollapsedRail (vertical strip with an arrow button to
// re-expand) so the canvas gets the full viewport when the side panel isn't needed.
const Shell = styled.div`
  display: flex; flex-direction: row; gap: 12px;
  flex: 1 1 auto; min-height: 0; height: 100%; min-width: 0;
`
const Col = styled.div`
  display: flex; flex-direction: column; gap: 10px; min-width: 0; min-height: 0;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.card}; padding: 12px;
  overflow: hidden; flex-shrink: 0;
`
// Drag handle between two panels. 6 px wide hit-target with a centred 2-px visual bar that
// highlights on hover / while dragging. ``pointerEvents: auto`` keeps it clickable even when
// the operator's mouse is over a busy panel.
const Splitter = styled.div<{ $active?: boolean }>`
  flex: 0 0 6px; cursor: col-resize; position: relative; align-self: stretch;
  &::before {
    content: ''; position: absolute; top: 0; bottom: 0; left: 2px; width: 2px;
    background: ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
    border-radius: 1px; transition: background 80ms ease;
  }
  &:hover::before { background: ${colors.blue.border}; }
`
// Collapsed-panel rail. Narrow vertical strip with an arrow button that expands the panel
// back. We keep it the same width on both sides for visual symmetry; the rail's icon
// rotates so the arrow always points "outward" (toward the panel that would open).
const CollapsedRail = styled.button`
  flex: 0 0 24px; align-self: stretch; padding: 6px 0;
  background: ${colors.bg.card}; border: 1px solid ${colors.border}; border-radius: ${radius.md};
  color: ${colors.text.muted}; cursor: pointer;
  display: flex; flex-direction: column; align-items: center; justify-content: flex-start;
  gap: 8px;
  &:hover { color: ${colors.blue.main}; border-color: ${colors.blue.border}; }
`
const CollapseBtn = styled.button`
  background: transparent; border: none; padding: 2px; cursor: pointer; color: ${colors.text.muted};
  display: inline-flex; align-items: center; justify-content: center;
  &:hover { color: ${colors.blue.main}; }
`
const ColTitle = styled.div`
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; color: ${colors.text.muted};
  text-transform: uppercase; letter-spacing: 0.04em;
`
// Section heading + hint inside the Tab Settings panel (e.g. the Embedded forms block).
const SubHead = styled.strong`display: block; color: ${colors.text.primary}; font-size: ${fontSize.sm}; margin-top: 4px;`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro}; line-height: 1.4; margin: 2px 0 8px;`
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

// localStorage keys for the panel-layout preferences (one set across every screen — the
// operator's preferred ratio rarely depends on which screen is open). Tolerate a missing /
// corrupt entry by falling back to the design defaults.
const LS_LEFT_WIDTH = 'screenDesigner.leftWidth'
const LS_RIGHT_WIDTH = 'screenDesigner.rightWidth'
const LS_LEFT_COLL = 'screenDesigner.leftCollapsed'
const LS_RIGHT_COLL = 'screenDesigner.rightCollapsed'
const PANEL_MIN = 180   // can't shrink narrower than this — title + icon need room
const PANEL_MAX = 640   // and not wider — otherwise the canvas gets squeezed
const readInt = (key: string, fallback: number): number => {
  try { const v = Number(window.localStorage.getItem(key)); return Number.isFinite(v) && v > 0 ? v : fallback }
  catch { return fallback }
}
const readBool = (key: string): boolean => {
  try { return window.localStorage.getItem(key) === '1' } catch { return false }
}


export default function ScreenVisualBuilder({ app, value, schema, onChange }: ScreenVisualBuilderProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const defs = (schema.$defs ?? {}) as Record<string, JsonSchema>

  // ── panel layout state ──────────────────────────────────────────────────────────────
  // Three flexible columns: palette (left), canvas (center, grows), inspector (right).
  // The two side panels carry an explicit pixel width (drag-resizable + persisted) and a
  // collapsed bool (toggle a button to hide → canvas takes the whole space; a thin rail
  // remains so the operator can re-expand). Defaults match the original grid layout.
  const [leftWidth, setLeftWidth] = useState(() => readInt(LS_LEFT_WIDTH, 240))
  const [rightWidth, setRightWidth] = useState(() => readInt(LS_RIGHT_WIDTH, 340))
  const [leftCollapsed, setLeftCollapsed] = useState(readBool(LS_LEFT_COLL))
  const [rightCollapsed, setRightCollapsed] = useState(readBool(LS_RIGHT_COLL))
  useEffect(() => { try { window.localStorage.setItem(LS_LEFT_WIDTH, String(leftWidth)) } catch { /* ignore */ } }, [leftWidth])
  useEffect(() => { try { window.localStorage.setItem(LS_RIGHT_WIDTH, String(rightWidth)) } catch { /* ignore */ } }, [rightWidth])
  useEffect(() => { try { window.localStorage.setItem(LS_LEFT_COLL, leftCollapsed ? '1' : '0') } catch { /* ignore */ } }, [leftCollapsed])
  useEffect(() => { try { window.localStorage.setItem(LS_RIGHT_COLL, rightCollapsed ? '1' : '0') } catch { /* ignore */ } }, [rightCollapsed])
  // Splitter drag — captures the pointer on mousedown so the operator can drag across the
  // whole window without losing the gesture. ``which`` says which side's width we're
  // updating (left or right); ``startX`` + ``startW`` are the snapshot at drag start so we
  // compute new = startW ± (clientX - startX). The right side subtracts because moving
  // right shrinks the right panel.
  const [draggingSide, setDraggingSide] = useState<'left' | 'right' | null>(null)
  const startDrag = (side: 'left' | 'right') => (e: React.PointerEvent) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = side === 'left' ? leftWidth : rightWidth
    setDraggingSide(side)
    const onMove = (ev: PointerEvent) => {
      const dx = ev.clientX - startX
      const next = side === 'left' ? startW + dx : startW - dx
      const clamped = Math.max(PANEL_MIN, Math.min(PANEL_MAX, next))
      if (side === 'left') setLeftWidth(clamped)
      else setRightWidth(clamped)
    }
    const onUp = () => {
      setDraggingSide(null)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onUp)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onUp)
  }

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
  // Theme A polish — Inspector hosts the per-tab action editor. When this path is non-null,
  // the Inspector renders ``ActionTreeView`` in editor mode (focused on the action at the
  // path); the Tab Settings panel keeps a flat *list* of actions with the matching row
  // highlighted. Selecting an action clears any field selection (and vice versa) — the
  // Inspector shows one thing at a time.
  const [selActionPath, setSelActionPath] = useState<ActionPath | null>(null)
  // Reset both selections when the operator switches tabs — the per-tab actions live on a
  // specific tab and a stale path would reach into the new tab's (different) actions list.
  useEffect(() => { setSelFieldIdx(null); setSelActionPath(null) }, [tabIdx])
  // Mutual-exclusion helpers — clicking a field clears the action selection; clicking an
  // action clears the field selection. Either way the Inspector switches modes.
  const selectField = useCallback((idx: number | null) => {
    setSelFieldIdx(idx)
    if (idx != null) setSelActionPath(null)
  }, [])
  const selectActionPath = useCallback((next: ActionPath | null) => {
    setSelActionPath(next)
    if (next != null) setSelFieldIdx(null)
  }, [])
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
  // A nested_form is in REFERENCE mode when it carries ``form_screen`` (reuse an existing screen's
  // form). In that mode its queries + fields are inherited, so the inline query pickers and the
  // field canvas are hidden — the picker itself is the mode switch (pick a screen → reference;
  // clear it → inline). ``tabHasOwnFields`` gates everything that edits this tab's OWN field grid.
  const nestedFormIsRef = tabType === 'nested_form' && typeof selTab?.form_screen === 'string' && !!selTab.form_screen
  const tabHasOwnFields = tabType === 'form' || (tabType === 'nested_form' && !nestedFormIsRef)

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
    key: 'read_query' | 'update_query' | 'insert_query' | 'delete_query',
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
      // Carry a reference (form_screen) across when present — e.g. a nested_table's ``screen``
      // becomes the reference-mode form_screen, so switching table↔form keeps the chosen screen.
      const ref = (selTab.form_screen ?? selTab.screen) as string | undefined
      if (ref) keep.form_screen = ref
      else keep.read_query = (selTab.read_query as string | undefined) ?? ''
      keep.fields = Array.isArray(selTab.fields) ? selTab.fields : []
      if (selTab.connector) keep.connector = selTab.connector
      if (selTab.update_query) keep.update_query = selTab.update_query
      if (selTab.insert_query) keep.insert_query = selTab.insert_query
      if (selTab.delete_query) keep.delete_query = selTab.delete_query
      if (Array.isArray(selTab.param_binds)) keep.param_binds = selTab.param_binds
    } else if (newType === 'nested_table') {
      keep.type = 'nested_table'
      keep.screen = (selTab.screen as string | undefined) ?? (selTab.form_screen as string | undefined) ?? ''
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
  // An inline nested_form tab has its OWN read query → the palette's Columns tab must offer THAT
  // query's columns (not the parent screen's). Fetched separately when such a tab is selected.
  const [nestedTabColumns, setNestedTabColumns] = useState<Column[] | null>(null)
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

  // Columns for an INLINE nested_form tab's own read query — powers the palette's Columns tab when
  // such a tab is selected (so the operator picks the nested query's columns, not the parent's).
  // Reference-mode nested_forms inherit fields from the referenced screen, so they need no palette.
  const nestedFormReadQuery = (tabType === 'nested_form' && !selTab?.form_screen && typeof selTab?.read_query === 'string')
    ? (selTab.read_query as string) : ''
  const nestedFormConnector = (typeof selTab?.connector === 'string' && selTab.connector.trim()
    ? (selTab.connector as string) : connector)
  useEffect(() => {
    setNestedTabColumns(null)
    if (!nestedFormReadQuery || !nestedFormConnector) return
    let cancelled = false
    api.get<QueryResult>(
      `/api/sql/${encodeURIComponent(nestedFormConnector)}/${encodeURIComponent(nestedFormReadQuery)}?_limit=0`,
    ).then((r) => { if (!cancelled) setNestedTabColumns(r.columns) })
      .catch(() => { /* silent — the Columns tab just stays empty for this nested form */ })
    return () => { cancelled = true }
  }, [nestedFormConnector, nestedFormReadQuery])

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
  // The referenced screen's columns for a nested_table (or reference-mode nested_form) — lazy
  // fetched so the param-binding TARGET picker can offer them. Sourcing from the screen's columns
  // (not the query's declared :params) means no per-query param setup — the bind value narrows the
  // nested read at runtime. Best-effort + reset per target.
  const [nestedTargetScreenColumns, setNestedTargetScreenColumns] = useState<string[]>([])
  useEffect(() => {
    const targetId = tabType === 'nested_table'
      ? (selTab?.screen as string | undefined)
      : (tabType === 'nested_form' ? (selTab?.form_screen as string | undefined) : undefined)
    setNestedTargetScreenColumns([])
    if (!targetId) return
    let cancelled = false
    api.get<ScreenDetail>(`/api/screens/${encodeURIComponent(app)}/${encodeURIComponent(targetId)}`)
      .then((s) => { if (!cancelled) setNestedTargetScreenColumns((s.columns ?? []).map((c) => c.name.toUpperCase())) })
      .catch(() => { /* silent — the picker falls back to the query's declared params */ })
    return () => { cancelled = true }
  }, [tabType, selTab?.screen, selTab?.form_screen, app])
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
    if (tabType === 'nested_form' && !selTab?.form_screen) {
      // Inline nested_form — binds feed the tab's OWN read_query.
      nestedReadQuery = selTab?.read_query as string | undefined
      nestedConnector = (selTab?.connector as string | undefined) || connector
    } else {
      // nested_table (``screen``) OR reference-mode nested_form (``form_screen``) — binds feed the
      // read query of the referenced screen, resolved in the parent app first then anywhere.
      const targetScreenId = (tabType === 'nested_form'
        ? selTab?.form_screen
        : selTab?.screen) as string | undefined
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
    const names = new Set<string>()
    const meta = nestedConnector ? (wsConnectors ?? []).find((c) => c.name === nestedConnector) : undefined
    const q = (meta && meta.type === 'sql' && nestedReadQuery)
      ? meta.queries.find((qq) => qq.name === nestedReadQuery)
      : undefined
    for (const p of q?.params ?? []) names.add(p.name)
    for (const b of q?.bind_params ?? []) names.add(b)
    // ALSO offer the target SCREEN's columns — for a nested_table (or reference-mode nested_form)
    // the bind target is a column on the referenced screen, and its read query often declares no
    // ``:params`` (it narrows via the passed bind value). Sourcing from the screen's columns means
    // no per-query param setup. (Inline nested_forms have no screen, so they rely on the query's
    // params above — same trade-off the operator already understands.)
    for (const c of nestedTargetScreenColumns) names.add(c)
    // Drop the FilterPanel's operator-picker placeholders — they're paired with each
    // filter-flagged column as ``:COL`` + ``:COL_op``, set by the TableView's per-column
    // operator combobox at runtime, never by a static param_bind.
    return [...names]
      .filter((n) => !n.endsWith('_op'))
      .sort()
      .map((n) => ({ value: n, label: n, mono: n }))
  }, [tabType, selTab, wsScreens, wsConnectors, connector, app, nestedTargetScreenColumns])
  const updateFields = useCallback((nextFields: Row[]) => updateTab(tabIdx, { fields: nextFields }), [tabIdx, updateTab])
  const addFieldFromName = useCallback((fieldName: string, ddId?: string | null) => {
    if (!fieldName) return
    if (fields.some((f) => f.name === fieldName)) {
      // duplicate — just select the existing one rather than silently doing nothing.
      const ex = fields.findIndex((f) => f.name === fieldName)
      selectField(ex)
      return
    }
    const entry: Row = { name: fieldName }
    if (ddId && ddId !== fieldName) entry.dd = ddId
    const nextFields = [...fields, entry]
    updateFields(nextFields)
    selectField(nextFields.length - 1)
  }, [fields, updateFields, selectField])
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
    selectField(idx)
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
    setTabIdx(targetTabIdx); selectField(targetFields.length); setDragIdx(null)
  }

  // ── inspector sub-schemas ────────────────────────────────────────────────────────────────
  // Pick subsets of ScreenField so each SchemaForm renders just the section it owns. **Split
  // Basic (always visible) from Advanced (collapsible)** so the inspector isn't a wall of every
  // property — operators rarely set colspan / default / per-field flags but always set
  // ``dd`` / ``label`` / ``required``.
  const fieldDef = useMemo<JsonSchema>(() => ({ ...((defs.ScreenField as JsonSchema | undefined) ?? { type: 'object' }), $defs: defs }), [defs])
  // ``dd`` (+ label / format / rule overrides) shown for EVERY field. On the main dialog they
  // default to blank → the field inherits from its Screen.columns entry at runtime; the operator
  // can still override per-dialog here. A nested_form field has no column layer, so this is where
  // it links its dictionary entry.
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
  // The Columns palette source: an inline nested_form's own query columns when such a tab is
  // selected, else the parent screen's read-query columns.
  // Column-group fields live on a related table joined into the read query — but an operator can add
  // a group field that ISN'T in the read SELECT (e.g. a write-only field whose value comes from a DD
  // default/rule). Those never come back from the read-query introspection above, so they'd be
  // invisible in the picker. Merge them in from the screen config (any ``columns`` entry tagged with
  // a ``group``) so every group field is pickable, deduped against what the read query already returns.
  const groupConfigColumns = useMemo<Column[]>(() => {
    const seen = new Set((readColumns ?? []).map((c) => c.name.toLowerCase()))
    const out: Column[] = []
    for (const c of screenColumns) {
      const name = String(c.name ?? '')
      if (!name || !c.group) continue
      if (seen.has(name.toLowerCase())) continue
      seen.add(name.toLowerCase())
      out.push({ name, type: null, label: typeof c.label === 'string' ? c.label : undefined })
    }
    return out
  }, [screenColumns, readColumns])
  // Group fields belong to the MAIN dialog, not an inline nested-form tab — only merge for the parent.
  const paletteColumns = nestedFormReadQuery
    ? nestedTabColumns
    : [...(readColumns ?? []), ...groupConfigColumns]
  const colItems = useMemo(() => {
    if (!paletteColumns) return [] as Column[]
    const needle = paletteQ.trim().toLowerCase()
    return needle ? paletteColumns.filter((c) => c.name.toLowerCase().includes(needle) || (c.label ?? '').toLowerCase().includes(needle)) : paletteColumns
  }, [paletteColumns, paletteQ])

  // Column names come back from the SQL result lowercased (Postgres folds unquoted identifiers),
  // but every other surface in the app uses the UPPERCASE canonical name (the dictionary keys, the
  // migrated Screen.columns, the save path's ``withUpper``). Normalise to upper here so the palette
  // reads consistently AND a dictionary auto-link actually matches (its keys are uppercase).
  const colFieldName = (c: Column) => c.name.toUpperCase()

  // Adding from each palette source. Dictionary: use the entry id as the field's name (matches
  // v1's convention where dd_id == col_target). Columns: use the UPPERCASE column name; if a
  // matching DD entry exists, auto-link it via `dd` for free.
  const addFromDict = (e: DictEntry) => addFieldFromName(e.id, e.id)
  const addFromCol = (c: Column) => {
    const name = colFieldName(c)
    const matchedDd = ddEntries?.get(name) ? name : null
    addFieldFromName(name, matchedDd)
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
    const colsInconsistent = tabHasOwnFields && cols < maxColspan
    // Auto-open when the tab carries per-tab actions (operators land on F0092 / F00926 in
    // NOMAJDE and need to find the workflow buttons; default-collapsed hid them).
    const hasTabActions = tabActions.length > 0
    return (
      <TabSettingsBox open={isNested || colsInconsistent || hasTabActions}>
        <summary>
          <ChevronRightIcon size={12} />
          <span>{t('settings.screens.visual.tabSettings.title')}</span>
          {colsInconsistent && (
            <span className="kind" style={{ color: colors.orange.main, borderColor: colors.orange.border, background: colors.orange.bg }}>
              <AlertTriangle size={10} style={{ verticalAlign: 'middle', marginRight: 3 }} />
              {t('settings.screens.visual.tabSettings.colsWarningChip')}
            </span>
          )}
          {hasTabActions && (
            <span className="kind" title={t('settings.screens.tabActions.heading')}>
              <Zap size={10} style={{ verticalAlign: 'middle', marginRight: 3 }} />
              {tabActions.length}
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
            {tabHasOwnFields && (
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
          {/* nested_form: a "Reuse a screen" picker chooses the mode. Pick a screen → REFERENCE
              mode (inherits that screen's queries + fields; only the screen + binds are editable
              here). Leave it "(inline)" → INLINE mode (configure connector + queries + the field
              canvas on this tab). The picker's presence/absence IS the mode — no separate toggle. */}
          {tabType === 'nested_form' && (
            <>
              <Field label={t('settings.screens.visual.tabSettings.reuseScreen', 'Reuse a screen')}>
                <SearchSelect
                  value={(selTab.form_screen as string | undefined) ?? ''}
                  options={sameAppScreenOptions}
                  onChange={(v) => patchTab({ form_screen: v || null })}
                  anyLabel={t('settings.screens.visual.tabSettings.reuseScreenInline', '(inline — configure the form below)')}
                  placeholder={t('settings.screens.visual.tabSettings.reuseScreenInline', '(inline — configure the form below)')}
                />
              </Field>
              {nestedFormIsRef ? (
                <>
                  <Banner $tone="info">
                    {t('settings.screens.visual.tabSettings.reuseScreenHint',
                      'This tab reuses the "{{screen}}" screen\'s form (its queries + fields). Edit those on that screen; here you only bind the parent\'s values into it.',
                      { screen: String(selTab.form_screen) })}
                  </Banner>
                  {renderTabBindsEditor()}
                </>
              ) : (
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
                  <Row gap={10}>
                    <div style={{ flex: 1 }}>
                      <Field label={t('settings.screens.editor.queries.delete_query', 'Delete query')}>
                        {renderTabQueryPicker('delete_query', { required: false, anyLabel: t('common.none') })}
                      </Field>
                    </div>
                    <div style={{ flex: 1 }} />
                  </Row>
                  {renderTabBindsEditor()}
                </>
              )}
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
          {/* form tabs only: embedded nested forms — labelled sub-forms rendered below this tab's
              own fields. Superseded for the 1:1 case by column_groups (Columns tab), which edit a
              related table's columns INLINE (grid + dialog). So we only surface this section when a
              tab ALREADY has embedded forms — existing config stays fully editable, but new 1:1
              work is steered to column groups. (Drop the ``length`` guard to re-enable adding.) */}
          {tabType === 'form' && Array.isArray(selTab.nested_forms) && (selTab.nested_forms as Row[]).length > 0 && (
            <div style={{ marginTop: 12 }}>
              <SubHead>{t('settings.screens.visual.embedded.heading', 'Embedded forms')}</SubHead>
              <Sub>{t('settings.screens.visual.embedded.hint',
                'Child-record forms shown below this tab’s fields. Each writes its own table on Save, bound to this row’s key — no on_save action needed. For a 1:1 relationship, prefer a column group (Columns tab) so the related columns edit inline.')}</Sub>
              <EmbeddedFormsEditor
                value={selTab.nested_forms as Row[]}
                onChange={(next) => patchTab({ nested_forms: next })}
                app={app}
                parentConnector={connector}
                parentColumnOptions={parentColumnOptions}
                screenOptions={sameAppScreenOptions}
                onEditQuery={(c, q) => setEditQuery({ connector: c, queryName: q })}
                defs={defs}
              />
            </div>
          )}
          {/* Per-tab actions — v2's port of v1's ``col_component='InputAction'`` rows. Toolbar
              buttons placed *inside* this tab (Roles tab on NOMASX1's settings_applications
              carries Import Security + Merge Roles + an Activity Log next to its nested grid;
              JDE F0092 / F00926 carry workflow buttons here too). Same shape every other action
              attachment point uses — one editor everywhere. */}
          {/* Theme A polish — flat list of action rows (no inline expansion). Clicking a row
              sets ``selActionPath``; the Inspector swaps to ``ActionTreeView`` in editor mode
              focused on that action, with breadcrumb dive-in for nested steps. Matching
              row gets the highlighted style so the operator sees which action is currently
              under edit. ``path={null}`` keeps this instance permanently in list mode. */}
          <ActionTreeView
            actions={tabActions}
            onChange={(n) => patchTab({ actions: n.length ? (n as unknown as Row[]) : null })}
            path={null}
            onPathChange={selectActionPath}
            selectedPath={selActionPath}
            defs={defs}
            effectiveConnector={tabEffectiveConnector}
            onEditQuery={(c, q) => setEditQuery({ connector: c, queryName: q })}
            rootLabel={t('settings.screens.tabActions.heading')}
            heading={t('settings.screens.tabActions.heading')}
            hint={t('settings.screens.tabActions.hint')}
            emptyMessage={t('settings.screens.tabActions.empty')}
            screenReadColumns={screenColumns}
          />
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
      {/* ─── PALETTE (left) ─── collapsible to a thin rail (PanelLeftOpen expands).
          Width is operator-resizable via the splitter that follows. */}
      {leftCollapsed ? (
        <CollapsedRail
          type="button"
          onClick={() => setLeftCollapsed(false)}
          title={t('settings.screens.visual.palette.expand', { defaultValue: 'Show palette' })}
        >
          <PanelLeftOpen size={14} />
        </CollapsedRail>
      ) : (
      <Col style={{ flex: `0 0 ${leftWidth}px`, width: leftWidth }}>
        <Row gap={6} style={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <ColTitle>{t('settings.screens.visual.palette.title')}</ColTitle>
          <CollapseBtn
            type="button"
            onClick={() => setLeftCollapsed(true)}
            title={t('settings.screens.visual.palette.collapse', { defaultValue: 'Hide palette' })}
          >
            <ChevronLeft size={13} />
          </CollapseBtn>
        </Row>
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
            paletteColumns == null ? (
              externalError
                ? <Empty>{externalError}</Empty>
                : <Empty>{t('common.loading')}</Empty>
            ) : colItems.length === 0 ? (
              <Empty>{t('common.noMatches')}</Empty>
            ) : colItems.map((c) => (
              <PaletteItem key={c.name} type="button" onClick={() => addFromCol(c)} title={c.type ?? ''}>
                <Code2 size={11} />
                <span className="lbl">{colFieldName(c)}</span>
                {c.label && <span className="sub">{c.label}</span>}
              </PaletteItem>
            ))
          )}
        </PaletteList>
        <div style={{ color: colors.text.muted, fontSize: fontSize.micro, lineHeight: 1.4 }}>
          {t('settings.screens.visual.palette.hint')}
        </div>
      </Col>
      )}
      {/* Drag-resize handle between the palette and the canvas. Only when the palette
          isn't collapsed (no panel to resize otherwise). */}
      {!leftCollapsed && <Splitter $active={draggingSide === 'left'} onPointerDown={startDrag('left')} />}

      {/* ─── CANVAS (center) ─── grows to fill the leftover space. */}
      <Col style={{ flex: '1 1 auto', minWidth: 0 }}>
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
                  onClick={() => selectField(i)}
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
                {/* Field grid — only meaningful for tabs with their OWN fields (form, or an
                    INLINE nested_form). A nested_table and a REFERENCE-mode nested_form both carry
                    no own fields (their target/referenced screen does), so we show a hint instead. */}
                {tabHasOwnFields && (
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
                {nestedFormIsRef && (
                  <Empty>{t('settings.screens.visual.tabSettings.reuseScreenNoFields',
                    'Fields + queries come from the reused "{{screen}}" screen. Edit them on that screen.',
                    { screen: String(selTab.form_screen) })}</Empty>
                )}
                {/* Hidden fields — collapsible group at the bottom. Migrated v1 screens often
                    have 20+ hidden columns kept around so the _put / _post queries' :NAME binds
                    don't null out untouched columns; this lets the operator see them without
                    flooding the visible canvas. Click a card to edit; in the inspector flip
                    ``hidden`` off to promote it into the visible group above. */}
                {tabHasOwnFields && hiddenFields.length > 0 && (
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

      {/* Drag-resize handle between the canvas and the inspector. Only when the inspector
          isn't collapsed. */}
      {!rightCollapsed && <Splitter $active={draggingSide === 'right'} onPointerDown={startDrag('right')} />}

      {/* ─── INSPECTOR (right) ─── collapsible to a thin rail. */}
      {rightCollapsed ? (
        <CollapsedRail
          type="button"
          onClick={() => setRightCollapsed(false)}
          title={t('settings.screens.visual.inspector.expand', { defaultValue: 'Show inspector' })}
        >
          <PanelRightOpen size={14} />
        </CollapsedRail>
      ) : (
      <Col style={{ flex: `0 0 ${rightWidth}px`, width: rightWidth }}>
        <Row gap={6} style={{ alignItems: 'center', justifyContent: 'space-between' }}>
          <ColTitle>{t('settings.screens.visual.inspector.title')}</ColTitle>
          <CollapseBtn
            type="button"
            onClick={() => setRightCollapsed(true)}
            title={t('settings.screens.visual.inspector.collapse', { defaultValue: 'Hide inspector' })}
          >
            <ChevronRightIcon size={13} />
          </CollapseBtn>
        </Row>
        {/* Breadcrumb: "Dialog ▸ <field>" or "Dialog ▸ <action>" — clicking "Dialog"
            deselects so the dialog-level view comes back (title editor + events). The
            inspector hosts EITHER a field editor OR an action editor (path-routed via
            ActionTreeView); the two are mutually exclusive — selecting one clears the other.
            The breadcrumb sits ABOVE the scroll body so it doesn't disappear when the
            operator scrolls a long inspector. When in action mode, ActionTreeView renders
            its own deeper breadcrumb on top of the body. */}
        <Crumbs>
          <CrumbBtn
            type="button"
            $active={!selField && selActionPath == null}
            onClick={() => { selectField(null); selectActionPath(null) }}
          >
            {t('settings.screens.visual.dialog.title')}
          </CrumbBtn>
          {selField && (
            <>
              <ChevronRightIcon size={11} />
              <CrumbBtn type="button" $active>{String(selField.name ?? '')}</CrumbBtn>
            </>
          )}
          {selActionPath != null && (() => {
            // Build the unified action breadcrumb: ``Tab actions`` (clickable — pops back to
            // list mode) followed by one crumb per path segment. ``breadcrumbCrumbs`` walks
            // the path against ``tabActions`` and gives us a (label, path) for each crumb;
            // its first entry is the root with an empty path → we wire that to clear
            // ``selActionPath`` (which puts the inspector back in list mode). The last entry
            // (the deepest action) is the active crumb — muted, not clickable. Avoids the
            // earlier two-row breadcrumb (outer "Dialog title › Tab actions" + inner "Tab
            // actions › Merge Roles") that stacked vertically in the inspector head.
            const crumbs = breadcrumbCrumbs(
              tabActions,
              selActionPath,
              t('settings.screens.tabActions.heading'),
            )
            return crumbs.map((c, i) => {
              const isLast = i === crumbs.length - 1
              return (
                <span key={`a-${i}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                  <ChevronRightIcon size={11} />
                  <CrumbBtn
                    type="button"
                    $active={isLast}
                    onClick={() => {
                      if (isLast) return
                      // Root crumb (empty path) → exit action mode entirely; deeper crumb →
                      // pop the path to that prefix.
                      selectActionPath(c.path.length === 0 ? null : c.path)
                    }}
                  >
                    {c.label || '(unnamed)'}
                  </CrumbBtn>
                </span>
              )
            })
          })()}
        </Crumbs>
        <InspBody>
        {selActionPath != null ? (
          // Theme A — action editor lives in the Inspector. The Tab Settings panel keeps the
          // flat action list; clicking a list row sets ``selActionPath`` and this branch
          // takes over. ActionTreeView renders the focused action's body (variant SchemaForm
          // + prompt_fields + nested sub-lists with click-to-dive). ``showBreadcrumb={false}``
          // — the unified breadcrumb above already covers the path; without this we'd render
          // two stacked breadcrumb rows in the inspector head.
          <ActionTreeView
            actions={tabActions}
            onChange={(n) => patchTab({ actions: n.length ? (n as unknown as Row[]) : null })}
            path={selActionPath}
            onPathChange={selectActionPath}
            defs={defs}
            effectiveConnector={tabEffectiveConnector}
            onEditQuery={(c, q) => setEditQuery({ connector: c, queryName: q })}
            rootLabel={t('settings.screens.tabActions.heading')}
            showBreadcrumb={false}
            screenReadColumns={screenColumns}
          />
        ) : selField ? (
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
      )}
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
