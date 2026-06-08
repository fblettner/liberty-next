// Editor for a screen's ``column_groups`` — related 1:1 tables whose JOINed columns are edited
// INLINE (grid + dialog) and written back on Save. Each group names its write queries + the FK
// ``param_binds`` linking the parent row; a column joins a group via ``ColumnHint.group`` (the
// per-column dropdown in the Columns tab). The dialog-only equivalent is EmbeddedFormsEditor —
// this one is kept dedicated for the same reason: each card computes its own connector's writable
// query list + the FK param suggestions, and surfaces which columns are currently attached.
import { useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Plus, Trash2, X } from 'lucide-react'
import { Button, Checkbox, Input, SearchSelect, type SearchSelectOption } from '../../common'
import ParamBindList, { type ParamBind } from './ParamBindList'
import { EditQueryButton, CloneQueryButton, AddQueryButton } from './EditQueryButton'
import { targetParamOptions } from './actionCandidates'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'

type Row = Record<string, unknown>

const Card = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  padding: 10px 12px; margin-bottom: 10px;
`
const CardHead = styled.div`
  display: flex; align-items: center; gap: 8px; cursor: pointer; user-select: none;
  & .grow { flex: 1; min-width: 0; }
  & .id { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .count { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
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
const Body = styled.div`display: flex; flex-direction: column; gap: 10px; margin-top: 10px;`
const Two = styled.div`display: grid; grid-template-columns: 1fr 1fr; gap: 10px;`
// Query picker + its Edit/Clone/Add trio on one line (full width, like the Queries tab) — the
// buttons need horizontal room, so update/insert queries each get their own row.
const QueryRow = styled.div`
  display: flex; align-items: center; gap: 6px;
  & .grow { flex: 1; min-width: 0; }
`
const SubLabel = styled.div`
  font-size: ${fontSize.micro}; font-weight: 600; text-transform: uppercase; letter-spacing: 0.06em;
  color: ${colors.text.muted}; margin: 6px 0 2px;
`
const Hint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro}; line-height: 1.4; margin: 2px 0;`
// Read-only chips of the columns tagged with this group — so the operator sees at a glance which
// columns route through this related table without scrolling the Columns list.
const ChipRow = styled.div`display: flex; flex-wrap: wrap; gap: 6px; margin: 2px 0;`
const Chip = styled.span`
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 999px;
  background: ${colors.bg.card}; border: 1px solid ${colors.border};
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.secondary};
`
// Editable key-column chips carry a remove × (key_columns is a small picked set, not free text).
const KeyChip = styled(Chip)`
  & button { display: inline-flex; border: none; background: transparent; padding: 0; cursor: pointer;
    color: ${colors.text.muted}; &:hover { color: ${colors.red.main}; } }
