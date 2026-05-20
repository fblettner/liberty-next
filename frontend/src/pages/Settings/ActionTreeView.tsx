// Path-routed action editor (Theme A polish). Two render modes wired by the same component:
//
// * **list mode** — used by the Visual Designer's Tab Settings panel. Renders a flat list of
//   action rows (id · type · label). Click → tells the parent to select that action via
//   ``onPathChange``. No inline expansion; the editing happens elsewhere (typically in the
//   right-column Inspector).
//
// * **editor mode** — used by the Visual Designer's right-column Inspector when an action is
//   selected. Renders the action *at the selected path* with:
//   - a clickable breadcrumb at the top (``rootLabel › chain "X" › if "Y"`` → click any crumb
//     to pop back up that level);
//   - the variant body (type picker + connector / target dropdowns + variant SchemaForm +
//     prompt_fields editor);
//   - for workflow variants (chain / if / loop), a *flat* sub-list of the nested step
//     group(s) — chain shows ``steps``; if shows the Condition inline + ``then_steps`` and
//     ``else_steps`` lists; loop shows the ``source`` field + ``steps`` list. Clicking any
//     row in those sub-lists dives one level deeper (push to ``path``).
//
// Why a separate component from :file:`ActionListEditor.tsx`: the Visual Designer wants to
// pull the editor *out* of the canvas column into the Inspector (Theme A — biggest reduction
// in vertical sprawl), with breadcrumb-based dive-in instead of inline accordion expansion.
// :file:`ActionListEditor` keeps the inline-expansion behaviour for its other consumers
// (ScreenEditor's hook attachments — dialog on_save / screen.on_insert / etc., where the
// lists are usually short and an Inspector isn't available).
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import {
  ChevronDown, ChevronRight, Edit3, FileText, GitBranch, LayoutList, Plus, Repeat, Trash2, Zap,
} from 'lucide-react'
import {
  Button, Field, Row, SchemaForm, SearchSelect, Stack, useModals, type JsonSchema, type SearchSelectOption,
} from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import { pickSchemaProperties } from './connectorTables'
import {
  ACTION_DEF_NAME, ACTION_OVERRIDE_KEYS, ACTION_TYPES, blankActionOfType,
  PROMPT_FIELDS_KEY, PROMPT_FIELD_ADVANCED_KEYS, PROMPT_FIELD_BASIC_KEYS, PROMPT_FIELD_BINDS_KEY,
  PROMPT_FIELD_CONDITION_KEYS, PROMPT_FIELD_DEF_NAME, PROMPTABLE_ACTION_TYPES,
  type ActionType,
} from './ActionListEditor'
import {
  appendAtPath, breadcrumbCrumbs, pathEquals, removeAtPath, resolveAtPath, updateAtPath,
  type ActionPath,
} from './actionPath'

type Row = Record<string, unknown>

