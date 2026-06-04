// Editor for a form tab's ``nested_forms`` — embedded child-record forms rendered as labelled
// sections below the tab's own fields, so one tab edits the main table PLUS related tables and
// saves them all in one pass (Part B). Each embedded form is a full nested_form: either INLINE
// (its own connector + read/update/insert queries + a picked field list) or a REFERENCE to an
// existing screen (``form_screen`` — reuse its queries + fields). ``param_binds`` bind the parent's
// PK into the child's read/write queries.
//
// Kept as a dedicated component (not inlined into ScreenVisualBuilder) because each card owns its
// own read-query column fetch — for the field picker and the bind's target-param suggestions.
import { useContext, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, ChevronUp, Edit3, Plus, Settings2, Trash2 } from 'lucide-react'
import {
  Button, Field, FrameworkEnumsContext, Input, SchemaForm, SearchSelect,
  type FrameworkEnums, type JsonSchema, type SearchSelectOption,
} from '../../common'
import ParamBindList, { type ParamBind } from './ParamBindList'
import { builtinSourceOptions, mergeCandidates, targetParamOptions } from './actionCandidates'
import { pickSchemaProperties } from './connectorTables'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { api } from '../../api/client'
import type { Column, QueryResult } from '../../types/connectors'
import { colors, fontSize, fonts, radius } from '../../theme'

// The ScreenField properties an embedded-form field exposes — the same set the main field
// inspector edits. ``dd`` is the important one: it links the field to a dictionary entry so the
// runtime resolves its rule (BOOLEAN / ENUM / LOOKUP); without it dictionary rules don't apply.
const FIELD_PROP_KEYS = [
  'dd', 'label', 'format', 'hidden', 'disabled', 'required', 'colspan', 'default',
  'lookup_param_binds', 'visible_when', 'required_when', 'disabled_when',
] as const

type Row = Record<string, unknown>

const Card = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  padding: 10px 12px; margin-bottom: 10px;
`
const CardHead = styled.div`
  display: flex; align-items: center; gap: 8px; margin-bottom: 8px;
  & .grow { flex: 1; min-width: 0; }
