// Custom editor for one Screen — replaces the generic SchemaNavigator with a tabbed view that
// surfaces the dialog structure (tabs / fields / lookup_param_binds) at a glance, addressing the
// "all on one page, hard to understand" feedback after slice 2 landed.
//
// Tabs across the top: General · Queries · Dialog · Actions · Row menu.
// - General / Queries use SchemaForm over picked subsets of the Screen schema, so the same
//   field-level enums, descriptions and validation as the form view kick in.
// - Dialog uses a custom layout: a vertical strip of dialog tabs on the left, the selected tab's
//   properties + a list of fields on the right. Each field row is an expandable accordion (no
//   drill-down breadcrumbs). lookup_param_binds nest inline under the expanded field.
// - Actions / Row menu show a "Coming in slice 4/6" placeholder so the UI shape is stable when
//   those slices land.
//
// All mutations go through `onChange(nextScreen)` — the parent (ScreensBuilder) owns the dirty
// flag and the save call. `screenSchema` carries the full `$defs` map so we can pick out
// `ScreenDialog` / `ScreenTab` / `ScreenField` / `ParamBind` for the per-section sub-schemas.
import { useMemo, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, FileText, Layers, Plus, Trash2 } from 'lucide-react'
import { Button, Row, SchemaForm, Stack, type JsonSchema } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import { pickSchemaProperties } from './connectorTables'

type Row = Record<string, unknown>

// Which top-level Screen properties belong on which inner tab.
const GENERAL_KEYS = ['label', 'description', 'connector', 'audit', 'auto_load', 'editable', 'uploadable'] as const
const QUERY_KEYS = ['read_query', 'update_query', 'insert_query', 'delete_query'] as const

type TabKey = 'general' | 'queries' | 'dialog' | 'actions' | 'rowmenu'
const TAB_ORDER: TabKey[] = ['general', 'queries', 'dialog', 'actions', 'rowmenu']

// ── styled bits ─────────────────────────────────────────────────────────────
const TabsBar = styled.div`display: flex; gap: 4px; border-bottom: 1px solid ${colors.border}; margin-bottom: 14px;`
const TabBtn = styled.button<{ $active?: boolean }>`
  height: 34px; padding: 0 14px; border: none; border-bottom: 2px solid transparent; background: transparent;
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  border-bottom-color: ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  font-weight: ${({ $active }) => ($active ? 600 : 400)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; }
`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; line-height: 1.5; margin-bottom: 10px;`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 24px 4px; text-align: center;`

