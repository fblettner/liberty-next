// Unified per-table editor: presents a first-class CRUD `TableEntry` (its `name` / `label` /
// `description` + up to four CRUD slots) as ONE configurable object — a tab strip across the top:
//   General · Read · Update · Insert · Delete
// **Sectioned model** — the table metadata (label / description) lives on the `TableEntry` itself,
// NOT on a `_get` slot, so a write-only table (only `_put` / `_post`, no `_get`) edits its metadata
// just the same. Each CRUD tab edits its slot (`sql` / `writable`); a missing slot shows a
// "+ Create" button. The table `name` is renamed cross-file via the Rename button (editing it
// inline would break every screen / dictionary / chart / dashboard / menu reference).
//
// **Phase 3** — per-screen behaviour (`columns` / `auto_load` / `audit_table` / `max_rows` /
// `key_columns` / companion-query refs) lives on the `Screen`, edited in the Screen Designer; the
// "Open in Screens" link in this editor's header jumps to the matching screen.
import { useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { ArrowRightLeft, Copy, Edit3, ExternalLink, GitBranch, Plus, Shuffle, Trash2, Search, Tag } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button, Row, SchemaForm, SqlConnectorContext, SqlStatementContext, Stack, useModals, type JsonSchema } from '../../common'
import { SubNav } from '../../common/SubNav'
import { colors, fontSize, fonts } from '../../theme'
import StageStrip, { type StageTone } from './StageStrip'
import { tableSlots } from '../../services/connectorFigures'
import {
  CRUD_KINDS,
  type CrudKind,
  type CrudSlotEntry,
  type TableEntry,
  pickSchemaProperties,
} from './connectorTables'

