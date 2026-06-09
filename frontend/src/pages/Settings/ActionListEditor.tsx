// Shared action-list editor — used by ScreenEditor (dialog on_load/on_save/on_cancel, screen
// actions / row_menu / on_insert / on_update / on_delete) **and** by ScreenVisualBuilder's
// Tab Settings panel (a tab's per-tab actions, v2's port of v1's ``col_component='InputAction'``
// rows). Same wire shape, same behaviour everywhere — one component renders them all.
//
// What the editor does:
// 1. Lists actions with a collapsible header per row (chevron + id + variant chip + label).
// 2. Expanded body: type picker (the discriminator) → connector + target dropdowns (for the
//    three ParamBind-bearing variants run_query / call_api / navigate) → variant SchemaForm
//    for the remaining properties (label / stop_on_error / param_binds / prompt_title / …).
// 3. ``prompt_fields`` is *stripped* from the variant schema and gets its own dedicated editor
//    underneath (collapsible per-field rows, four split blocks: Basic / Advanced / Lookup binds
//    / Conditions). Only rendered for the promptable variants.
// 4. The "Edit query" pencil button next to an action's query/navigate target raises a modal
//    so operators can tweak the query without leaving the screen. The caller wires this up
//    (the modal itself lives in ScreenEditor / ScreenVisualBuilder).
//
// State management: expansion state is kept *inside* the component (one map per editor
// instance). That means switching to a different tab in the parent (and back) resets which row
// was expanded — minor concession, but the alternative (lifting state for every editor
// instance) is much noisier and operators rarely care.
import { useMemo, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Edit3, FileText, Plus, Trash2 } from 'lucide-react'
import {
  Button, Field, Input, Row, SchemaForm, SearchSelect, Stack, useModals,
  type JsonSchema, type SearchSelectOption,
} from '../../common'
import ParamBindList, { type ParamBind } from './ParamBindList'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import { pickSchemaProperties } from './connectorTables'
import { NavigateTargetField } from './NavigateTargetField'
import {
  builtinSourceOptions, mergeCandidates, screenReadColumnOptions, targetParamOptions,
} from './actionCandidates'

type Row = Record<string, unknown>

