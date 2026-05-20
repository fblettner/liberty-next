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
import { Edit3, Plus, Trash2 } from 'lucide-react'
import {
  Button, Field, Row, SchemaForm, SchemaNavigator, SearchSelect, Stack, useModals, type JsonSchema, type SearchSelectOption,
} from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts } from '../../theme'
import { pickSchemaProperties } from './connectorTables'
import { EditQueryModal } from './EditQueryModal'
import ScreenVisualBuilder from './ScreenVisualBuilder'
import ActionListEditor from './ActionListEditor'

type Row = Record<string, unknown>

// Which top-level Screen properties belong on which inner tab. ``connector`` + the four CRUD
// query refs are rendered as custom SearchSelects (driven by the workspace's connector list
// and the selected connector's queries) — the rest fall through to SchemaForm for free.
// Phase 3 — ``audit_table`` (str) replaces ``audit`` (bool); ``max_rows`` lives in General.
// ``key_columns`` was here too, but the row's identifying columns are now ticked per-column
// via the Columns tab (``ColumnHint.key``) — the runtime's ``Screen.effective_key_columns()``
// derives the list from those flags. The explicit ``key_columns`` field stays on the schema
// for hand-edited overrides, but it doesn't render here (less duplication, one place to set).
const GENERAL_FORM_KEYS = ['label', 'description', 'audit_table', 'max_rows', 'auto_load', 'editable', 'uploadable'] as const
// Phase 3 — Screen.columns drives both grid + dialog display (single source of truth). Edited
// via SchemaNavigator on a dedicated tab.
const COLUMNS_KEYS = ['columns'] as const

type TabKey = 'general' | 'queries' | 'columns' | 'dialog' | 'actions' | 'rowmenu'
const TAB_ORDER: TabKey[] = ['general', 'queries', 'columns', 'dialog', 'actions', 'rowmenu']

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
// (``FieldList`` / ``FieldHeader`` / ``FieldBody`` moved into ``ActionListEditor.tsx`` — the
// shared editor owns its own row look.)