// ── styled bits ───────────────────────────────────────────────────────────────
const Title = styled.strong`
  font-family: ${fonts.mono}; color: ${colors.text.primary}; font-size: ${fontSize.base};
  & .muted { color: ${colors.text.muted}; font-weight: 400; margin-left: 6px; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 30px 6px; text-align: center;`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; margin-bottom: 10px;`

// ── tabs ──────────────────────────────────────────────────────────────────────
type TabKey = 'general' | CrudKind

const SLOT_ICON: Record<CrudKind, ReactNode> = {
  get: <Search size={15} />, put: <Edit3 size={15} />, post: <Plus size={15} />, delete: <Trash2 size={15} />,
}
const TAB_TO_CRUD: Partial<Record<TabKey, CrudKind>> = { get: 'get', put: 'put', post: 'post', delete: 'delete' }
// Each CRUD slot's statement kind — threaded to the slot's SQL wizard so it builds the right shape.
const CRUD_TO_STMT: Record<CrudKind, 'SELECT' | 'UPDATE' | 'INSERT' | 'DELETE'> = {
  get: 'SELECT', put: 'UPDATE', post: 'INSERT', delete: 'DELETE',
}

// General edits the table-level metadata that lives on the TableDef itself (label / description) —
// `name` is renamed cross-file via the Rename button. CRUD bodies edit the CrudSlot: `sql` on the
// read, `sql` + `writable` on the writes. Table CRUD slots carry no declared params (reads filter
// via the screen's FilterPanel; writes bind the edited row's columns), so Params is omitted — it
// lives only on the Custom / Sequence / Lookup query editors.
const GENERAL_KEYS = ['label', 'description']
const READ_BODY_KEYS = ['sql']
const WRITE_BODY_KEYS = ['sql', 'writable']

const SLOT_DEFAULTS: Record<CrudKind, CrudSlotEntry> = {
  get: { sql: '' },
  put: { sql: '', writable: true },
  post: { sql: '', writable: true },
  delete: { sql: '', writable: true },
}

export interface ConnectorsTableEditorProps {
  table: TableEntry
  /** The connector this table lives in — threaded into `SqlConnectorContext` so every SQL editor
   *  enables schema-aware autocomplete against that connector's pool. */
  connectorName: string
  /** TableDef schema (label / description live here) + CrudSlot schema (sql / writable). */
  tableDefSchema: JsonSchema
  crudSlotSchema: JsonSchema
  defs: Record<string, JsonSchema>
  /** Persist the edited table back to the connector's `tables[]` (replace-by-name). */
  onChange: (next: TableEntry) => void
  /** Drop the whole table from the connector. */
  onDelete: () => void
  onBack: () => void
  onDuplicate?: () => void
  /** Cross-file rename of the table name (all CRUD slot refs + screen bindings). Parent wires it
   *  to the rename endpoint; absent → no Rename button. */
  onRename?: () => void
  /** Reclassify the table to a standalone query / sequence / lookup (single-slot tables only).
   *  Absent → no Change-type button. */
  onChangeType?: () => void
  /** Find every screen / menu / action / dictionary reference to this table (via its slot
   *  queries). Absent → no Find-usages button. */
  onFindUsages?: () => void
  /** Move this table (all its CRUD slots) to another connector + rewrite refs. Absent → no button. */
  onMove?: () => void
  /** When the parent finds a Screen whose `read_query` matches `<name>_get`, it passes `{app, id}`
   *  so the header shows an "Open visual builder" button. */
  screenLink?: { app: string; id: string } | null
  onOpenScreen?: (app: string, id: string) => void
}

export default function ConnectorsTableEditor({
  table, connectorName, tableDefSchema, crudSlotSchema, defs, onChange, onDelete, onBack, onDuplicate, onRename, onChangeType, onFindUsages, onMove, screenLink, onOpenScreen,
}: ConnectorsTableEditorProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const filledSlots = CRUD_KINDS.filter((c) => table[c])
  const [tab, setTab] = useState<TabKey>('general')

  const setSlot = (crud: CrudKind, slot: CrudSlotEntry | undefined) => {
    const next: TableEntry = { ...table }
    if (slot === undefined) delete next[crud]
    else next[crud] = slot
    onChange(next)
  }
  const createSlot = (crud: CrudKind) => setSlot(crud, { ...SLOT_DEFAULTS[crud] })
  const editMeta = (patch: Record<string, unknown>) => {
    const next: TableEntry = { ...table, ...patch }
    for (const [k, v] of Object.entries(patch)) if (v === undefined) delete next[k]
    onChange(next)
  }
  const editSlot = (crud: CrudKind, patch: Record<string, unknown>) => {
    const cur = (table[crud] ?? {}) as CrudSlotEntry
    const slot: CrudSlotEntry = { ...cur, ...patch }
    for (const [k, v] of Object.entries(patch)) if (v === undefined) delete slot[k]
    setSlot(crud, slot)
  }
  const removeWholeTable = async () => {
    const ok = await modals.confirm({
      title: t('settings.tables.deleteTable'),
      message: t('settings.tables.confirmDelete', { name: table.name }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    onDelete()
  }
  // Delete a single CRUD slot off the table (the operator created one they don't want, or a v1
  // migration left a stray _put/_post/_delete). The slot's synthesised name `<base>_<crud>` may be
  // referenced elsewhere; deleting it can leave a dangling ref (the Integrity tab flags those).
  const removeSlot = async (crud: CrudKind) => {
    const ok = await modals.confirm({
      title: t('settings.tables.deleteSlot'),
      message: t('settings.tables.confirmDeleteSlot', { crud: crud.toUpperCase(), name: `${table.name}_${crud}` }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    setSlot(crud, undefined)
  }

  // Pre-pick the subset schemas — flatten x_group so the inner SchemaForm has no nested tabs.
  const generalSchema: JsonSchema = pickSchemaProperties(tableDefSchema, GENERAL_KEYS)
  const readBodySchema: JsonSchema = pickSchemaProperties(crudSlotSchema, READ_BODY_KEYS)
  const writeBodySchema: JsonSchema = pickSchemaProperties(crudSlotSchema, WRITE_BODY_KEYS)

  const renderSlot = (crud: CrudKind): ReactNode => {
    const slot = table[crud] as CrudSlotEntry | undefined
    if (!slot) {
      return (
        <Empty>
          <Stack gap={10} style={{ alignItems: 'center' }}>
            <div>{t('settings.tables.missingSlot', { crud: crud.toUpperCase(), name: `${table.name}_${crud}` })}</div>
            <Button $variant="ghost" $size="sm" onClick={() => createSlot(crud)}>
              <Plus size={13} /> {t('settings.tables.createSlot', { crud: crud.toUpperCase() })}
            </Button>
          </Stack>
        </Empty>
      )
    }
    const schema = crud === 'get' ? readBodySchema : writeBodySchema
    const keys = crud === 'get' ? READ_BODY_KEYS : WRITE_BODY_KEYS
    return (
      <>
        <Row style={{ alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
          <Sub style={{ marginBottom: 0 }}><code style={{ fontFamily: fonts.mono }}>{`${table.name}_${crud}`}</code></Sub>
          <Button $variant="ghost" $size="sm" onClick={() => removeSlot(crud)} style={{ color: colors.red.main }}>
            <Trash2 size={13} /> {t('common.delete')}
          </Button>
        </Row>
        <SqlStatementContext.Provider value={CRUD_TO_STMT[crud]}>
          <SchemaForm
            schema={schema}
            defs={defs}
            value={slot}
            onChange={(v) => {
              // SchemaForm returns the whole picked value; convert to a patch so we don't drop keys
              // we didn't pick.
              const patch: Record<string, unknown> = {}
              for (const k of keys) patch[k] = v[k]
              editSlot(crud, patch)
            }}
          />
        </SqlStatementContext.Provider>
      </>
    )
  }

  const renderTab = (): ReactNode => {
    if (tab === 'general') {
      return (
        <>
          <Sub>{t('settings.tables.generalHint')}</Sub>
          <SchemaForm
            schema={generalSchema}
            defs={defs}
            value={{ label: table.label ?? undefined, description: table.description ?? undefined }}
            onChange={(v) => {
              const patch: Record<string, unknown> = {}
              for (const k of GENERAL_KEYS) patch[k] = v[k]
              editMeta(patch)
            }}
          />
        </>
      )
    }
    return renderSlot(TAB_TO_CRUD[tab]!)
  }

  return (
    <SqlConnectorContext.Provider value={connectorName}>
    <div>
      <SubNav
        onBack={onBack}
        backLabel={t('settings.tables.backToTables')}
        lead={false}
        crumbs={[{ label: (
          <Title as="span">{table.name} <span className="muted">· {filledSlots.length} {t('settings.tables.slot', { count: filledSlots.length })}</span></Title>
        ) }]}
        right={(
        <Row gap={6}>
          {screenLink && onOpenScreen && (
            <Button $variant="ghost" $size="sm" onClick={() => onOpenScreen(screenLink.app, screenLink.id)}
              title={t('settings.tables.openInScreens')}>
              <ExternalLink size={13} /> {t('settings.tables.openInScreens')}
            </Button>
          )}
          {onFindUsages && (
            <Button $variant="ghost" $size="sm" onClick={onFindUsages} title={t('findUsages.button')} aria-label={t('findUsages.button')}>
              <GitBranch size={13} />
            </Button>
          )}
          {onRename && (
            <Button $variant="ghost" $size="sm" onClick={onRename} title={t('settings.rename.button')} aria-label={t('settings.rename.button')}>
              <Edit3 size={13} />
            </Button>
          )}
          {onChangeType && (
            <Button $variant="ghost" $size="sm" onClick={onChangeType} title={t('settings.connectors.changeTypeButton')} aria-label={t('settings.connectors.changeTypeButton')}>
              <Shuffle size={13} />
            </Button>
          )}
          {onMove && (
            <Button $variant="ghost" $size="sm" onClick={onMove} title={t('settings.connectors.moveButton')} aria-label={t('settings.connectors.moveButton')}>
              <ArrowRightLeft size={13} />
            </Button>
          )}
          {onDuplicate && (
            <Button $variant="ghost" $size="sm" onClick={onDuplicate} title={t('settings.tables.duplicate')} aria-label={t('settings.tables.duplicate')}>
              <Copy size={13} />
            </Button>
          )}
          <Button $variant="ghost" $size="sm" onClick={removeWholeTable} style={{ color: colors.red.main }} title={t('common.delete')} aria-label={t('common.delete')}>
              <Trash2 size={13} />
            </Button>
        </Row>
        )}
      />
      {/* the table as its stages: what it is, then its four statements — each card says whether it is set */}
      <StageStrip
        dense
        current={tab}
        onPick={(k) => setTab(k as TabKey)}
        stages={[
          { id: 'general', tone: 'blue', icon: <Tag size={15} />, title: t('settings.tables.tab.general'),
            what: (typeof table.label === 'string' && table.label) || table.name,
            more: typeof table.description === 'string' ? table.description : '' },
          ...tableSlots(table).map((sl) => ({
            id: sl.crud, tone: (sl.crud === 'get' ? 'green' : 'purple') as StageTone, icon: SLOT_ICON[sl.crud], title: t(`settings.tables.tab.${sl.crud}`),
            what: !sl.set ? t('settings.tables.stage.notSet') : sl.blank ? t('settings.tables.stage.blank') : t('settings.tables.stage.set'),
            more: sl.crud === 'get' || !sl.set ? '' : sl.writable ? t('settings.tables.stage.writable') : t('settings.tables.stage.notWritable'),
            warn: sl.set && sl.crud !== 'get' && !sl.writable,
          })),
        ]}
      />
      {renderTab()}
    </div>
    </SqlConnectorContext.Provider>
  )
}