// ── action variants ──────────────────────────────────────────────────────────────────────
// Discriminated union over the seven action types (see :class:`Action` in
// :mod:`liberty.screens.config`). Each maps to its $def name; ``blankActionOfType`` seeds the
// minimum-viable shape when the operator switches types so SchemaForm doesn't render a sea of
// validation red on first paint.
export const ACTION_TYPES = [
  'run_query', 'call_api', 'call_plugin', 'call_action', 'navigate', 'set_field', 'confirm', 'notify', 'refresh',
  // Slice 4d step variants (Phase 6) — workflow control flow.
  'chain', 'if', 'loop', 'return',
] as const
export type ActionType = typeof ACTION_TYPES[number]
export const ACTION_DEF_NAME: Record<ActionType, string> = {
  run_query: 'RunQueryAction',
  call_api: 'CallApiAction',
  call_plugin: 'CallPluginAction',
  call_action: 'CallActionAction',
  navigate: 'NavigateAction',
  set_field: 'SetFieldAction',
  confirm: 'ConfirmAction',
  notify: 'NotifyAction',
  refresh: 'RefreshAction',
  chain: 'ChainAction',
  if: 'IfAction',
  loop: 'LoopAction',
  return: 'ReturnAction',
}
export function blankActionOfType(t: ActionType, id: string): Row {
  const base: Row = { id, type: t }
  if (t === 'run_query') base.query = ''
  if (t === 'call_api') { base.connector = ''; base.endpoint = '' }
  if (t === 'call_plugin') base.callable = ''
  if (t === 'call_action') base.ref = ''
  if (t === 'navigate') base.to = ''
  if (t === 'set_field') base.target = ''
  if (t === 'confirm') base.message = ''
  if (t === 'notify') { base.message = ''; base.tone = 'info' }
  if (t === 'chain') base.steps = []
  if (t === 'if') {
    base.condition = { source: '', operator: 'truthy' }
    base.then_steps = []
    base.else_steps = []
  }
  if (t === 'loop') { base.source = ''; base.steps = [] }
  if (t === 'return') base.bindings = {}
  return base
}
// Properties we render as custom widgets (above / below the variant SchemaForm) — strip them
// out of the variant schema so SchemaForm doesn't double-render them. The workflow-control
// variants (chain / if / loop / return) carry nested ``Action[]`` arrays + a ``Condition`` —
// those get dedicated recursive editors below; without stripping, SchemaForm would render
// them as generic object-list accordions with cramped widget choices.
export const ACTION_OVERRIDE_KEYS: Record<ActionType, ReadonlyArray<string>> = {
  run_query: ['connector', 'query', 'param_binds'],
  call_api: ['connector', 'endpoint', 'param_binds'],
  call_plugin: ['callable', 'param_binds'],
  call_action: ['ref', 'param_binds'],
  navigate: ['connector', 'to', 'screen', 'param_binds'],
  set_field: [],
  confirm: [],
  notify: [],
  refresh: [],
  chain: ['steps'],
  if: ['condition', 'then_steps', 'else_steps'],
  loop: ['source', 'steps'],   // ``source`` → custom autocomplete (Theme B) in ActionTreeView;
                                // ``steps`` → recursive sub-list editor in both views
  return: [],        // ``bindings: Record<str,str>`` renders fine via SchemaForm's StringMap
  // ``param_binds`` on the three ParamBind-bearing variants is rendered by the dedicated
  // ParamBindList component (one row per bind: param name + value/source toggle + the
  // matching widget — autocomplete on source). SchemaForm's generic object-list accordion
  // was too cramped for the workflow editing case.
}
// The four variants that carry ``prompt_fields`` (the ``_PromptableMixin`` in
// :mod:`liberty.screens.config`). ``chain`` joins the trio so a chain-fired workflow can
// open a single pre-fire prompt for the operator's inputs. For the rest (set_field / confirm
// / notify / refresh / if / loop / return) the PromptField editor stays hidden — these are
// internal steps that don't fire their own prompt sub-dialogs.
export const PROMPTABLE_ACTION_TYPES = new Set<ActionType>(['run_query', 'call_api', 'call_plugin', 'navigate', 'chain', 'call_action'])
export const PROMPT_FIELDS_KEY = 'prompt_fields'
// PromptField has 11+ properties; split into Basic / Advanced / Lookup binds / Conditions so
// each per-field expander reads like the visual builder's field inspector instead of a wall of
// toggles. ``pickSchemaProperties`` flattens ``x_group`` so the picked fields all sit on one
// SchemaForm (no tab strip inside an already-expanded row).
export const PROMPT_FIELD_DEF_NAME = 'PromptField'
export const PROMPT_FIELD_BASIC_KEYS = ['name', 'dd', 'label', 'format', 'required', 'default'] as const
export const PROMPT_FIELD_ADVANCED_KEYS = ['hidden', 'disabled', 'colspan'] as const
export const PROMPT_FIELD_BINDS_KEY = 'lookup_param_binds'
export const PROMPT_FIELD_CONDITION_KEYS = ['visible_when', 'required_when', 'disabled_when'] as const

/** Lightweight ParamBind.source candidates derived from one action's own ``prompt_fields``.
 *  ActionListEditor (the dialog-hook / screen-hook editor used by ScreenEditor) has no
 *  chain-path context — it can't walk the tree to find preceding bind_result steps the way
 *  ActionTreeView does. The next-best autocomplete is the action's *own* prompt fields:
 *  each becomes ``INPUT.<name>`` since the runtime merges prompt values under ``ctx.INPUT``
 *  before the action fires. Not exhaustive (no step results, no parent-chain prompts), but
 *  covers the common ``{param: 'USER', source: 'INPUT.USER'}`` case operators write by hand. */
export function promptFieldsToOptions(action: Row): SearchSelectOption[] {
  const fields = Array.isArray(action.prompt_fields) ? (action.prompt_fields as Row[]) : []
  return fields
    .map((f) => String(f.name ?? '').trim())
    .filter((n) => n.length > 0)
    .map((n) => ({ value: `INPUT.${n}`, label: '(prompt input)', mono: `INPUT.${n}` }))
}