// Dialog tab: a vertical strip of dialog tabs on the left + the selected tab's body on the right.
const DialogSplit = styled.div`display: flex; gap: 14px; align-items: flex-start; min-height: 320px;`
const TabsCol = styled.div`flex: 0 0 180px; display: flex; flex-direction: column; gap: 4px; max-height: calc(100dvh - 28rem); overflow-y: auto;`
const TabRow = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 7px; padding: 7px 10px; border-radius: ${radius.md}; text-align: left;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer;
  & .id { font-family: ${fonts.mono}; color: ${colors.text.muted}; margin-left: auto; font-size: ${fontSize.micro}; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const TabBody = styled.div`flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 14px;`
const FieldList = styled.div`display: flex; flex-direction: column; gap: 6px;`
const FieldHeader = styled.button<{ $open?: boolean }>`
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 8px 12px; text-align: left;
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; cursor: pointer;
  border-radius: ${({ $open }) => ($open ? `${radius.md} ${radius.md} 0 0` : radius.md)};
  border-bottom-color: ${({ $open }) => ($open ? colors.blue.border : colors.border)};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  & .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .dd { font-family: ${fonts.mono}; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  & .badges { display: inline-flex; gap: 4px; }
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  &:hover { border-color: ${colors.blue.border}; }
`
const Badge = styled.span<{ $tone?: 'orange' | 'red' | 'muted' }>`
  display: inline-block; padding: 1px 6px; border-radius: ${radius.sm}; font-size: ${fontSize.micro}; font-family: ${fonts.sans};
  border: 1px solid ${({ $tone }) => ($tone === 'orange' ? colors.orange.border : $tone === 'red' ? colors.red.border : colors.border)};
  color: ${({ $tone }) => ($tone === 'orange' ? colors.orange.main : $tone === 'red' ? colors.red.main : colors.text.muted)};
  background: ${({ $tone }) => ($tone === 'orange' ? colors.orange.bg : $tone === 'red' ? colors.red.bg : 'transparent')};
`
const FieldBody = styled.div`
  border: 1px solid ${colors.blue.border}; border-top: none; padding: 12px;
  border-radius: 0 0 ${radius.md} ${radius.md}; background: ${colors.bg.input};
`
const PlaceholderCard = styled.div`
  padding: 24px; border: 1px dashed ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; text-align: center; line-height: 1.6;
`

// Sub-schemas used inside Dialog. Picked once per screenSchema change.
const TAB_PROPS_KEYS = ['label', 'l', 'cols', 'hide_on_add', 'hide_on_edit'] as const
const FIELD_PROPS_KEYS = ['dd', 'label', 'hidden', 'disabled', 'required', 'colspan', 'default'] as const
const FIELD_BINDS_KEY = 'lookup_param_binds'
const FIELD_CONDITION_KEYS = ['visible_when', 'required_when', 'disabled_when'] as const

function pickFromDefs(defs: Record<string, JsonSchema>, name: string): JsonSchema {
  // Return the named $def as a self-contained schema (the parent's $defs ride along so nested
  // refs — ParamBind under ScreenField — still resolve when SchemaForm walks them).
  const base = defs[name] ?? { type: 'object' }
  return { ...base, $defs: defs }
}

export interface ScreenEditorProps {
  /** Selected screen path — for the optional breadcrumb. */
  app: string
  id: string
  /** The Screen as a JSON-compatible dict (matching the Screen Pydantic shape's fields). */
  value: Row
  /** The Screen JSON schema (the one carrying all the $defs for ScreenDialog/Tab/Field/ParamBind). */
  schema: JsonSchema
  /** Called whenever any field changes — the parent (ScreensBuilder) owns the dirty flag. */
  onChange: (next: Row) => void
}

export default function ScreenEditor({ app, id, value, schema, onChange }: ScreenEditorProps) {
  const { t } = useTranslation()
  const [tab, setTab] = useState<TabKey>('general')

  const defs = (schema.$defs ?? {}) as Record<string, JsonSchema>
  // Pre-pick the per-tab sub-schemas; the General/Queries tabs use a SchemaForm over these
  // so the field-level enums + descriptions still drive each input.
  const generalSchema = useMemo<JsonSchema>(() => pickSchemaProperties(schema, GENERAL_KEYS as unknown as string[]), [schema])
  const queriesSchema = useMemo<JsonSchema>(() => pickSchemaProperties(schema, QUERY_KEYS as unknown as string[]), [schema])
  const tabPropsSchema = useMemo<JsonSchema>(() => pickSchemaProperties(pickFromDefs(defs, 'ScreenTab'), TAB_PROPS_KEYS as unknown as string[]), [defs])
  const fieldPropsSchema = useMemo<JsonSchema>(() => pickSchemaProperties(pickFromDefs(defs, 'ScreenField'), FIELD_PROPS_KEYS as unknown as string[]), [defs])
  // Field's lookup_param_binds — pick that one property so SchemaForm inlines its list[ParamBind] editor.
  const bindsSchema = useMemo<JsonSchema>(() => {
    const sf = pickFromDefs(defs, 'ScreenField')
    const out = pickSchemaProperties(sf, [FIELD_BINDS_KEY] as unknown as string[])
    return { ...out, $defs: defs }
  }, [defs])
  // Field's per-field conditions (visible_when / required_when / disabled_when). Same trick as
  // bindsSchema — the three list[FieldCondition] props render as inline editors with `$defs`
  // resolving FieldCondition. Operators see all three side-by-side under the expanded field row.
  const conditionsSchema = useMemo<JsonSchema>(() => {
    const sf = pickFromDefs(defs, 'ScreenField')
    const out = pickSchemaProperties(sf, FIELD_CONDITION_KEYS as unknown as string[])
    return { ...out, $defs: defs }
  }, [defs])

  // --- mutation helpers (all rebuild the screen value and call onChange) -------
  const setProp = (k: string, v: unknown) => {
    const next = { ...value }
    if (v === undefined || v === null || v === '' || (Array.isArray(v) && v.length === 0)) delete next[k]
    else next[k] = v
    onChange(next)
  }
  const dialog = (value.dialog && typeof value.dialog === 'object' ? value.dialog : null) as { title?: string; tabs?: Row[] } | null
  const setDialog = (next: { title?: string; tabs?: Row[] } | null) => {
    if (!next || (!next.title && (!next.tabs || next.tabs.length === 0))) {
      const v = { ...value }; delete v.dialog
      onChange(v)
    } else {
      onChange({ ...value, dialog: next })
    }
  }
  const dialogTabs = useMemo<Row[]>(() => (Array.isArray(dialog?.tabs) ? dialog!.tabs : []), [dialog])

  const [selTabIdx, setSelTabIdx] = useState(0)
  const [expandedField, setExpandedField] = useState<number | null>(null)
  const updateTab = (idx: number, patch: Row) => {
    const next = dialogTabs.slice()
    next[idx] = { ...next[idx], ...patch }
    setDialog({ ...(dialog ?? {}), tabs: next })
  }
  const renameTab = (idx: number) => {
    const cur = String((dialogTabs[idx] ?? {}).id ?? '')
    const v = window.prompt(t('settings.screens.tab.renamePrompt'), cur)?.trim()
    if (!v || v === cur) return
    if (dialogTabs.some((tab, i) => i !== idx && tab.id === v)) {
      window.alert(t('settings.screens.tab.idExists', { id: v })); return
    }
    updateTab(idx, { id: v })
  }
  const addTab = () => {
    const id = window.prompt(t('settings.screens.tab.namePrompt'))?.trim()
    if (!id) return
    if (dialogTabs.some((t) => t.id === id)) { window.alert(t('settings.screens.tab.idExists', { id })); return }
    const next = [...dialogTabs, { id, fields: [] }]
    setDialog({ ...(dialog ?? {}), tabs: next })
    setSelTabIdx(next.length - 1)
  }
  const deleteTab = (idx: number) => {
    if (!window.confirm(t('settings.screens.tab.confirmDelete', { id: dialogTabs[idx]?.id }))) return
    const next = dialogTabs.slice(); next.splice(idx, 1)
    setDialog({ ...(dialog ?? {}), tabs: next })
    setSelTabIdx((cur) => Math.min(cur, Math.max(0, next.length - 1)))
  }
  const createDialog = () => {
    setDialog({ tabs: [{ id: 'general', label: 'General', fields: [] }] })
    setSelTabIdx(0)
  }

  // --- field-level mutations -------------------------------------------------
  const selTab = dialogTabs[selTabIdx]
  const fields: Row[] = useMemo(() => (Array.isArray(selTab?.fields) ? (selTab!.fields as Row[]) : []), [selTab])
  const updateFields = (next: Row[]) => updateTab(selTabIdx, { fields: next })
  const addField = () => {
    const name = window.prompt(t('settings.screens.field.namePrompt'))?.trim()
    if (!name) return
    if (fields.some((f) => f.name === name)) { window.alert(t('settings.screens.field.nameExists', { name })); return }
    updateFields([...fields, { name }])
    setExpandedField(fields.length)
  }
  const deleteField = (idx: number) => {
    if (!window.confirm(t('settings.screens.field.confirmDelete', { name: fields[idx]?.name }))) return
    const next = fields.slice(); next.splice(idx, 1)
    updateFields(next)
    setExpandedField((cur) => (cur === idx ? null : cur != null && cur > idx ? cur - 1 : cur))
  }
  const updateField = (idx: number, patch: Row) => {
    const next = fields.slice()
    next[idx] = { ...next[idx], ...patch }
    // Drop empty optional keys so the saved TOML stays terse.
    for (const k of Object.keys(patch)) {
      if (patch[k] === undefined || patch[k] === null || patch[k] === '' || patch[k] === false) delete (next[idx] as Row)[k]
    }
    updateFields(next)
  }

  // --- render ----------------------------------------------------------------
  const renderGeneral = (): ReactNode => (
    <>
      <Sub>{t('settings.screens.editor.generalHint')}</Sub>
      <SchemaForm
        schema={generalSchema}
        defs={defs}
        value={value}
        onChange={(v) => {
          // SchemaForm gives the full subset — convert to a patch so we don't clobber the keys
          // we didn't include (dialog, actions, row_menu, id, read_query/…).
          const patch: Row = {}
          for (const k of GENERAL_KEYS) patch[k] = v[k]
          for (const [k, val] of Object.entries(patch)) setProp(k, val)
        }}
      />
    </>
  )

  const renderQueries = (): ReactNode => (
    <>
      <Sub>{t('settings.screens.editor.queriesHint')}</Sub>
      <SchemaForm
        schema={queriesSchema}
        defs={defs}
        value={value}
        onChange={(v) => {
          for (const k of QUERY_KEYS) setProp(k, v[k])
        }}
      />
    </>
  )

  const renderDialog = (): ReactNode => {
    if (!dialog) {
      return (
        <Stack gap={14}>
          <Sub>{t('settings.screens.editor.dialogHint')}</Sub>
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
    return (
      <Stack gap={10}>
        <Sub>{t('settings.screens.editor.dialogHint')}</Sub>
        {/* Top-level dialog props — currently just `title`; kept compact above the split. */}
        <Row gap={10} style={{ alignItems: 'flex-end' }}>
          <SchemaForm
            schema={{ type: 'object', properties: { title: { type: 'string', title: 'Dialog title' } } }}
            defs={defs}
            value={{ title: dialog.title ?? '' }}
            onChange={(v) => setDialog({ ...(dialog ?? {}), title: typeof v.title === 'string' ? v.title : undefined })}
          />
        </Row>
        <DialogSplit>
          <TabsCol>
            {dialogTabs.length === 0 && <Empty>{t('settings.screens.tab.empty')}</Empty>}
            {dialogTabs.map((tab, i) => {
              const tabId = String(tab.id ?? `tab_${i}`)
              const tabLabel = typeof tab.label === 'string' && tab.label ? tab.label : tabId
              const fcount = Array.isArray(tab.fields) ? tab.fields.length : 0
              return (
                <TabRow key={tabId + i} $active={i === selTabIdx} onClick={() => { setSelTabIdx(i); setExpandedField(null) }}>
                  <Layers size={13} />
                  <span className="lbl">{tabLabel}</span>
                  <span className="id">· {fcount}</span>
                </TabRow>
              )
            })}
            <Button $variant="ghost" $size="sm" onClick={addTab} style={{ justifyContent: 'flex-start', marginTop: 6 }}>
              <Plus size={13} /> {t('settings.screens.tab.add')}
            </Button>
          </TabsCol>
          <TabBody>
            {selTab ? (
              <>
                <Row gap={8} style={{ justifyContent: 'space-between', alignItems: 'center' }}>
                  <strong style={{ fontFamily: fonts.mono, color: colors.text.primary }}>
                    <code>{String(selTab.id)}</code>
                  </strong>
                  <Row gap={6}>
                    <Button $variant="ghost" $size="sm" onClick={() => renameTab(selTabIdx)}>
                      {t('settings.screens.tab.rename')}
                    </Button>
                    <Button $variant="danger" $size="sm" onClick={() => deleteTab(selTabIdx)}>
                      <Trash2 size={13} /> {t('settings.screens.tab.delete')}
                    </Button>
                  </Row>
                </Row>
                <SchemaForm
                  schema={tabPropsSchema}
                  defs={defs}
                  value={selTab}
                  onChange={(v) => {
                    const patch: Row = {}
                    for (const k of TAB_PROPS_KEYS) patch[k] = v[k]
                    updateTab(selTabIdx, patch)
                  }}
                />
                <strong style={{ color: colors.text.primary, fontSize: fontSize.sm }}>
                  {t('settings.screens.field.heading')}
                </strong>
                <FieldList>
                  {fields.length === 0 && <Empty>{t('settings.screens.field.empty')}</Empty>}
                  {fields.map((f, i) => {
                    const open = expandedField === i
                    const required = !!f.required
                    const hidden = !!f.hidden
                    const disabled = !!f.disabled
                    return (
                      <div key={i}>
                        <FieldHeader $open={open} onClick={() => setExpandedField(open ? null : i)}>
                          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                          <FileText size={13} />
                          <span className="name">{String(f.name ?? '')}</span>
                          {f.dd != null && f.dd !== '' && <span className="dd">dd:{String(f.dd)}</span>}
                          <span className="badges">
                            {required && <Badge $tone="orange">{t('settings.screens.field.required')}</Badge>}
                            {hidden && <Badge $tone="muted">{t('settings.screens.field.hidden')}</Badge>}
                            {disabled && <Badge $tone="muted">{t('settings.screens.field.disabled')}</Badge>}
                            {Array.isArray(f.lookup_param_binds) && (f.lookup_param_binds as unknown[]).length > 0 && (
                              <Badge>{t('settings.screens.field.binds', { count: (f.lookup_param_binds as unknown[]).length })}</Badge>
                            )}
                            {(Array.isArray(f.visible_when) && (f.visible_when as unknown[]).length > 0)
                              || (Array.isArray(f.required_when) && (f.required_when as unknown[]).length > 0)
                              || (Array.isArray(f.disabled_when) && (f.disabled_when as unknown[]).length > 0)
                              ? <Badge $tone="orange">{t('settings.screens.field.conditional')}</Badge>
                              : null}
                          </span>
                        </FieldHeader>
                        {open && (
                          <FieldBody>
                            <Stack gap={12}>
                              <SchemaForm
                                schema={fieldPropsSchema}
                                defs={defs}
                                value={f}
                                onChange={(v) => {
                                  const patch: Row = {}
                                  for (const k of FIELD_PROPS_KEYS) patch[k] = v[k]
                                  updateField(i, patch)
                                }}
                              />
                              <SchemaForm
                                schema={bindsSchema}
                                defs={defs}
                                value={f}
                                onChange={(v) => {
                                  // SchemaForm wraps the picked subset back into an object; pull
                                  // out just the binds key so we don't accidentally write `name`/etc.
                                  updateField(i, { [FIELD_BINDS_KEY]: v[FIELD_BINDS_KEY] })
                                }}
                              />
                              <SchemaForm
                                schema={conditionsSchema}
                                defs={defs}
                                value={f}
                                onChange={(v) => {
                                  // Same trick as bindsSchema — pick only the three condition keys
                                  // so static name/dd/etc. on the field aren't mass-overwritten.
                                  const patch: Row = {}
                                  for (const k of FIELD_CONDITION_KEYS) patch[k] = v[k]
                                  updateField(i, patch)
                                }}
                              />
                              <Row gap={8}>
                                <Button $variant="danger" $size="sm" onClick={() => deleteField(i)}>
                                  <Trash2 size={13} /> {t('settings.screens.field.delete')}
                                </Button>
                              </Row>
                            </Stack>
                          </FieldBody>
                        )}
                      </div>
                    )
                  })}
                </FieldList>
                <Button $variant="ghost" $size="sm" onClick={addField} style={{ justifyContent: 'flex-start', alignSelf: 'flex-start' }}>
                  <Plus size={13} /> {t('settings.screens.field.add')}
                </Button>
              </>
            ) : (
              <Empty>{t('settings.screens.tab.pickOne')}</Empty>
            )}
          </TabBody>
        </DialogSplit>
      </Stack>
    )
  }

  const renderPlaceholder = (sliceName: string): ReactNode => (
    <PlaceholderCard>
      <strong style={{ display: 'block', marginBottom: 8, color: colors.text.primary }}>
        {t('settings.screens.editor.comingSoon')}
      </strong>
      {t('settings.screens.editor.comingSoonHint', { slice: sliceName })}
    </PlaceholderCard>
  )

  const renderTab = (): ReactNode => {
    switch (tab) {
      case 'general': return renderGeneral()
      case 'queries': return renderQueries()
      case 'dialog':  return renderDialog()
      case 'actions': return renderPlaceholder(t('settings.screens.editor.sliceActions'))
      case 'rowmenu': return renderPlaceholder(t('settings.screens.editor.sliceRowMenu'))
    }
  }

  return (
    <Stack gap={10}>
      <div style={{ color: colors.text.muted, fontSize: fontSize.micro, fontFamily: fonts.mono }}>
        [screens.{app}.{id}]
      </div>
      <TabsBar>
        {TAB_ORDER.map((k) => (
          <TabBtn key={k} type="button" $active={tab === k} onClick={() => setTab(k)}>
            {t(`settings.screens.editor.tabs.${k}`)}
          </TabBtn>
        ))}
      </TabsBar>
      {renderTab()}
    </Stack>
  )
}