// (Action discriminated union + ``blankActionOfType`` + ``ACTION_*`` constants moved into
// ``ActionListEditor.tsx`` — the shared editor used by both this file and the visual builder's
// Tab Settings panel.)
// (``pickFromDefs`` removed — it was used by the now-deleted schema-mode field/tab editor.
// The visual builder picks its sub-schemas internally.)

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
  const modals = useModals()
  const [tab, setTab] = useState<TabKey>('general')
  // ``editQuery`` raises ``EditQueryModal`` to let the operator tweak a query's SQL / params
  // / writable flag without leaving the Screen Designer — every query SearchSelect has an
  // adjacent Edit (pencil) button that sets this. Cleared on Save / Cancel inside the modal.
  const [editQuery, setEditQuery] = useState<{ connector: string; queryName: string } | null>(null)

  const defs = (schema.$defs ?? {}) as Record<string, JsonSchema>
  // Workspace connectors carry their query list; we render the connector + query pickers as
  // SearchSelects driven off that list (instead of plain text fields). Fetched once at login;
  // permission-pruned to what the caller can read.
  const { connectors: wsConnectors } = useWorkspace()
  // Workspace context streams the connector list after login; until then it's null. Guard
  // both reads so the pickers show ``loading`` placeholders instead of crashing on first render.
  const effectiveConnector = (typeof value.connector === 'string' && value.connector.trim() ? value.connector : app)
  const connectorOptions = useMemo<SearchSelectOption[]>(
    () => (wsConnectors ?? []).filter((c) => c.type === 'sql')
      .map((c) => ({ value: c.name, label: c.name, mono: c.name })),
    [wsConnectors],
  )
  const selectedConnectorMeta = useMemo(
    () => (wsConnectors ?? []).find((c) => c.name === effectiveConnector),
    [wsConnectors, effectiveConnector],
  )
  // Queries available on the effective connector — the four CRUD pickers (read / update /
  // insert / delete) all read from this. SQL connectors only; an API connector has endpoints
  // not queries, but a Screen pins to a SQL read query so the picker filters to SQL anyway.
  const queryOptions = useMemo<SearchSelectOption[]>(() => {
    if (!selectedConnectorMeta || selectedConnectorMeta.type !== 'sql') return []
    return selectedConnectorMeta.queries.map((q) => ({
      value: q.name,
      label: q.description || q.label || q.name,
      mono: q.name,
    }))
  }, [selectedConnectorMeta])
  // Pre-pick the per-tab sub-schemas. General/Queries leave connector + the four query fields
  // out (rendered manually as SearchSelects); everything else still goes through SchemaForm so
  // its field-level enums + descriptions kick in.
  const generalSchema = useMemo<JsonSchema>(() => pickSchemaProperties(schema, GENERAL_FORM_KEYS as unknown as string[]), [schema])
  // Phase 3 — the Columns tab edits ``Screen.columns`` via SchemaNavigator. Same ColumnHint
  // shape used elsewhere; carries the full ``$defs`` map so nested ``filter_from`` /
  // ``visible_when`` / ``lookup_param_binds`` drill in via the breadcrumb navigator.
  const columnsSchema = useMemo<JsonSchema>(
    () => ({ ...pickSchemaProperties(schema, COLUMNS_KEYS as unknown as string[]), $defs: defs }),
    [schema, defs],
  )
  // (Schema-mode sub-schemas + tab/field mutation helpers are gone — the visual builder owns
  // the dialog-tab/field editing experience now. ``setProp`` / ``dialog`` / ``setDialog`` /
  // ``createDialog`` remain since they're used by the Dialog tab's empty-state "Create dialog"
  // affordance and by the lifecycle-hook accessors below.)
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
  const createDialog = () => {
    setDialog({ tabs: [{ id: 'general', label: 'General', fields: [] }] })
  }

  // --- render ----------------------------------------------------------------
  // Connector picker — SearchSelect over the workspace's accessible SQL connectors. Setting it
  // back to the app's name implicit value is handled by passing an "(use app's connector)"
  // anyLabel: picking it clears ``screen.connector`` so the runtime falls back to the app name.
  const renderGeneral = (): ReactNode => (
    <>
      <Sub>{t('settings.screens.editor.generalHint')}</Sub>
      <Field label={t('settings.screens.editor.connectorLabel')}>
        <SearchSelect
          value={(value.connector as string | undefined) ?? ''}
          options={connectorOptions}
          onChange={(v) => setProp('connector', v && v !== app ? v : null)}
          anyLabel={t('settings.screens.editor.connectorUseApp', { app })}
          placeholder={app}
        />
      </Field>
      <SchemaForm
        schema={generalSchema}
        defs={defs}
        value={value}
        onChange={(v) => {
          // Apply every GENERAL_FORM_KEYS field in ONE onChange call — calling ``setProp``
          // in a loop loses edits because each call reads ``value`` from the closure (stale
          // within the synchronous event handler), so only the last one wins (and any field
          // that wasn't the last key in the loop gets clobbered). Build the patched next
          // value in one go, then call ``onChange`` once with the full result. Untouched
          // keys outside GENERAL_FORM_KEYS (dialog, actions, row_menu, id, read_query, …)
          // stay verbatim via the ``{...value}`` spread.
          const next: Row = { ...value }
          for (const k of GENERAL_FORM_KEYS) {
            const val = v[k]
            if (val === undefined || val === null || val === '' || (Array.isArray(val) && val.length === 0)) delete next[k]
            else next[k] = val
          }
          onChange(next)
        }}
      />
    </>
  )

  // The four CRUD query pickers — each a SearchSelect over the effective connector's queries.
  // read_query is required (validator on the backend); the others are optional and clearing
  // them removes the key. Picker shows the v2 query name (mono) + the query's description /
  // label so operators can find by friendly name. An "Edit" button next to the picker raises
  // ``EditQueryModal`` so the operator can tweak the SQL / params without leaving this screen.
  const renderQueryField = (key: 'read_query' | 'update_query' | 'insert_query' | 'delete_query', required: boolean): ReactNode => {
    const queryName = (value[key] as string | undefined) ?? ''
    return (
      <Field
        key={key}
        label={`${t(`settings.screens.editor.queries.${key}`)}${required ? ' *' : ''}`}
      >
        <Row gap={6} style={{ alignItems: 'center' }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <SearchSelect
              value={queryName}
              options={queryOptions}
              onChange={(v) => setProp(key, v || (required ? value[key] ?? '' : null))}
              anyLabel={required ? undefined : t('common.none')}
              placeholder={selectedConnectorMeta ? t('common.pick') : t('settings.screens.editor.queries.pickConnectorFirst')}
              loading={!selectedConnectorMeta}
            />
          </div>
          {queryName && effectiveConnector && (
            <Button
              $variant="ghost"
              $size="sm"
              onClick={() => setEditQuery({ connector: effectiveConnector, queryName })}
              title={t('settings.editQuery.edit', 'Edit query')}
            >
              <Edit3 size={13} />
            </Button>
          )}
        </Row>
      </Field>
    )
  }
  const renderQueries = (): ReactNode => (
    <>
      <Sub>{t('settings.screens.editor.queriesHint')}</Sub>
      {renderQueryField('read_query', true)}
      {renderQueryField('update_query', false)}
      {renderQueryField('insert_query', false)}
      {renderQueryField('delete_query', false)}
    </>
  )

  // Phase 3 — the Columns tab edits ``Screen.columns`` (single source of truth for per-screen
  // display metadata: label / dd / format / hidden / filter / filter_from / visible_when / width
  // / align / rules / rules_values / default / lookup_param_binds). Same shape drives both the
  // grid editor and the dialog form. SchemaNavigator gives breadcrumb drill-down into each
  // column hint and its nested rules.
  const renderColumns = (): ReactNode => {
    const currentColumns = Array.isArray(value.columns) ? (value.columns as unknown[]) : []
    return (
      <>
        <Sub>{t('settings.screens.editor.columnsHint')}</Sub>
        <SchemaNavigator
          root={{
            label: t('settings.screens.editor.columnsCrumb', { id }),
            schema: columnsSchema,
            value: { columns: currentColumns },
            onChange: (v) => {
              const next = Array.isArray(v.columns) && (v.columns as unknown[]).length
                ? (v.columns as unknown[])
                : null
              setProp('columns', next)
            },
          }}
        />
      </>
    )
  }

  // ── action editors (shared component) ──────────────────────────────────────────────
  // ``renderActionList`` + its inline ``renderPromptFields`` / ``renderActionOverrides`` /
  // ``actionVariantSchema`` blocks (and the action / PromptField constants) all moved into
  // ``ActionListEditor.tsx`` so the visual builder's Tab Settings panel can reuse the same
  // editor. Each attachment point below renders one ``<ActionListEditor>`` instance.
  const onEditQueryRaise = (connector: string, queryName: string) =>
    setEditQuery({ connector, queryName })


  // Dialog lifecycle hook accessors — same pattern for each. ``setDialog`` strips empty hook
  // lists (we drop the key when the array empties) so the saved TOML stays terse.
  const dialogList = (key: 'on_load' | 'on_save' | 'on_cancel'): Row[] =>
    Array.isArray((dialog as Row | null)?.[key]) ? ((dialog as Row)[key] as Row[]) : []
  const setDialogList = (key: 'on_load' | 'on_save' | 'on_cancel', next: Row[]) => {
    const updated = { ...(dialog ?? {}) } as Row
    if (next.length === 0) delete updated[key]
    else updated[key] = next
    setDialog(updated as { title?: string; tabs?: Row[] })
  }
  const onLoad = useMemo(() => dialogList('on_load'), [dialog])
  const onSave = useMemo(() => dialogList('on_save'), [dialog])
  const onCancel = useMemo(() => dialogList('on_cancel'), [dialog])
  const renderOnLoad = (): ReactNode => (
    <ActionListEditor
      actions={onLoad}
      onChange={(n) => setDialogList('on_load', n)}
      heading={t('settings.screens.onLoad.heading')}
      hint={t('settings.screens.onLoad.hint')}
      emptyMessage={t('settings.screens.onLoad.empty')}
      defs={defs}
      effectiveConnector={effectiveConnector}
      onEditQuery={onEditQueryRaise}
    />
  )
  const renderOnSave = (): ReactNode => (
    <ActionListEditor
      actions={onSave}
      onChange={(n) => setDialogList('on_save', n)}
      heading={t('settings.screens.action.heading')}
      hint={t('settings.screens.action.hint')}
      emptyMessage={t('settings.screens.action.empty')}
      defs={defs}
      effectiveConnector={effectiveConnector}
      onEditQuery={onEditQueryRaise}
    />
  )
  const renderOnCancel = (): ReactNode => (
    <ActionListEditor
      actions={onCancel}
      onChange={(n) => setDialogList('on_cancel', n)}
      heading={t('settings.screens.onCancel.heading')}
      hint={t('settings.screens.onCancel.hint')}
      emptyMessage={t('settings.screens.onCancel.empty')}
      defs={defs}
      effectiveConnector={effectiveConnector}
      onEditQuery={onEditQueryRaise}
    />
  )

  // (Per-tab ``actions`` editor moved to the visual builder — it owns the dialog-tab selection
  // state, so the per-tab actions sit naturally on the selected tab's canvas. The renderActionList
  // editor for them is wired inside ScreenVisualBuilder's tab body.)

  // Screen `actions` — toolbar buttons above the TableView. v1's named workflows (NOMAJDE's
  // "Create Role" / "Reset Password" / etc.) belong here. ParamBinds resolve against the
  // *selected row* (when one is selected) — same Action shape used everywhere.
  const screenActions: Row[] = useMemo(
    () => (Array.isArray((value as Row).actions) ? ((value as Row).actions as Row[]) : []),
    [value],
  )
  const setScreenActions = (next: Row[]) => {
    const v = { ...value }
    if (next.length === 0) delete v.actions
    else v.actions = next
    onChange(v)
  }
  const renderScreenActions = (): ReactNode => (
    <ActionListEditor
      actions={screenActions}
      onChange={setScreenActions}
      heading={t('settings.screens.actions.heading')}
      hint={t('settings.screens.actions.hint')}
      emptyMessage={t('settings.screens.actions.empty')}
      defs={defs}
      effectiveConnector={effectiveConnector}
      onEditQuery={onEditQueryRaise}
    />
  )

  // Screen-level row lifecycle hooks — v2's port of v1's ``ly_evt_cpt`` FormsTable events
  // (evt 2 = on_insert, evt 3 = on_delete; on_update is v2's own extension). Fire after a row
  // is mutated whether via dialog Save in the matching mode *or* the batch-edit grid Save.
  // ParamBinds resolve against the *new row's* values (insert/update) or the deleted row's
  // values (delete). Edit them in the same "Actions" tab alongside the toolbar buttons —
  // related concept, same Action shape.
  const screenHookList = (key: 'on_insert' | 'on_update' | 'on_delete'): Row[] =>
    Array.isArray((value as Row)[key]) ? ((value as Row)[key] as Row[]) : []
  const setScreenHook = (key: 'on_insert' | 'on_update' | 'on_delete', next: Row[]) => {
    const v = { ...value }
    if (next.length === 0) delete v[key]
    else v[key] = next
    onChange(v)
  }
  const renderRowHooks = (): ReactNode => (
    <>
      <ActionListEditor
        actions={screenHookList('on_insert')}
        onChange={(n) => setScreenHook('on_insert', n)}
        heading={t('settings.screens.onInsert.heading')}
        hint={t('settings.screens.onInsert.hint')}
        emptyMessage={t('settings.screens.onInsert.empty')}
        defs={defs}
        effectiveConnector={effectiveConnector}
        onEditQuery={onEditQueryRaise}
      />
      <ActionListEditor
        actions={screenHookList('on_update')}
        onChange={(n) => setScreenHook('on_update', n)}
        heading={t('settings.screens.onUpdate.heading')}
        hint={t('settings.screens.onUpdate.hint')}
        emptyMessage={t('settings.screens.onUpdate.empty')}
        defs={defs}
        effectiveConnector={effectiveConnector}
        onEditQuery={onEditQueryRaise}
      />
      <ActionListEditor
        actions={screenHookList('on_delete')}
        onChange={(n) => setScreenHook('on_delete', n)}
        heading={t('settings.screens.onDelete.heading')}
        hint={t('settings.screens.onDelete.hint')}
        emptyMessage={t('settings.screens.onDelete.empty')}
        defs={defs}
        effectiveConnector={effectiveConnector}
        onEditQuery={onEditQueryRaise}
      />
    </>
  )

  // Screen `row_menu` (slice 6) — actions shown when the user right-clicks a row in the TableView.
  // ParamBinds resolve against the clicked row's values (not the dialog form state) — the runtime
  // uses the same Action shape; only the firing context differs.
  const rowMenu: Row[] = useMemo(
    () => (Array.isArray((value as Row).row_menu) ? ((value as Row).row_menu as Row[]) : []),
    [value],
  )
  const setRowMenu = (next: Row[]) => {
    const v = { ...value }
    if (next.length === 0) delete v.row_menu
    else v.row_menu = next
    onChange(v)
  }
  const renderRowMenu = (): ReactNode => (
    <ActionListEditor
      actions={rowMenu}
      onChange={setRowMenu}
      heading={t('settings.screens.rowmenu.heading')}
      hint={t('settings.screens.rowmenu.hint')}
      emptyMessage={t('settings.screens.rowmenu.empty')}
      defs={defs}
      effectiveConnector={effectiveConnector}
      onEditQuery={onEditQueryRaise}
    />
  )

  // Deletes the entire dialog (every tab + field + lookup_param_bind). Confirmed via the
  // shared ConfirmModal — the action is destructive and irreversible inside this designer
  // session (the only way back is to Cancel the whole designer modal, which reverts to the
  // snapshot, OR Reload from disk after committing).
  const deleteDialog = async () => {
    const ok = await modals.confirm({
      title: t('settings.screens.editor.dialogDeleteTitle', 'Delete dialog?'),
      message: t('settings.screens.editor.dialogDeleteMsg', 'Remove the screen\'s dialog (every tab + field + lookup_param_bind). The screen becomes read-only / grid-edit only.'),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    setDialog(null)
  }

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
    // The Dialog tab IS the visual builder — schema mode and the Visual/Schema toggle are gone.
    // Lifecycle hooks (on_load / on_save / on_cancel) moved to the Actions tab where the rest
    // of the action chains live, grouped by event kind. Per-panel scrolling (palette / canvas /
    // inspector) is handled inside ScreenVisualBuilder; we just give it a flex-fill container.
    // A "Delete dialog" button on the right header line lets the operator wipe it entirely —
    // confirmed via shared modal so an accidental click doesn't nuke the work.
    return (
      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        <Row gap={8} style={{ justifyContent: 'flex-end', marginBottom: 8 }}>
          <Button $variant="danger" $size="sm" onClick={deleteDialog}>
            <Trash2 size={13} /> {t('settings.screens.editor.dialogDelete', 'Delete dialog')}
          </Button>
        </Row>
        <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
          <ScreenVisualBuilder app={app} id={id} value={value} schema={schema} onChange={onChange} />
        </div>
      </div>
    )
  }

  const renderTab = (): ReactNode => {
    switch (tab) {
      case 'general': return renderGeneral()
      case 'queries': return renderQueries()
      case 'columns': return renderColumns()
      case 'dialog':  return renderDialog()
      case 'actions': return (
        // All action attachment points consolidated. Grouped visually by *when* they fire:
        // - **Dialog hooks** (on_load / on_save / on_cancel) — fire while the dialog is open
        // - **Toolbar** (Screen.actions) — the buttons above the table
        // - **Row hooks** (on_insert / on_update / on_delete) — fire after a row mutation,
        //   either via dialog Save or the inline grid's batch save
        // Each section is rendered through the same ``renderActionList`` editor.
        <Stack gap={20}>
          <div>
            <Sub style={{ margin: 0, marginBottom: 8 }}>{t('settings.screens.actionsTab.dialogHooksGroup')}</Sub>
            {renderOnLoad()}
            {renderOnSave()}
            {renderOnCancel()}
          </div>
          <div>
            <Sub style={{ margin: 0, marginBottom: 8 }}>{t('settings.screens.actionsTab.toolbarGroup')}</Sub>
            {renderScreenActions()}
          </div>
          <div>
            <Sub style={{ margin: 0, marginBottom: 8 }}>{t('settings.screens.actionsTab.rowHooksGroup')}</Sub>
            {renderRowHooks()}
          </div>
        </Stack>
      )
      case 'rowmenu': return renderRowMenu()
    }
  }

  // Active tab content lives inside a scroll-managing wrapper. Most tabs (General / Queries /
  // Actions / Row menu) get an ``overflow-y: auto`` wrapper so long forms scroll inside the
  // modal body without making the body itself scroll. The Dialog tab is special — the visual
  // builder owns its own per-panel scrolling (palette / canvas / inspector all scroll on their
  // own); wrapping it would create a redundant outer scrollbar. Detect via the active tab key.
  const isDialogTab = tab === 'dialog'
  return (
    <Stack gap={10} style={{ flex: 1, minHeight: 0 }}>
      <TabsBar>
        {TAB_ORDER.map((k) => (
          <TabBtn key={k} type="button" $active={tab === k} onClick={() => setTab(k)}>
            {t(`settings.screens.editor.tabs.${k}`)}
          </TabBtn>
        ))}
      </TabsBar>
      <div style={{
        flex: 1,
        minHeight: 0,
        overflowY: isDialogTab ? 'hidden' : 'auto',
        display: 'flex',
        flexDirection: 'column',
        paddingRight: isDialogTab ? 0 : 4,
      }}>
        {renderTab()}
      </div>
      {/* Per-query Edit modal — opened from any query SearchSelect's pencil button. The modal
          fetches its own connectors copy + PUTs it back on Save (independent of the Screen
          Designer's edit cycle, since query edits live in connectors.toml). */}
      {editQuery && (
        <EditQueryModal
          connector={editQuery.connector}
          queryName={editQuery.queryName}
          onClose={() => setEditQuery(null)}
        />
      )}
    </Stack>
  )
}