`
const Toggle = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px;
  border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; border-radius: ${radius.sm};
  &:hover { color: ${colors.text.primary}; background: var(--hover-subtle); }
`
const RemoveBtn = styled.button`
  height: 28px; width: 28px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid transparent; background: transparent; color: ${colors.text.muted};
  border-radius: ${radius.sm}; cursor: pointer;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; background: ${colors.red.bg}; }
`
const Two = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 10px;`
const FieldRowLine = styled.div`
  display: flex; align-items: center; gap: 6px; padding: 3px 0;
  & .name { flex: 1; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .lbl { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const IconBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 22px; height: 22px;
  border: none; background: transparent; color: ${colors.text.muted}; cursor: pointer; border-radius: ${radius.sm};
  &:hover:not(:disabled) { color: ${colors.text.primary}; background: var(--hover-subtle); }
  &:disabled { opacity: 0.3; cursor: default; }
`
const Hint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro}; line-height: 1.4; margin: 4px 0 8px;`
// The expanded per-field property inspector (the ScreenField SchemaForm), inset under its row.
const FieldProps = styled.div`
  margin: 2px 0 8px 12px; padding: 8px 10px; border-left: 2px solid ${colors.border};
  background: ${colors.bg.card}; border-radius: ${radius.sm};
`
const SubLabel = styled.div`
  font-size: ${fontSize.micro}; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
  color: ${colors.text.muted}; margin: 10px 0 4px;
`

interface EmbeddedFormsEditorProps {
  /** The form tab's ``nested_forms`` list. */
  value: Row[]
  onChange: (next: Row[]) => void
  /** Parent screen app — for the reuse-screen lookup + resolving a referenced screen's read query. */
  app: string
  /** Parent screen's effective connector — the default for an embedded form that doesn't override it. */
  parentConnector: string
  /** Bind SOURCE candidates — the PARENT screen's read columns (+ builtins are added here). */
  parentColumnOptions: SearchSelectOption[]
  /** Same-app screens — the reuse (``form_screen``) picker options. */
  screenOptions: SearchSelectOption[]
  /** Raise the shared EditQueryModal for a query the operator wants to tweak in place. */
  onEditQuery: (connector: string, queryName: string) => void
  /** Schema ``$defs`` (carries ScreenField) — drives each field's property inspector. */
  defs: Record<string, JsonSchema>
}

export default function EmbeddedFormsEditor({
  value, onChange, app, parentConnector, parentColumnOptions, screenOptions, onEditQuery, defs,
}: EmbeddedFormsEditorProps) {
  const { t } = useTranslation()
  const forms = Array.isArray(value) ? value : []

  const patch = (idx: number, p: Row) => {
    const next = forms.slice()
    const cur = { ...next[idx], ...p }
    for (const k of Object.keys(p)) {
      const v = (cur as Row)[k]
      if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) delete (cur as Row)[k]
    }
    next[idx] = cur
    onChange(next)
  }
  const add = () => {
    // Seed a fresh inline embedded form. A unique-ish id from the count keeps the saver registry
    // keys distinct; the operator can rename via the label (id stays stable as the registry key).
    const ids = new Set(forms.map((f) => String(f.id ?? '')))
    let n = forms.length + 1
    let id = `nested_${n}`
    while (ids.has(id)) { n += 1; id = `nested_${n}` }
    onChange([...forms, { id, type: 'nested_form', read_query: '', fields: [], param_binds: [] }])
  }
  const remove = (idx: number) => {
    const next = forms.slice(); next.splice(idx, 1); onChange(next)
  }

  return (
    <div>
      {forms.map((f, i) => (
        <EmbeddedFormCard
          key={String(f.id ?? i)}
          form={f}
          app={app}
          parentConnector={parentConnector}
          parentColumnOptions={parentColumnOptions}
          screenOptions={screenOptions}
          onEditQuery={onEditQuery}
          defs={defs}
          onPatch={(p) => patch(i, p)}
          onRemove={() => remove(i)}
        />
      ))}
      <Button $variant="ghost" $size="sm" onClick={add} style={{ alignSelf: 'flex-start' }}>
        <Plus size={13} /> {t('settings.screens.visual.embedded.add', 'Add embedded form')}
      </Button>
    </div>
  )
}

function EmbeddedFormCard({
  form, app, parentConnector, parentColumnOptions, screenOptions, onEditQuery, defs, onPatch, onRemove,
}: {
  form: Row
  app: string
  parentConnector: string
  parentColumnOptions: SearchSelectOption[]
  screenOptions: SearchSelectOption[]
  onEditQuery: (connector: string, queryName: string) => void
  defs: Record<string, JsonSchema>
  onPatch: (p: Row) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const { connectors: wsConnectors, findScreenById } = useWorkspace()
  const ambientEnums = useContext(FrameworkEnumsContext)
  const [open, setOpen] = useState(true)
  // Which field's property inspector is expanded (by index), if any.
  const [editFieldIdx, setEditFieldIdx] = useState<number | null>(null)

  const isRef = typeof form.form_screen === 'string' && !!form.form_screen
  const connector = (typeof form.connector === 'string' && form.connector.trim() ? form.connector : parentConnector)

  // Connector + query option lists, scoped to the embedded form's effective connector.
  const sqlConnectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? []).filter((c) => c.type === 'sql')
      .map((c) => ({ value: c.name, label: c.name, mono: c.name }))
      .sort((a, b) => a.value.localeCompare(b.value)),
    [wsConnectors],
  )
  const queryOptions = useMemo<SearchSelectOption[]>(() => {
    const meta = (wsConnectors ?? []).find((c) => c.name === connector)
    if (!meta || meta.type !== 'sql') return []
    return meta.queries.map((q) => ({ value: q.name, label: q.description || q.label || q.name, mono: q.name }))
  }, [wsConnectors, connector])

  // The read query whose columns drive the field picker + the bind target-params. Inline → the
  // form's own read_query on ``connector``; reference → the referenced screen's read query.
  const refScreen = isRef ? findScreenById(app, String(form.form_screen)) : null
  const effReadQuery = isRef ? (refScreen?.read_query ?? '') : (typeof form.read_query === 'string' ? form.read_query : '')
  const effConnector = isRef ? (refScreen?.connector || connector) : connector

  // Fetch the read query's columns (for the field picker). Best-effort; empty = picker disabled.
  const [cols, setCols] = useState<Column[]>([])
  useEffect(() => {
    setCols([])
    if (!effConnector || !effReadQuery) return
    let cancelled = false
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(effConnector)}/${encodeURIComponent(effReadQuery)}?_limit=0`)
      .then((r) => { if (!cancelled) setCols(r.columns) })
      .catch(() => { /* silent — picker just stays empty */ })
    return () => { cancelled = true }
  }, [effConnector, effReadQuery])

  // Bind target-params = the embedded read query's :placeholders (declared + scanned).
  const paramOptions = useMemo<SearchSelectOption[]>(
    () => (effReadQuery
      ? targetParamOptions({ type: 'navigate', to: effReadQuery, connector: effConnector }, wsConnectors, effConnector)
      : []),
    [effReadQuery, effConnector, wsConnectors],
  )
  const sourceOptions = useMemo<SearchSelectOption[]>(
    () => mergeCandidates(parentColumnOptions, builtinSourceOptions()),
    [parentColumnOptions],
  )

  const fields = Array.isArray(form.fields) ? (form.fields as Row[]) : []
  const fieldNames = new Set(fields.map((x) => String(x.name ?? '').toLowerCase()))
  const colOptions = useMemo<SearchSelectOption[]>(
    () => cols
      .filter((c) => !fieldNames.has(c.name.toLowerCase()))
      .map((c) => ({ value: c.name.toUpperCase(), label: c.label || '', mono: c.name.toUpperCase() })),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [cols, fields.length],
  )
  const setFields = (next: Row[]) => onPatch({ fields: next })
  const addField = (name: string) => {
    if (!name || fieldNames.has(name.toLowerCase())) return
    setFields([...fields, { name }])
  }
  const removeField = (idx: number) => {
    const n = fields.slice(); n.splice(idx, 1); setFields(n)
    setEditFieldIdx((cur) => (cur === idx ? null : cur != null && cur > idx ? cur - 1 : cur))
  }
  const moveField = (idx: number, dir: -1 | 1) => {
    const j = idx + dir
    if (j < 0 || j >= fields.length) return
    const n = fields.slice(); const [m] = n.splice(idx, 1); n.splice(j, 0, m); setFields(n)
    setEditFieldIdx(null)
  }
  // Per-field property inspector — the SAME ScreenField properties the main field editor exposes
  // (dd / label / format / rules / hidden / conditions / binds). Editing ``dd`` is what makes the
  // runtime resolve the field's dictionary rule (BOOLEAN / ENUM / LOOKUP).
  const fieldPropsSchema = useMemo<JsonSchema>(
    () => ({ ...pickSchemaProperties((defs.ScreenField as JsonSchema) ?? { type: 'object' }, FIELD_PROP_KEYS as unknown as string[]), $defs: defs }),
    [defs],
  )
  // Enums for the field inspector: the ambient framework enums (so ``dd`` resolves against the
  // dictionary) PLUS SCREEN_COLUMNS rebased to THIS embedded form's columns (so condition pickers
  // / column refs point at the right query).
  const fieldEnums = useMemo<FrameworkEnums>(() => ({
    ...(ambientEnums ?? {}),
    SCREEN_COLUMNS: {
      label: 'Columns',
      values: cols.map((c) => ({ value: c.name.toUpperCase(), label: c.label || c.name, mono: c.name.toUpperCase() })),
    },
  }), [ambientEnums, cols])
  const updateFieldProps = (idx: number, v: Row) => {
    const cur = { ...(fields[idx] ?? {}) }
    for (const k of FIELD_PROP_KEYS) {
      const nv = v[k]
      if (nv == null || nv === '' || (Array.isArray(nv) && nv.length === 0)) delete cur[k]
      else cur[k] = nv
    }
    setFields(fields.map((f, i) => (i === idx ? cur : f)))
  }

  const renderQueryPicker = (key: 'read_query' | 'update_query' | 'insert_query', required: boolean) => {
    const val = typeof form[key] === 'string' ? (form[key] as string) : ''
    return (
      <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <SearchSelect
            value={val}
            options={queryOptions}
            onChange={(v) => onPatch({ [key]: v || (required ? '' : null) })}
            anyLabel={required ? undefined : t('common.none')}
            placeholder={t('common.pick')}
          />
        </div>
        {val && (
          <Button $variant="ghost" $size="sm" onClick={() => onEditQuery(connector, val)} title={t('settings.editQuery.edit', 'Edit query')}>
            <Edit3 size={13} />
          </Button>
        )}
      </div>
    )
  }

  return (
    <Card>
      <CardHead>
        <Toggle type="button" onClick={() => setOpen((o) => !o)}>
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </Toggle>
        <div className="grow">
          <Input
            value={(form.label as string | undefined) ?? ''}
            onChange={(e) => onPatch({ label: e.target.value })}
            placeholder={t('settings.screens.visual.embedded.labelPlaceholder', 'Section label (e.g. JD Edwards)')}
          />
        </div>
        <RemoveBtn type="button" onClick={onRemove} title={t('common.remove')}><Trash2 size={14} /></RemoveBtn>
      </CardHead>
      {open && (
        <div>
          <Field label={t('settings.screens.visual.tabSettings.reuseScreen', 'Reuse a screen')}>
            <SearchSelect
              value={(form.form_screen as string | undefined) ?? ''}
              options={screenOptions}
              onChange={(v) => onPatch({ form_screen: v || null })}
              anyLabel={t('settings.screens.visual.tabSettings.reuseScreenInline', '(inline — configure the form below)')}
              placeholder={t('settings.screens.visual.tabSettings.reuseScreenInline', '(inline — configure the form below)')}
            />
          </Field>
          {isRef ? (
            <Hint>{t('settings.screens.visual.embedded.refHint',
              'Reuses the "{{screen}}" screen\'s form (queries + fields). Only the parent-value bindings below are set here.',
              { screen: String(form.form_screen) })}</Hint>
          ) : (
            <>
              <Two>
                <Field label={t('settings.screens.visual.tabSettings.connector')}>
                  <SearchSelect
                    value={(form.connector as string | undefined) ?? ''}
                    options={sqlConnectorOptions}
                    onChange={(v) => onPatch({ connector: v && v !== parentConnector ? v : null })}
                    anyLabel={t('settings.screens.editor.connectorUseApp', { app: parentConnector })}
                    placeholder={parentConnector}
                  />
                </Field>
                <Field label={`${t('settings.screens.editor.queries.read_query')} *`}>
                  {renderQueryPicker('read_query', true)}
                </Field>
              </Two>
              <Two>
                <Field label={t('settings.screens.editor.queries.update_query')}>
                  {renderQueryPicker('update_query', false)}
                </Field>
                <Field label={t('settings.screens.editor.queries.insert_query')}>
                  {renderQueryPicker('insert_query', false)}
                </Field>
              </Two>
            </>
          )}

          <SubLabel>{t('settings.screens.visual.tabSettings.paramBinds', 'Parameter bindings')}</SubLabel>
          <ParamBindList
            value={Array.isArray(form.param_binds) ? (form.param_binds as ParamBind[]) : []}
            onChange={(next) => onPatch({ param_binds: next })}
            sourceOptions={sourceOptions}
            paramOptions={paramOptions}
          />

          {!isRef && (
            <>
              <SubLabel>{t('settings.screens.visual.embedded.fields', 'Fields')}</SubLabel>
              {fields.length === 0 && (
                <Hint>{t('settings.screens.visual.embedded.fieldsEmpty', 'No fields yet — add columns from the read query below.')}</Hint>
              )}
              {fields.map((f, i) => (
                <div key={`${String(f.name)}_${i}`}>
                  <FieldRowLine>
                    <span className="name">{String(f.name)}</span>
                    {typeof f.dd === 'string' && f.dd && f.dd !== f.name && <span className="lbl">dd: {String(f.dd)}</span>}
                    <IconBtn type="button" onClick={() => setEditFieldIdx((cur) => (cur === i ? null : i))}
                      title={t('settings.screens.visual.embedded.fieldProps', 'Properties')}><Settings2 size={13} /></IconBtn>
                    <IconBtn type="button" disabled={i === 0} onClick={() => moveField(i, -1)} title={t('common.moveUp', 'Move up')}><ChevronUp size={13} /></IconBtn>
                    <IconBtn type="button" disabled={i === fields.length - 1} onClick={() => moveField(i, 1)} title={t('common.moveDown', 'Move down')}><ChevronDown size={13} /></IconBtn>
                    <IconBtn type="button" onClick={() => removeField(i)} title={t('common.remove')}><Trash2 size={13} /></IconBtn>
                  </FieldRowLine>
                  {editFieldIdx === i && (
                    <FieldProps>
                      {/* Same ScreenField inspector the main fields use — scoped enums so ``dd``
                          resolves against the dictionary and column refs point at this query. */}
                      <FrameworkEnumsContext.Provider value={fieldEnums}>
                        <SchemaForm
                          schema={fieldPropsSchema}
                          defs={defs}
                          value={f}
                          onChange={(v) => updateFieldProps(i, v)}
                        />
                      </FrameworkEnumsContext.Provider>
                    </FieldProps>
                  )}
                </div>
              ))}
              <div style={{ marginTop: 6, maxWidth: 360 }}>
                <SearchSelect
                  value=""
                  options={colOptions}
                  onChange={(v) => addField(v)}
                  placeholder={t('settings.screens.visual.embedded.addField', 'Add a column…')}
                />
              </div>
            </>
          )}
        </div>
      )}
    </Card>
  )
}