// ── styled bits ────────────────────────────────────────────────────────────────────────────
// The list-mode rows + the editor-mode breadcrumb + the editor's sub-list rows all share the
// same "monospaced id + variant chip + label" style. ``ActionListEditor`` uses a similar look
// for its inline-expansion rows; we don't share components because the list-mode rows have a
// different right-icon (chevron) than the editor-mode sub-list rows (also chevron, but
// semantically different — "dive in" vs "expand inline").
const RowBox = styled.div`display: flex; flex-direction: column; gap: 6px;`
const RowItem = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 8px 12px; text-align: left;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  cursor: pointer; border-radius: ${radius.md};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  & .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .dd { font-family: ${fonts.mono}; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  & .lead, & .trail { flex-shrink: 0; color: ${colors.text.muted}; }
  &:hover { border-color: ${colors.blue.border}; background: ${colors.bg.card}; }
`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; line-height: 1.5;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 16px 4px; text-align: center; font-style: italic;`
const CrumbBar = styled.div`
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  padding: 6px 0; border-bottom: 1px solid ${colors.border};
`
const CrumbBtn = styled.button<{ $active?: boolean }>`
  background: transparent; border: none; padding: 0; cursor: ${({ $active }) => ($active ? 'default' : 'pointer')};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.blue.main)};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  &:hover { text-decoration: ${({ $active }) => ($active ? 'none' : 'underline')}; }
`
// Subgroups inside the editor body. ChainAction / LoopAction render one sublist; IfAction
// renders Condition + then + else. The heading is a small chip so each block reads as a
// labelled section without taking too much vertical space.
const SubHead = styled.strong`color: ${colors.text.primary}; font-size: ${fontSize.sm};`
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

// Per-variant icon (Theme B preview) so the row leads with a glyph that says "this is an if"
// / "this is a loop" rather than just text. Cheap visual cue, big help when scanning a chain
// with 6+ steps of mixed kinds.
function variantIcon(type: ActionType): ReactNode {
  switch (type) {
    case 'run_query': return <Zap size={13} />
    case 'call_api': return <Zap size={13} />     // same icon for now — Theme B can split
    case 'navigate': return <FileText size={13} />
    case 'if': return <GitBranch size={13} />
    case 'loop': return <Repeat size={13} />
    case 'chain': return <LayoutList size={13} />
    default: return <FileText size={13} />
  }
}

// ── component props ─────────────────────────────────────────────────────────────────────────
export interface ActionTreeViewProps {
  /** The list of Action dicts the operator is editing. */
  actions: Row[]
  /** Called whenever the list changes. The consumer owns the dirty flag + save. */
  onChange: (next: Row[]) => void
  /** Currently selected action path. ``null`` (or empty) renders list mode; a non-empty path
   *  renders editor mode at that depth. */
  path: ActionPath | null
  /** Called when the user clicks a list row / a breadcrumb crumb / a dive-in chevron. */
  onPathChange: (next: ActionPath | null) => void
  /** The screen JSON schema's ``$defs`` map — picks ``RunQueryAction`` / ``PromptField`` / …. */
  defs: Record<string, JsonSchema>
  /** The Screen's effective connector (its ``connector`` field, or its app name). Used as the
   *  fallback when an action's own ``connector`` field is unset. */
  effectiveConnector: string
  /** Called when the operator clicks the pencil next to an action's query / navigate target. */
  onEditQuery: (connector: string, queryName: string) => void
  /** Breadcrumb root label — typically the tab's label or "Tab actions". */
  rootLabel: string
  /** Optional copy override for the list-mode heading / hint / empty placeholder. The defaults
   *  read sensibly for the per-tab actions case (the only consumer today); a future caller can
   *  point us at a different i18n bucket. */
  heading?: string
  hint?: string
  emptyMessage?: string
  /** Optional highlight path — when this view is in *list mode* (``path == null``), the row
   *  matching ``selectedPath`` gets the highlighted style. Lets the Tab Settings list show
   *  which action is currently being edited in the Inspector (the action editor is a sibling
   *  ActionTreeView in editor mode bound to the same selection state). */
  selectedPath?: ActionPath | null
}

export default function ActionTreeView({
  actions, onChange, path, onPathChange, defs, effectiveConnector, onEditQuery, rootLabel,
  heading, hint, emptyMessage, selectedPath,
}: ActionTreeViewProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const { connectors: wsConnectors } = useWorkspace()

  // ── workspace catalog → dropdown options ───────────────────────────────────────────────
  const sqlConnectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? []).filter((c) => c.type === 'sql').map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [wsConnectors],
  )
  const apiConnectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? []).filter((c) => c.type === 'api').map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [wsConnectors],
  )

  // ── variant + PromptField sub-schemas (memoised against defs — identical to ActionListEditor) ──
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
  const conditionSchema = useMemo<JsonSchema | null>(
    () => (defs.Condition ? { ...(defs.Condition as JsonSchema), $defs: defs } : null),
    [defs],
  )
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

  // ── prompt-field row expansion (per-action) ────────────────────────────────────────────
  // Only one action is visible at a time in editor mode, so a single ``selectedFieldIdx`` is
  // enough. List mode doesn't render prompts.
  const [promptFieldOpen, setPromptFieldOpen] = useState<number | null>(null)

  // Stale-path cleanup. The path is captured imperatively (operator clicked a list row);
  // between captures the actions list may have shrunk (operator deleted an action elsewhere,
  // re-migration shuffled ids, etc.). Drop a stale path on the next tick so we don't render
  // a broken editor. The setter runs from an effect — calling it in render would violate
  // "no state updates during another component's render".
  useEffect(() => {
    if (path != null && path.length > 0 && resolveAtPath(actions, path) === undefined) {
      onPathChange(null)
    }
  }, [actions, path, onPathChange])

  // ── path-aware mutators (delegating to the immutable helpers) ──────────────────────────
  const patchSelected = (patch: Row) => {
    if (!path || path.length === 0) return
    onChange(updateAtPath(actions, path, patch))
  }
  const replaceSelected = (next: Row) => {
    // ``updateAtPath`` merges; for a full replace (e.g. type change reseeds the variant) we
    // need to overwrite — patch with each key of next + null-out absent keys. Simpler:
    // remove the action then re-insert at the same index. We do it inline so the operator
    // doesn't see a flash.
    if (!path || path.length === 0) return
    const target = resolveAtPath(actions, path)
    if (!target) return
    const removed = removeAtPath(actions, path)
    const parentPath = path.slice(0, -1)
    const head = path[path.length - 1]
    const field = head.field as 'steps' | 'then_steps' | 'else_steps' | null
    // For the root segment ``field`` is ``null`` — append to root. For nested segments append
    // to the named field. ``appendAtPath`` returns the array with the new action *at the end*;
    // for a "replace" we want it back at the original index. We splice manually for simplicity.
    const inserted = (() => {
      if (parentPath.length === 0 && head.field == null) {
        const copy = removed.slice()
        copy.splice(head.i, 0, next)
        return copy
      }
      // Walk down to the parent and splice in.
      const recur = (arr: Row[], depth: number): Row[] => {
        const seg = parentPath[depth]
        const ret = arr.slice()
        const cur = ret[seg.i] as Row
        if (depth === parentPath.length - 1) {
          const childField = field as string
          const childArr = (cur[childField] as Row[] | undefined)?.slice() ?? []
          childArr.splice(head.i, 0, next)
          ret[seg.i] = { ...cur, [childField]: childArr }
          return ret
        }
        const childSeg = parentPath[depth + 1]
        if (childSeg.field == null) return ret
        const childArr = (cur[childSeg.field] as Row[] | undefined) ?? []
        ret[seg.i] = { ...cur, [childSeg.field]: recur(childArr, depth + 1) }
        return ret
      }
      return recur(removed, 0)
    })()
    onChange(inserted)
  }
  const removeSelected = async () => {
    if (!path || path.length === 0) return
    const target = resolveAtPath(actions, path)
    if (!target) return
    const ok = await modals.confirm({
      title: t('settings.screens.action.delete'),
      message: t('settings.screens.action.confirmDelete', { id: target.id }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    onChange(removeAtPath(actions, path))
    // Pop the path one level up — the deleted action is gone, focus moves to its parent's
    // sub-list (or back to the root list if the deleted action was top-level).
    onPathChange(path.length > 1 ? path.slice(0, -1) : null)
  }
  const changeSelectedType = (newType: ActionType) => {
    if (!path || path.length === 0) return
    const cur = resolveAtPath(actions, path)
    if (!cur || cur.type === newType) return
    // Preserve id + label + stop_on_error; reset the rest to the new variant's minimum shape.
    const seeded = blankActionOfType(newType, String(cur.id ?? ''))
    if (cur.label != null) seeded.label = cur.label
    if (cur.stop_on_error != null) seeded.stop_on_error = cur.stop_on_error
    replaceSelected(seeded)
  }

  // ── append helpers — used by the "Add step" buttons in the editor's sub-lists ──────────
  const addAtRoot = async () => {
    const id = (await modals.prompt({
      title: t('settings.screens.action.add'),
      message: t('settings.screens.action.namePrompt'),
      validate: (v) => {
        if (!v) return null
        if (actions.some((a) => a.id === v)) return t('settings.screens.action.idExists', { id: v })
        return null
      },
    }))?.trim()
    if (!id) return
    const next = blankActionOfType('run_query', id)
    const newRoot = [...actions, next]
    onChange(newRoot)
    // Auto-select the new action so the operator can fill in its details immediately.
    onPathChange([{ field: null, i: newRoot.length - 1 }])
  }
  const addToSelected = async (field: 'steps' | 'then_steps' | 'else_steps') => {
    if (!path || path.length === 0) return
    const parent = resolveAtPath(actions, path)
    const existingNested = Array.isArray(parent?.[field]) ? (parent![field] as Row[]) : []
    const id = (await modals.prompt({
      title: t('settings.screens.action.add'),
      message: t('settings.screens.action.namePrompt'),
      validate: (v) => {
        if (!v) return null
        if (existingNested.some((a) => a.id === v)) return t('settings.screens.action.idExists', { id: v })
        return null
      },
    }))?.trim()
    if (!id) return
    const next = blankActionOfType('run_query', id)
    onChange(appendAtPath(actions, path, field, next))
    // Auto-dive into the new step.
    onPathChange([...path, { field, i: existingNested.length }])
  }

  // ── connector + target dropdowns for the three ParamBind-bearing variants ──────────────
  // Same logic as ``ActionListEditor.renderActionOverrides``; lifted here to avoid an extra
  // shared module (the function is ~40 LOC, copying is simpler than restructuring).
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
          value: q.name, label: q.description || q.label || q.name, mono: q.name,
        }))
      } else if (aType === 'call_api' && targetConnMeta.type === 'api') {
        targetOpts = targetConnMeta.endpoints.map((e) => ({
          value: e.name, label: e.label || e.name, mono: e.name,
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
            {(aType === 'run_query' || aType === 'navigate') && typeof a[targetKey] === 'string' && a[targetKey] && actionConn && (
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
      </>
    )
  }

  // ── PromptField list — same shape as ActionListEditor's version ────────────────────────
  const renderPromptFields = (action: Row, onPatch: (patch: Row) => void): ReactNode => {
    const aType = String(action.type ?? 'run_query') as ActionType
    if (!PROMPTABLE_ACTION_TYPES.has(aType)) return null
    const fields = Array.isArray(action[PROMPT_FIELDS_KEY]) ? (action[PROMPT_FIELDS_KEY] as Row[]) : []
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
      setPromptFieldOpen(fields.length)
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
      setPromptFieldOpen(promptFieldOpen === idx ? null : promptFieldOpen != null && promptFieldOpen > idx ? promptFieldOpen - 1 : promptFieldOpen)
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
        <SubHead>{t('settings.screens.prompt.heading')}</SubHead>
        <Sub>{t('settings.screens.prompt.hint')}</Sub>
        <FieldList>
          {fields.length === 0 && <Empty>{t('settings.screens.prompt.empty')}</Empty>}
          {fields.map((f, i) => {
            const open = promptFieldOpen === i
            return (
              <div key={i}>
                <FieldHeader $open={open} onClick={() => setPromptFieldOpen(open ? null : i)}>
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

  // ── nested step sub-list (the in-editor click-to-dive list for chain / if / loop) ──────
  // Different from the top-level list: each row's click pushes a new segment onto the parent
  // path (instead of *replacing* the path the way the top-level list does).
  const renderSubList = (
    label: string,
    field: 'steps' | 'then_steps' | 'else_steps',
    list: Row[],
    parentPath: ActionPath,
    emptyMsg: string,
  ): ReactNode => (
    <Stack gap={6}>
      <SubHead>{label}</SubHead>
      <RowBox>
        {list.length === 0 && <Empty>{emptyMsg}</Empty>}
        {list.map((s, i) => {
          const sType = String(s.type ?? 'run_query') as ActionType
          return (
            <RowItem
              key={`${field}-${i}`}
              type="button"
              onClick={() => onPathChange([...parentPath, { field, i }])}
            >
              <span className="lead">{variantIcon(sType)}</span>
              <span className="name">{String(s.id ?? '')}</span>
              <span className="dd">{sType}</span>
              {s.label != null && s.label !== '' && <span className="dd">· {String(s.label)}</span>}
              <span className="trail"><ChevronRight size={13} /></span>
            </RowItem>
          )
        })}
        <Button
          $variant="ghost"
          $size="sm"
          onClick={() => void addToSelected(field)}
          style={{ justifyContent: 'flex-start', alignSelf: 'flex-start' }}
        >
          <Plus size={13} /> {t('settings.screens.action.add')}
        </Button>
      </RowBox>
    </Stack>
  )

  // ── render: list mode ──────────────────────────────────────────────────────────────────
  if (path == null || path.length === 0) {
    return (
      <Stack gap={8}>
        {heading != null && <SubHead>{heading}</SubHead>}
        {hint != null && <Sub>{hint}</Sub>}
        <RowBox>
          {actions.length === 0 && <Empty>{emptyMessage ?? t('settings.screens.action.empty')}</Empty>}
          {actions.map((a, i) => {
            const aType = String(a.type ?? 'run_query') as ActionType
            // Highlight the row whose path-prefix matches ``selectedPath``. We compare just the
            // root segment so the row stays highlighted even when the operator has dived deeper
            // (the breadcrumb in the Inspector shows the current depth).
            const rowPath: ActionPath = [{ field: null, i }]
            const isSelected = selectedPath != null && selectedPath.length > 0
              && pathEquals(selectedPath.slice(0, 1), rowPath)
            return (
              <RowItem
                key={i}
                type="button"
                $active={isSelected}
                onClick={() => onPathChange(rowPath)}
              >
                <span className="lead">{variantIcon(aType)}</span>
                <span className="name">{String(a.id ?? '')}</span>
                <span className="dd">{aType}</span>
                {a.label != null && a.label !== '' && <span className="dd">· {String(a.label)}</span>}
                <span className="trail"><ChevronRight size={13} /></span>
              </RowItem>
            )
          })}
        </RowBox>
        <Button
          $variant="ghost"
          $size="sm"
          onClick={() => void addAtRoot()}
          style={{ justifyContent: 'flex-start', alignSelf: 'flex-start' }}
        >
          <Plus size={13} /> {t('settings.screens.action.add')}
        </Button>
      </Stack>
    )
  }

  // ── render: editor mode (the focused action) ───────────────────────────────────────────
  const selected = resolveAtPath(actions, path)
  if (!selected) {
    // Stale path — the effect above will clear ``path`` on the next tick. Render nothing
    // this frame so we don't crash on missing fields.
    return null
  }
  const sType = String(selected.type ?? 'run_query') as ActionType
  const variantSchema = actionVariantSchema(selected)
  const crumbs = breadcrumbCrumbs(actions, path, rootLabel)

  // For workflow variants, the editor body grows by the matching sub-list (or two for IF).
  // For non-workflow variants, the body ends after the variant SchemaForm / prompt-fields.
  const renderBody = (): ReactNode => {
    if (sType === 'chain') {
      const steps = Array.isArray(selected.steps) ? (selected.steps as Row[]) : []
      return renderSubList(t('settings.screens.chain.heading'), 'steps', steps, path, t('settings.screens.chain.empty'))
    }
    if (sType === 'if') {
      const condition = (selected.condition && typeof selected.condition === 'object'
        ? selected.condition
        : { source: '', operator: 'truthy' }) as Row
      const thenSteps = Array.isArray(selected.then_steps) ? (selected.then_steps as Row[]) : []
      const elseSteps = Array.isArray(selected.else_steps) ? (selected.else_steps as Row[]) : []
      return (
        <Stack gap={14}>
          <Stack gap={6}>
            <SubHead>{t('settings.screens.condition.heading')}</SubHead>
            <Sub>{t('settings.screens.condition.hint')}</Sub>
            {conditionSchema ? (
              <SchemaForm
                schema={conditionSchema}
                defs={defs}
                value={condition}
                onChange={(v) => patchSelected({ condition: v })}
              />
            ) : (
              <Empty>{t('settings.screens.condition.unavailable')}</Empty>
            )}
          </Stack>
          {renderSubList(t('settings.screens.if.thenHeading'), 'then_steps', thenSteps, path, t('settings.screens.if.thenEmpty'))}
          {renderSubList(t('settings.screens.if.elseHeading'), 'else_steps', elseSteps, path, t('settings.screens.if.elseEmpty'))}
        </Stack>
      )
    }
    if (sType === 'loop') {
      const steps = Array.isArray(selected.steps) ? (selected.steps as Row[]) : []
      return (
        <Stack gap={14}>
          <Sub style={{ marginTop: -2 }}>{t('settings.screens.loop.sourceHint')}</Sub>
          {renderSubList(t('settings.screens.loop.bodyHeading'), 'steps', steps, path, t('settings.screens.loop.bodyEmpty'))}
        </Stack>
      )
    }
    return null
  }

  return (
    <Stack gap={12}>
      {/* Breadcrumb — every crumb is clickable; the active one (last) is muted. */}
      <CrumbBar>
        {crumbs.map((c, i) => {
          const isLast = i === crumbs.length - 1
          return (
            <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              {i > 0 && <ChevronRight size={12} style={{ color: colors.text.muted }} />}
              <CrumbBtn
                type="button"
                $active={isLast}
                onClick={() => {
                  if (isLast) return
                  // Empty path means "back to list mode".
                  onPathChange(c.path.length === 0 ? null : c.path)
                }}
              >
                {c.label || '(unnamed)'}
              </CrumbBtn>
            </span>
          )
        })}
      </CrumbBar>
      {/* Editor body — variant SchemaForm + prompt-fields + (for workflow variants) sub-list. */}
      <Stack gap={12}>
        <Field label={t('settings.screens.action.type')}>
          <SearchSelect
            value={sType}
            options={ACTION_TYPES.map((tt) => ({ value: tt, label: tt, mono: tt }))}
            onChange={(v) => changeSelectedType(v as ActionType)}
          />
        </Field>
        {renderActionOverrides(selected, (patch) => patchSelected(patch))}
        {variantSchema ? (
          <SchemaForm
            schema={variantSchema}
            defs={defs}
            value={selected}
            onChange={(v) => {
              // Patch with all editable variant keys (excluding ones rendered above as custom
              // widgets); SchemaForm gives us back the full object minus stripped keys.
              patchSelected(v)
            }}
          />
        ) : (
          <Empty>{t('settings.screens.action.unknownType', { type: sType })}</Empty>
        )}
        {renderPromptFields(selected, (patch) => patchSelected(patch))}
        {renderBody()}
        <Row gap={8}>
          <Button $variant="danger" $size="sm" onClick={() => void removeSelected()}>
            <Trash2 size={13} /> {t('settings.screens.action.delete')}
          </Button>
        </Row>
      </Stack>
    </Stack>
  )
}

// Re-export the path helpers so the consumer (ScreenVisualBuilder) can use them without a
// separate import — they go hand-in-hand with this component.
export { pathEquals } from './actionPath'
export type { ActionPath } from './actionPath'