// ── styled bits (same look the rest of the Screen builders use) ─────────────────────────────
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; line-height: 1.5; margin-bottom: 10px;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`
const FieldList = styled.div`display: flex; flex-direction: column; gap: 6px;`
const FieldHeader = styled.button<{ $open?: boolean }>`
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 8px 12px; text-align: left;
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; cursor: pointer;
  border-radius: ${({ $open }) => ($open ? `${radius.md} ${radius.md} 0 0` : radius.md)};
  border-bottom-color: ${({ $open }) => ($open ? colors.blue.border : colors.border)};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  & .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .dd { font-family: ${fonts.mono}; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  &:hover { border-color: ${colors.blue.border}; }
`
const FieldBody = styled.div`
  border: 1px solid ${colors.blue.border}; border-top: none; padding: 12px;
  border-radius: 0 0 ${radius.md} ${radius.md}; background: ${colors.bg.input};
`

export interface ActionListEditorProps {
  /** The list of Action dicts (raw JSON-compatible shape — see :class:`Action` in :mod:`liberty.screens.config`). */
  actions: Row[]
  /** Called whenever the list changes. The consumer owns the dirty flag and the save. */
  onChange: (next: Row[]) => void
  /** Section heading shown above the list. */
  heading: string
  /** One-line description shown below the heading. */
  hint: string
  /** Placeholder shown when the list is empty. */
  emptyMessage: string
  /** The screen JSON schema's ``$defs`` map — picks ``RunQueryAction`` / ``PromptField`` / …. */
  defs: Record<string, JsonSchema>
  /** The Screen's effective connector (its ``connector`` field, or its app name). Used as the
   *  fallback when an action's own ``connector`` field is unset. */
  effectiveConnector: string
  /** Called when the operator clicks the pencil next to an action's query/navigate target —
   *  the consumer raises ``EditQueryModal`` (the modal itself lives in the parent screen). */
  onEditQuery: (connector: string, queryName: string) => void
  /** The firing screen's read-query columns (the row-context-row's columns at runtime). Used
   *  by :class:`ParamBindList` to autocomplete ``source`` paths against the firing context —
   *  plain (no-dot) values resolve against the row at runtime. Pass ``screen.columns`` from
   *  the parent (ScreenEditor / dialog-hook hosts). Empty / undefined → only the action's own
   *  ``prompt_fields`` show in the source dropdown. */
  screenReadColumns?: Row[]
}

export default function ActionListEditor({
  actions, onChange, heading, hint, emptyMessage, defs, effectiveConnector, onEditQuery,
  screenReadColumns,
}: ActionListEditorProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const { connectors: wsConnectors, screens: wsScreens, findScreenById } = useWorkspace()
  // ``param`` (target placeholder) candidates for an action. ``targetParamOptions`` keys off ``to``
  // (the query name) for navigate — a navigate-to-SCREEN has no ``to``, so resolve the target
  // screen's read_query first and feed THAT as the query, otherwise the PARAM field falls back to a
  // plain text input. (Mirrors ActionTreeView.selectedDeclaredParamOptions.)
  const paramOptionsForAction = (a: Row): SearchSelectOption[] => {
    if (String(a?.type) === 'navigate' && typeof a?.screen === 'string' && a.screen) {
      const conn = (typeof a.connector === 'string' && a.connector.trim() ? a.connector : effectiveConnector) as string
      const hit = findScreenById(conn, a.screen)
      if (!hit?.read_query) return []
      const tConn = hit.connector || conn
      return targetParamOptions({ type: 'navigate', to: hit.read_query, connector: tConn }, wsConnectors, tConn)
    }
    return targetParamOptions(a, wsConnectors, effectiveConnector)
  }

  // Per-row expansion: ``actionsExpanded`` for the outer action list, ``promptExpanded`` keyed
  // by ``<actionIdx>`` for the inner PromptField list (one per action).
  const [actionsExpanded, setActionsExpanded] = useState<number | null>(null)
  const [promptExpanded, setPromptExpanded] = useState<Record<number, number | null>>({})

  // ── workspace catalog → dropdown options ───────────────────────────────────────────────
  const sqlConnectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? []).filter((c) => c.type === 'sql').map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [wsConnectors],
  )
  const apiConnectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? []).filter((c) => c.type === 'api').map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [wsConnectors],
  )

  // ── variant + PromptField sub-schemas (memoised against defs) ──────────────────────────
  const actionVariantSchema = (a: Row): JsonSchema | null => {
    const defName = ACTION_DEF_NAME[(a.type as ActionType) || 'run_query']
    const v = defs[defName] as JsonSchema | undefined
    if (!v) return null
    const props: Record<string, JsonSchema> = { ...(v.properties ?? {}) }
    delete props.type
    delete props[PROMPT_FIELDS_KEY]
    for (const k of ACTION_OVERRIDE_KEYS[(a.type as ActionType) || 'run_query']) delete props[k]
    return { ...v, properties: props, $defs: defs }
  }
  const promptFieldDef = useMemo<JsonSchema>(
    () => ({ ...((defs[PROMPT_FIELD_DEF_NAME] as JsonSchema | undefined) ?? { type: 'object' }), $defs: defs }),
    [defs],
  )
  const promptBasicSchema = useMemo<JsonSchema>(
    () => pickSchemaProperties(promptFieldDef, PROMPT_FIELD_BASIC_KEYS as unknown as string[]),
    [promptFieldDef],
  )
  const promptAdvancedSchema = useMemo<JsonSchema>(
    () => pickSchemaProperties(promptFieldDef, PROMPT_FIELD_ADVANCED_KEYS as unknown as string[]),
    [promptFieldDef],
  )
  const promptBindsSchema = useMemo<JsonSchema>(
    () => ({ ...pickSchemaProperties(promptFieldDef, [PROMPT_FIELD_BINDS_KEY] as unknown as string[]), $defs: defs }),
    [promptFieldDef, defs],
  )
  const promptConditionsSchema = useMemo<JsonSchema>(
    () => ({ ...pickSchemaProperties(promptFieldDef, PROMPT_FIELD_CONDITION_KEYS as unknown as string[]), $defs: defs }),
    [promptFieldDef, defs],
  )

  // ── list mutators ──────────────────────────────────────────────────────────────────────
  const updateAction = (idx: number, patch: Row) => {
    const next = actions.slice()
    next[idx] = { ...next[idx], ...patch }
    for (const k of Object.keys(patch)) {
      if (patch[k] === undefined || patch[k] === null || patch[k] === '' || patch[k] === false) delete (next[idx] as Row)[k]
    }
    onChange(next)
  }
  const addAction = async () => {
    const existing = actions.map((a) => a.id)
    const id = (await modals.prompt({
      title: t('settings.screens.action.add'),
      message: t('settings.screens.action.namePrompt'),
      validate: (v) => {
        if (!v) return null
        if (existing.includes(v)) return t('settings.screens.action.idExists', { id: v })
        return null
      },
    }))?.trim()
    if (!id) return
    onChange([...actions, blankActionOfType('run_query', id)])
    setActionsExpanded(actions.length)
  }
  const removeAction = async (idx: number) => {
    const ok = await modals.confirm({
      title: t('settings.screens.action.delete'),
      message: t('settings.screens.action.confirmDelete', { id: actions[idx]?.id }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    const next = actions.slice(); next.splice(idx, 1)
    onChange(next)
    setActionsExpanded(actionsExpanded === idx ? null : actionsExpanded != null && actionsExpanded > idx ? actionsExpanded - 1 : actionsExpanded)
  }
  const changeActionType = (idx: number, newType: ActionType) => {
    const cur = actions[idx] ?? {}
    if (cur.type === newType) return
    // Preserve id + label + stop_on_error; reset the rest to the new variant's minimum shape.
    const seeded = blankActionOfType(newType, String(cur.id ?? ''))
    if (cur.label != null) seeded.label = cur.label
    if (cur.stop_on_error != null) seeded.stop_on_error = cur.stop_on_error
    const next = actions.slice()
    next[idx] = seeded
    onChange(next)
  }

  // ── connector + target dropdowns for the three ParamBind-bearing variants ─────────────
  const renderActionOverrides = (a: Row, onPatch: (patch: Row) => void): ReactNode => {
    const aType = (a.type as ActionType) || 'run_query'
    if (aType !== 'run_query' && aType !== 'call_api' && aType !== 'navigate') return null
    const isApi = aType === 'call_api'
    const opts = isApi ? apiConnectorOptions : sqlConnectorOptions
    const actionConn = (a.connector as string | undefined) || effectiveConnector
    const targetConnMeta = (wsConnectors ?? []).find((c) => c.name === actionConn)
    let targetOpts: SearchSelectOption[] = []
    if (targetConnMeta) {
      if ((aType === 'run_query' || aType === 'navigate') && targetConnMeta.type === 'sql') {
        targetOpts = targetConnMeta.queries.map((q) => ({
          value: q.name,
          label: q.description || q.label || q.name,
          mono: q.name,
        }))
      } else if (aType === 'call_api' && targetConnMeta.type === 'api') {
        targetOpts = targetConnMeta.endpoints.map((e) => ({
          value: e.name,
          label: e.label || e.name,
          mono: e.name,
        }))
      }
    }
    const targetKey: 'query' | 'endpoint' | 'to' = aType === 'run_query' ? 'query' : aType === 'call_api' ? 'endpoint' : 'to'
    const targetLabel = t(`settings.screens.action.target.${aType}`)
    return (
      <>
        <Field label={t('settings.screens.action.connector')}>
          <SearchSelect
            value={(a.connector as string | undefined) ?? ''}
            options={opts}
            onChange={(v) => onPatch({ connector: v && v !== effectiveConnector ? v : null })}
            anyLabel={t('settings.screens.editor.connectorUseApp', { app: effectiveConnector })}
            placeholder={effectiveConnector}
          />
        </Field>
        {aType === 'navigate'
          ? (
            <NavigateTargetField
              key={`${a.id ?? ''}:navtarget`}
              action={a}
              onPatch={onPatch}
              actionConn={actionConn}
              hasConnector={!!targetConnMeta}
              queryOptions={targetOpts}
              screenOptions={((wsScreens ?? {})[actionConn] ?? []).map((s) => ({ value: s.id, label: s.label || s.id, mono: s.id })).sort((x, y) => String(x.mono).localeCompare(String(y.mono)))}
              onEditQuery={onEditQuery}
            />
          )
          : (
            <Field label={targetLabel + ' *'}>
              <Row gap={6} style={{ alignItems: 'center' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <SearchSelect
                    value={(a[targetKey] as string | undefined) ?? ''}
                    options={targetOpts}
                    onChange={(v) => onPatch({ [targetKey]: v || '' })}
                    placeholder={targetConnMeta ? t('common.pick') : t('settings.screens.editor.queries.pickConnectorFirst')}
                    loading={!targetConnMeta}
                  />
                </div>
                {aType === 'run_query' && typeof a[targetKey] === 'string' && a[targetKey] && actionConn && (
                  <Button
                    $variant="ghost"
                    $size="sm"
                    onClick={() => onEditQuery(actionConn, String(a[targetKey]))}
                    title={t('settings.editQuery.edit', 'Edit query')}
                  >
                    <Edit3 size={13} />
                  </Button>
                )}
              </Row>
            </Field>
          )}
      </>
    )
  }

  // ── PromptField list (slice 4b — v2's port of v1's ``ly_act_params``) ─────────────────
  // Shown only for the three promptable variants. Mirrors the visual-builder field inspector:
  // collapsible per-row, expanded body splits PromptField into four blocks (Basic / Advanced /
  // Lookup binds / Conditions). Expansion state is keyed by the parent action's index, so each
  // action's prompt list remembers which row is open across re-renders.
  const renderPromptFields = (actionIdx: number, action: Row, onPatch: (patch: Row) => void): ReactNode => {
    const aType = String(action.type ?? 'run_query') as ActionType
    if (!PROMPTABLE_ACTION_TYPES.has(aType)) return null
    const fields = Array.isArray(action[PROMPT_FIELDS_KEY]) ? (action[PROMPT_FIELDS_KEY] as Row[]) : []
    const expanded = promptExpanded[actionIdx] ?? null
    const setExpanded = (v: number | null) =>
      setPromptExpanded((prev) => ({ ...prev, [actionIdx]: v }))
    const setList = (next: Row[]) => {
      onPatch({ [PROMPT_FIELDS_KEY]: next.length ? next : null })
    }
    const updateField = (idx: number, patch: Row) => {
      const next = fields.slice()
      next[idx] = { ...next[idx], ...patch }
      for (const k of Object.keys(patch)) {
        if (k === 'name') continue
        const val = patch[k]
        if (val === undefined || val === null || val === '' || val === false || (Array.isArray(val) && val.length === 0)) {
          delete (next[idx] as Row)[k]
        }
      }
      setList(next)
    }
    const add = async () => {
      const existing = fields.map((f) => String(f.name ?? ''))
      const name = (await modals.prompt({
        title: t('settings.screens.prompt.add'),
        message: t('settings.screens.prompt.namePrompt'),
        validate: (v) => {
          if (!v) return null
          if (existing.includes(v)) return t('settings.screens.prompt.nameExists', { name: v })
          return null
        },
      }))?.trim()
      if (!name) return
      setList([...fields, { name }])
      setExpanded(fields.length)
    }
    const remove = async (idx: number) => {
      const ok = await modals.confirm({
        title: t('settings.screens.prompt.delete'),
        message: t('settings.screens.prompt.confirmDelete', { name: fields[idx]?.name }),
        variant: 'danger',
        confirmLabel: t('common.delete'),
      })
      if (!ok) return
      const next = fields.slice(); next.splice(idx, 1)
      setList(next)
      setExpanded(expanded === idx ? null : expanded != null && expanded > idx ? expanded - 1 : expanded)
    }
    const summary = (f: Row): string => {
      const bits: string[] = []
      if (typeof f.dd === 'string' && f.dd) bits.push(`dd=${f.dd}`)
      if (typeof f.format === 'string' && f.format) bits.push(f.format)
      if (f.required) bits.push('required')
      return bits.join(' · ')
    }
    return (
      <Stack gap={8} style={{ marginTop: 4 }}>
        <strong style={{ color: colors.text.primary, fontSize: fontSize.sm }}>
          {t('settings.screens.prompt.heading')}
        </strong>
        <Sub>{t('settings.screens.prompt.hint')}</Sub>
        <FieldList>
          {fields.length === 0 && <Empty>{t('settings.screens.prompt.empty')}</Empty>}
          {fields.map((f, i) => {
            const open = expanded === i
            return (
              <div key={i}>
                <FieldHeader $open={open} onClick={() => setExpanded(open ? null : i)}>
                  {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                  <FileText size={13} />
                  <span className="name">{String(f.name ?? '')}</span>
                  {summary(f) && <span className="dd">{summary(f)}</span>}
                </FieldHeader>
                {open && (
                  <FieldBody>
                    <Stack gap={12}>
                      <SchemaForm
                        schema={promptBasicSchema}
                        defs={defs}
                        value={f}
                        onChange={(v) => {
                          const patch: Row = {}
                          for (const k of PROMPT_FIELD_BASIC_KEYS) patch[k] = v[k]
                          updateField(i, patch)
                        }}
                      />
                      <details>
                        <summary style={{ cursor: 'pointer', color: colors.text.muted, fontSize: fontSize.sm }}>
                          {t('settings.screens.prompt.advanced')}
                        </summary>
                        <div style={{ marginTop: 8 }}>
                          <SchemaForm
                            schema={promptAdvancedSchema}
                            defs={defs}
                            value={f}
                            onChange={(v) => {
                              const patch: Row = {}
                              for (const k of PROMPT_FIELD_ADVANCED_KEYS) patch[k] = v[k]
                              updateField(i, patch)
                            }}
                          />
                        </div>
                      </details>
                      <details>
                        <summary style={{ cursor: 'pointer', color: colors.text.muted, fontSize: fontSize.sm }}>
                          {t('settings.screens.prompt.binds')}
                        </summary>
                        <div style={{ marginTop: 8 }}>
                          <SchemaForm
                            schema={promptBindsSchema}
                            defs={defs}
                            value={f}
                            onChange={(v) => updateField(i, { [PROMPT_FIELD_BINDS_KEY]: v[PROMPT_FIELD_BINDS_KEY] })}
                          />
                        </div>
                      </details>
                      <details>
                        <summary style={{ cursor: 'pointer', color: colors.text.muted, fontSize: fontSize.sm }}>
                          {t('settings.screens.prompt.conditions')}
                        </summary>
                        <div style={{ marginTop: 8 }}>
                          <SchemaForm
                            schema={promptConditionsSchema}
                            defs={defs}
                            value={f}
                            onChange={(v) => {
                              const patch: Row = {}
                              for (const k of PROMPT_FIELD_CONDITION_KEYS) patch[k] = v[k]
                              updateField(i, patch)
                            }}
                          />
                        </div>
                      </details>
                      <Row gap={8}>
                        <Button $variant="danger" $size="sm" onClick={() => remove(i)}>
                          <Trash2 size={13} /> {t('settings.screens.prompt.delete')}
                        </Button>
                      </Row>
                    </Stack>
                  </FieldBody>
                )}
              </div>
            )
          })}
        </FieldList>
        <Button $variant="ghost" $size="sm" onClick={add} style={{ justifyContent: 'flex-start', alignSelf: 'flex-start' }}>
          <Plus size={13} /> {t('settings.screens.prompt.add')}
        </Button>
      </Stack>
    )
  }

  // ── workflow-control extras (slice 4d: ChainAction / IfAction / LoopAction / ReturnAction) ─
  // Each new step variant has nested ``Action[]`` arrays (and IfAction a ``Condition``). The
  // generic SchemaForm would render those as inline accordions — workable, but the editing
  // depth gets cramped quickly. Dedicated UI mirrors how a v1 ``ly_actions`` workflow reads:
  // a chain shows its sub-steps directly, an if shows the condition above two branch lists,
  // a loop shows the source path + the inner steps. Recursion lands naturally — the same
  // ActionListEditor renders the nested step lists, so all the editing features (type
  // switcher, prompt_fields, connector / query / endpoint pickers, etc.) keep working at
  // every nesting depth.
  const conditionSchema = useMemo<JsonSchema | null>(
    () => (defs.Condition ? { ...(defs.Condition as JsonSchema), $defs: defs } : null),
    [defs],
  )
  const renderWorkflowExtras = (action: Row, onPatch: (patch: Row) => void): ReactNode => {
    const aType = String(action.type ?? '') as ActionType
    if (aType === 'chain') {
      const steps = Array.isArray(action.steps) ? (action.steps as Row[]) : []
      return (
        <ActionListEditor
          actions={steps}
          onChange={(next) => onPatch({ steps: next.length ? next : null })}
          heading={t('settings.screens.chain.heading')}
          hint={t('settings.screens.chain.hint')}
          emptyMessage={t('settings.screens.chain.empty')}
          defs={defs}
          effectiveConnector={effectiveConnector}
          onEditQuery={onEditQuery}
          screenReadColumns={screenReadColumns}
        />
      )
    }
    if (aType === 'if') {
      const condition = (action.condition && typeof action.condition === 'object'
        ? action.condition
        : { source: '', operator: 'truthy' }) as Row
      const thenSteps = Array.isArray(action.then_steps) ? (action.then_steps as Row[]) : []
      const elseSteps = Array.isArray(action.else_steps) ? (action.else_steps as Row[]) : []
      return (
        <Stack gap={14} style={{ marginTop: 4 }}>
          <Stack gap={6}>
            <strong style={{ color: colors.text.primary, fontSize: fontSize.sm }}>
              {t('settings.screens.condition.heading')}
            </strong>
            <Sub>{t('settings.screens.condition.hint')}</Sub>
            {conditionSchema ? (
              <SchemaForm
                schema={conditionSchema}
                defs={defs}
                value={condition}
                onChange={(v) => onPatch({ condition: v })}
              />
            ) : (
              <Empty>{t('settings.screens.condition.unavailable')}</Empty>
            )}
          </Stack>
          <ActionListEditor
            actions={thenSteps}
            onChange={(next) => onPatch({ then_steps: next.length ? next : null })}
            heading={t('settings.screens.if.thenHeading')}
            hint={t('settings.screens.if.thenHint')}
            emptyMessage={t('settings.screens.if.thenEmpty')}
            defs={defs}
            effectiveConnector={effectiveConnector}
            onEditQuery={onEditQuery}
          />
          <ActionListEditor
            actions={elseSteps}
            onChange={(next) => onPatch({ else_steps: next.length ? next : null })}
            heading={t('settings.screens.if.elseHeading')}
            hint={t('settings.screens.if.elseHint')}
            emptyMessage={t('settings.screens.if.elseEmpty')}
            defs={defs}
            effectiveConnector={effectiveConnector}
            onEditQuery={onEditQuery}
          />
        </Stack>
      )
    }
    if (aType === 'loop') {
      const steps = Array.isArray(action.steps) ? (action.steps as Row[]) : []
      const source = typeof action.source === 'string' ? action.source : ''
      return (
        <Stack gap={14} style={{ marginTop: 4 }}>
          {/* ``source`` is stripped from the variant SchemaForm (it lives in the loop's
              ACTION_OVERRIDE_KEYS) so we can render it manually with the dotted-path hint
              right next to it. ActionListEditor's consumers don't carry a chain path, so this
              stays a plain text input here — the ActionTreeView in the Visual Designer's
              Inspector renders the same field as a SearchSelect with chain-context candidates
              (Theme B autocomplete). */}
          <Field label={t('settings.screens.loop.sourceLabel')}>
            <Input
              value={source}
              onChange={(e) => onPatch({ source: e.target.value })}
              placeholder={t('settings.screens.loop.sourcePlaceholder')}
            />
          </Field>
          <Sub style={{ marginTop: -6 }}>{t('settings.screens.loop.sourceHint')}</Sub>
          <ActionListEditor
            actions={steps}
            onChange={(next) => onPatch({ steps: next.length ? next : null })}
            heading={t('settings.screens.loop.bodyHeading')}
            hint={t('settings.screens.loop.bodyHint')}
            emptyMessage={t('settings.screens.loop.bodyEmpty')}
            defs={defs}
            effectiveConnector={effectiveConnector}
            onEditQuery={onEditQuery}
          />
        </Stack>
      )
    }
    return null
  }

  return (
    <Stack gap={8} style={{ marginTop: 14 }}>
      <strong style={{ color: colors.text.primary, fontSize: fontSize.sm }}>{heading}</strong>
      <Sub>{hint}</Sub>
      <FieldList>
        {actions.length === 0 && <Empty>{emptyMessage}</Empty>}
        {actions.map((a, i) => {
          const open = actionsExpanded === i
          const aType = String(a.type ?? 'run_query') as ActionType
          const schema = actionVariantSchema(a)
          return (
            <div key={i}>
              <FieldHeader $open={open} onClick={() => setActionsExpanded(open ? null : i)}>
                {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                <FileText size={13} />
                <span className="name">{String(a.id ?? '')}</span>
                <span className="dd">{aType}</span>
                {a.label != null && a.label !== '' && <span className="dd">· {String(a.label)}</span>}
              </FieldHeader>
              {open && (
                <FieldBody>
                  <Stack gap={12}>
                    <Field label={t('settings.screens.action.type')}>
                      <SearchSelect
                        value={aType}
                        options={ACTION_TYPES.map((tt) => ({ value: tt, label: tt, mono: tt }))}
                        onChange={(v) => changeActionType(i, v as ActionType)}
                      />
                    </Field>
                    {renderActionOverrides(a, (patch) => updateAction(i, patch))}
                    {schema ? (
                      <SchemaForm
                        schema={schema}
                        defs={defs}
                        value={a}
                        onChange={(v) => updateAction(i, v)}
                      />
                    ) : (
                      <Empty>{t('settings.screens.action.unknownType', { type: aType })}</Empty>
                    )}
                    {/* ParamBindList — dedicated editor for the variant's ``param_binds``
                        array (stripped from the SchemaForm above via ACTION_OVERRIDE_KEYS).
                        Source autocomplete = the action's own ``prompt_fields`` (offered as
                        ``INPUT.<name>``) merged with the firing screen's row columns (plain
                        column names resolve against the row at runtime). ``paramOptions`` =
                        the action's target query / endpoint params. ActionListEditor lacks
                        the chain-path context the ActionTreeView (Visual Designer's
                        Inspector) carries, so chain-context candidates (preceding
                        bind_result captures, loop bindings) don't appear here. */}
                    {(aType === 'run_query' || aType === 'call_api' || aType === 'navigate') && (
                      <Stack gap={6}>
                        <strong style={{ color: colors.text.primary, fontSize: fontSize.sm }}>
                          {t('settings.screens.paramBinds.heading')}
                        </strong>
                        <Sub>{t('settings.screens.paramBinds.hint')}</Sub>
                        <ParamBindList
                          value={Array.isArray(a.param_binds) ? (a.param_binds as ParamBind[]) : []}
                          onChange={(next) => updateAction(i, { param_binds: next.length ? next : null })}
                          sourceOptions={mergeCandidates(
                            promptFieldsToOptions(a),
                            screenReadColumnOptions(screenReadColumns),
                            builtinSourceOptions(),
                          )}
                          paramOptions={paramOptionsForAction(a)}
                        />
                      </Stack>
                    )}
                    {/* Slice 4b — prompt-fields list for the four promptable variants
                        (run_query / call_api / navigate / chain). */}
                    {renderPromptFields(i, a, (patch) => updateAction(i, patch))}
                    {/* Slice 4d — workflow-control extras (steps / condition / then-else /
                        loop body). The recursive ActionListEditor lets each nested step keep
                        the full editing experience (type switch, prompts, connector pickers). */}
                    {renderWorkflowExtras(a, (patch) => updateAction(i, patch))}
                    <Row gap={8}>
                      <Button $variant="danger" $size="sm" onClick={() => removeAction(i)}>
                        <Trash2 size={13} /> {t('settings.screens.action.delete')}
                      </Button>
                    </Row>
                  </Stack>
                </FieldBody>
              )}
            </div>
          )
        })}
      </FieldList>
      <Button $variant="ghost" $size="sm" onClick={addAction} style={{ justifyContent: 'flex-start', alignSelf: 'flex-start' }}>
        <Plus size={13} /> {t('settings.screens.action.add')}
      </Button>
    </Stack>
  )
}
