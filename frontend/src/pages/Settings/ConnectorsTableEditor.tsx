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
import { ArrowLeft, Copy, Edit3, ExternalLink, Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button, Row, SchemaForm, SqlConnectorContext, Stack, useModals, type JsonSchema } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import {
  CRUD_KINDS,
  type CrudKind,
  type CrudSlotEntry,
  type TableEntry,
  pickSchemaProperties,
} from './connectorTables'

// ── styled bits ───────────────────────────────────────────────────────────────
const Header = styled(Row)`
  align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 12px;
`
const Title = styled.strong`
  font-family: ${fonts.mono}; color: ${colors.text.primary}; font-size: ${fontSize.base};
  & .muted { color: ${colors.text.muted}; font-weight: 400; margin-left: 6px; }
`
const BackBtn = styled.button`
  display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 10px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border}; background: ${colors.bg.card}; color: ${colors.text.secondary};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`
const TabsBar = styled.div`display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 14px; border-bottom: 1px solid ${colors.border}; padding-bottom: 6px;`
const TabBtn = styled.button<{ $active?: boolean; $missing?: boolean }>`
  height: 30px; padding: 0 12px; border-radius: ${radius.sm}; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active, $missing }) => ($active ? colors.blue.main : $missing ? colors.text.muted : colors.text.secondary)};
  display: inline-flex; align-items: center; gap: 6px;
  & .badge { font-size: ${fontSize.micro}; color: ${colors.text.muted}; opacity: 0.7; }
  &:hover { color: ${colors.text.primary}; background: ${({ $active }) => ($active ? colors.blue.bg : 'var(--hover-subtle)')}; }
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 30px 6px; text-align: center;`
const Sub = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; margin-bottom: 10px;`

// ── tabs ──────────────────────────────────────────────────────────────────────
type TabKey = 'general' | CrudKind

const TAB_ORDER: TabKey[] = ['general', 'get', 'put', 'post', 'delete']
const TAB_TO_CRUD: Partial<Record<TabKey, CrudKind>> = { get: 'get', put: 'put', post: 'post', delete: 'delete' }

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
  /** When the parent finds a Screen whose `read_query` matches `<name>_get`, it passes `{app, id}`
   *  so the header shows an "Open visual builder" button. */
  screenLink?: { app: string; id: string } | null
  onOpenScreen?: (app: string, id: string) => void
}

export default function ConnectorsTableEditor({
  table, connectorName, tableDefSchema, crudSlotSchema, defs, onChange, onDelete, onBack, onDuplicate, onRename, screenLink, onOpenScreen,
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
        <Sub><code style={{ fontFamily: fonts.mono }}>{`${table.name}_${crud}`}</code></Sub>
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
      <Header>
        <BackBtn type="button" onClick={onBack}><ArrowLeft size={13} /> {t('settings.tables.backToTables')}</BackBtn>
        <Title>{table.name} <span className="muted">· {filledSlots.length} {t('settings.tables.slot', { count: filledSlots.length })}</span></Title>
        <Row gap={6}>
          {screenLink && onOpenScreen && (
            <Button $variant="ghost" $size="sm" onClick={() => onOpenScreen(screenLink.app, screenLink.id)}
              title={t('settings.tables.openInScreens')}>
              <ExternalLink size={13} /> {t('settings.tables.openInScreens')}
            </Button>
          )}
          {onRename && (
            <Button $variant="ghost" $size="sm" onClick={onRename}>
              <Edit3 size={13} /> {t('settings.rename.button')}
            </Button>
          )}
          {onDuplicate && (
            <Button $variant="ghost" $size="sm" onClick={onDuplicate}>
              <Copy size={13} /> {t('settings.tables.duplicate')}
            </Button>
          )}
          <Button $variant="ghost" $size="sm" onClick={removeWholeTable} style={{ color: colors.red.main }}>
            <Trash2 size={13} /> {t('common.delete', 'Delete')}
          </Button>
        </Row>
      </Header>
      <TabsBar>
        {TAB_ORDER.map((k) => {
          const crud = TAB_TO_CRUD[k]
          // General always applies (it edits table-level metadata). A CRUD tab is "missing" when
          // that slot doesn't exist yet — the badge invites the operator to create it.
          const missing = crud ? !table[crud] : false
          return (
            <TabBtn key={k} type="button" $active={tab === k} $missing={missing} onClick={() => setTab(k)}>
              {t(`settings.tables.tab.${k}`)}{missing && <span className="badge">+</span>}
            </TabBtn>
          )
        })}
      </TabsBar>
      {renderTab()}
    </div>
    </SqlConnectorContext.Provider>
  )
}