`

interface ColumnGroupsEditorProps {
  /** The screen's ``column_groups`` list. */
  value: Row[]
  onChange: (next: Row[]) => void
  /** Default connector (the screen's) — a group that doesn't override ``connector`` writes here. */
  screenConnector: string
  /** SQL connector options for the per-group ``connector`` override. */
  connectorOptions: SearchSelectOption[]
  /** Screen columns — power the ``key_columns`` picker AND each bind's ``source`` dropdown. */
  columnOptions: SearchSelectOption[]
  /** groupId → the column names currently tagged with it (``ColumnHint.group``), for the display. */
  columnsByGroup: Map<string, string[]>
}

export default function ColumnGroupsEditor({
  value, onChange, screenConnector, connectorOptions, columnOptions, columnsByGroup,
}: ColumnGroupsEditorProps) {
  const { t } = useTranslation()
  const groups = Array.isArray(value) ? value : []

  // Patch one group, stripping keys that empty out so the saved TOML stays terse (matches the
  // migrator's default-stripping). ``update_query`` is required by the model, but we keep it even
  // when blank mid-edit so the card persists; the operator fills it before the config validates.
  const patch = (idx: number, p: Row) => {
    const next = groups.slice()
    const cur = { ...next[idx], ...p } as Row
    for (const k of Object.keys(p)) {
      if (k === 'update_query') continue
      const v = cur[k]
      if (v == null || v === '' || (Array.isArray(v) && v.length === 0)) delete cur[k]
    }
    next[idx] = cur
    onChange(next)
  }
  const add = () => {
    const ids = new Set(groups.map((g) => String(g.id ?? '')))
    let n = groups.length + 1
    let id = `group_${n}`
    while (ids.has(id)) { n += 1; id = `group_${n}` }
    onChange([...groups, { id, update_query: '', key_columns: [], param_binds: [] }])
  }
  const remove = (idx: number) => {
    const next = groups.slice(); next.splice(idx, 1); onChange(next)
  }

  return (
    <div>
      {groups.map((g, i) => (
        <GroupCard
          key={String(g.id ?? i)}
          group={g}
          screenConnector={screenConnector}
          connectorOptions={connectorOptions}
          columnOptions={columnOptions}
          attachedColumns={columnsByGroup.get(String(g.id ?? '')) ?? []}
          onPatch={(p) => patch(i, p)}
          onRemove={() => remove(i)}
        />
      ))}
      <Button $variant="ghost" $size="sm" onClick={add} style={{ alignSelf: 'flex-start' }}>
        <Plus size={13} /> {t('settings.screens.editor.columnGroupsAdd', 'Add column group')}
      </Button>
    </div>
  )
}

function GroupCard({
  group, screenConnector, connectorOptions, columnOptions, attachedColumns, onPatch, onRemove,
}: {
  group: Row
  screenConnector: string
  connectorOptions: SearchSelectOption[]
  columnOptions: SearchSelectOption[]
  attachedColumns: string[]
  onPatch: (p: Row) => void
  onRemove: () => void
}) {
  const { t } = useTranslation()
  const { connectors } = useWorkspace()
  const [open, setOpen] = useState(false)

  const conn = (typeof group.connector === 'string' && group.connector.trim()) ? group.connector : screenConnector
  // Writable queries on the group's connector — its related table's _put / _post. Fall back to all
  // queries when none advertise ``writable`` so a custom write query still shows.
  const writableQueryOptions = useMemo<SearchSelectOption[]>(() => {
    const c = (connectors ?? []).find((x) => x.name === conn)
    if (!c || c.type !== 'sql') return []
    const ws = c.queries.filter((q) => q.writable)
    return (ws.length ? ws : c.queries).map((q) => ({
      value: q.name, label: q.label ? `${q.label} (${q.name})` : q.name, mono: q.name,
    }))
  }, [connectors, conn])
  // ALL query names on the connector — the Clone/Add name validator's duplicate check (not just
  // writable ones, so a clone can't collide with a read query either).
  const allQueryNames = useMemo<string[]>(() => {
    const c = (connectors ?? []).find((x) => x.name === conn)
    return c && c.type === 'sql' ? c.queries.map((q) => q.name) : []
  }, [connectors, conn])
  // FK param dropdown — the related write query's :placeholders (update preferred, else insert).
  const paramOptions = useMemo<SearchSelectOption[]>(() => {
    const target = (typeof group.update_query === 'string' && group.update_query)
      ? group.update_query
      : (typeof group.insert_query === 'string' ? group.insert_query : '')
    if (!target) return []
    return targetParamOptions({ type: 'run_query', query: target, connector: conn }, connectors, conn)
  }, [connectors, conn, group.update_query, group.insert_query])

  const keyColumns = Array.isArray(group.key_columns) ? (group.key_columns as string[]) : []
  const addKeyColumn = (name: string) => {
    if (!name || keyColumns.includes(name)) return
    onPatch({ key_columns: [...keyColumns, name] })
  }
  const removeKeyColumn = (name: string) =>
    onPatch({ key_columns: keyColumns.filter((k) => k !== name) })
  const keyColumnChoices = columnOptions.filter((o) => !keyColumns.includes(o.value))

  const id = String(group.id ?? '')
  return (
    <Card>
      <CardHead onClick={() => setOpen((o) => !o)}>
        <Toggle as="span" aria-hidden>
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </Toggle>
        <span className="grow id">{id || t('settings.screens.editor.columnGroupUntitled', '(unnamed group)')}</span>
        <span className="count">{t('settings.screens.editor.columnGroupCount', '{{n}} column(s)', { n: attachedColumns.length })}</span>
        <RemoveBtn type="button" onClick={(e) => { e.stopPropagation(); onRemove() }} aria-label="Remove group"><Trash2 size={15} /></RemoveBtn>
      </CardHead>

      {open && (
        <Body>
          <Two>
            <div>
              <SubLabel>{t('settings.screens.editor.columnGroupId', 'Group id')}</SubLabel>
              <Input value={id} placeholder="addr"
                onChange={(e) => onPatch({ id: e.target.value })} />
            </div>
            <div>
              <SubLabel>{t('settings.screens.editor.columnGroupLabel', 'Label')}</SubLabel>
              <Input value={typeof group.label === 'string' ? group.label : ''} placeholder={t('settings.screens.editor.columnGroupLabelPh', 'Address Book')}
                onChange={(e) => onPatch({ label: e.target.value })} />
            </div>
          </Two>

          <div>
            <SubLabel>{t('settings.screens.editor.columnGroupConnector', 'Connector')}</SubLabel>
            <SearchSelect
              value={typeof group.connector === 'string' ? group.connector : ''}
              onChange={(v) => onPatch({ connector: v })}
              options={connectorOptions}
              anyLabel={t('settings.screens.editor.columnGroupConnectorAny', '(use screen’s — {{c}})', { c: screenConnector })}
              placeholder={screenConnector}
            />
          </div>

          <div>
            <SubLabel>{t('settings.screens.editor.columnGroupUpdate', 'Update query')}</SubLabel>
            <QueryRow>
              <div className="grow">
                <SearchSelect
                  value={typeof group.update_query === 'string' ? group.update_query : ''}
                  onChange={(v) => onPatch({ update_query: v })}
                  options={writableQueryOptions} allowCustom
                  placeholder={t('settings.screens.editor.columnGroupPickQuery', 'Pick a query…')}
                />
              </div>
              <EditQueryButton connector={conn} queryName={typeof group.update_query === 'string' ? group.update_query : ''} />
              <CloneQueryButton connector={conn} queryName={typeof group.update_query === 'string' ? group.update_query : ''} existingNames={allQueryNames} />
              <AddQueryButton connector={conn} existingNames={allQueryNames} />
            </QueryRow>
          </div>
          <div>
            <SubLabel>{t('settings.screens.editor.columnGroupInsert', 'Insert query')}</SubLabel>
            <QueryRow>
              <div className="grow">
                <SearchSelect
                  value={typeof group.insert_query === 'string' ? group.insert_query : ''}
                  onChange={(v) => onPatch({ insert_query: v })}
                  options={writableQueryOptions} allowCustom
                  anyLabel={t('settings.screens.editor.columnGroupNoInsert', '(no insert — update only)')}
                  placeholder={t('settings.screens.editor.columnGroupPickQuery', 'Pick a query…')}
                />
              </div>
              <EditQueryButton connector={conn} queryName={typeof group.insert_query === 'string' ? group.insert_query : ''} />
              <CloneQueryButton connector={conn} queryName={typeof group.insert_query === 'string' ? group.insert_query : ''} existingNames={allQueryNames} />
              <AddQueryButton connector={conn} existingNames={allQueryNames} />
            </QueryRow>
          </div>
          <div>
            <SubLabel>{t('settings.screens.editor.columnGroupDelete', 'Delete query')}</SubLabel>
            <Hint>{t('settings.screens.editor.columnGroupDeleteHint',
              'Removes the related row when the main row is deleted (grid or dialog). Leave blank to keep it.')}</Hint>
            <QueryRow>
              <div className="grow">
                <SearchSelect
                  value={typeof group.delete_query === 'string' ? group.delete_query : ''}
                  onChange={(v) => onPatch({ delete_query: v })}
                  options={writableQueryOptions} allowCustom
                  anyLabel={t('settings.screens.editor.columnGroupNoDelete', '(no delete — leave related row)')}
                  placeholder={t('settings.screens.editor.columnGroupPickQuery', 'Pick a query…')}
                />
              </div>
              <EditQueryButton connector={conn} queryName={typeof group.delete_query === 'string' ? group.delete_query : ''} />
              <CloneQueryButton connector={conn} queryName={typeof group.delete_query === 'string' ? group.delete_query : ''} existingNames={allQueryNames} />
              <AddQueryButton connector={conn} existingNames={allQueryNames} />
            </QueryRow>
          </div>

          <div>
            <SubLabel>{t('settings.screens.editor.columnGroupKeys', 'Key columns')}</SubLabel>
            <Hint>{t('settings.screens.editor.columnGroupKeysHint',
              'The related row’s key as it appears in the JOINed result — non-null ⇒ the row exists ⇒ UPDATE, else INSERT.')}</Hint>
            {keyColumns.length > 0 && (
              <ChipRow>
                {keyColumns.map((k) => (
                  <KeyChip key={k}>{k}<button type="button" onClick={() => removeKeyColumn(k)} aria-label={`Remove ${k}`}><X size={12} /></button></KeyChip>
                ))}
              </ChipRow>
            )}
            <SearchSelect value="" onChange={addKeyColumn} options={keyColumnChoices}
              placeholder={t('settings.screens.editor.columnGroupAddKey', '+ add key column')} />
          </div>

          <div>
            <SubLabel>{t('settings.screens.editor.columnGroupBinds', 'FK binds (parent → related)')}</SubLabel>
            <Hint>{t('settings.screens.editor.columnGroupBindsHint',
              'Link the parent row into the related write: source = a main column, param = the related query’s :placeholder.')}</Hint>
            <ParamBindList
              value={(Array.isArray(group.param_binds) ? group.param_binds : []) as ParamBind[]}
              onChange={(next) => onPatch({ param_binds: next })}
              sourceOptions={columnOptions}
              paramOptions={paramOptions}
            />
          </div>

          <div>
            <Checkbox
              checked={group.insert_on_add === true}
              onChange={(v) => onPatch({ insert_on_add: v })}
              label={t('settings.screens.editor.columnGroupInsertOnAdd', 'Always insert on Add (mandatory 1:1 companion)')}
            />
            <Hint>{t('settings.screens.editor.columnGroupInsertOnAddHint',
              'Insert the related row on every Add even if none of its fields were filled — the FK bind + server-side dictionary defaults populate it. Off: only inserted when a field has a value (so an optional companion isn’t created blank). No effect on Edit.')}</Hint>
          </div>

          <div>
            <SubLabel>{t('settings.screens.editor.columnGroupColumns', 'Columns in this group')}</SubLabel>
            {attachedColumns.length > 0 ? (
              <ChipRow>{attachedColumns.map((c) => <Chip key={c}>{c}</Chip>)}</ChipRow>
            ) : (
              <Hint>{t('settings.screens.editor.columnGroupNoColumns',
                'No columns tagged yet — set a column’s Group to “{{id}}” in the list below.', { id: id || '…' })}</Hint>
            )}
          </div>
        </Body>
      )}
    </Card>
  )
}
