// Unified per-table editor: presents a `<base>_<get|put|post|delete>` quartet (or whichever slots
// exist) as ONE configurable object — a tab strip across the top:
//   General · Read · Update · Insert · Delete
// **Phase 3** — per-screen behaviour (`columns` / `auto_load` / `audit_table` / `max_rows` /
// `key_columns` / companion-query refs) has moved off `QueryDef` onto `Screen`. The connector's
// per-table editor now only edits the SQL-level pieces of each query: General (label/description
// on the read query — friendly metadata that still rides on `QueryDef`) and the four CRUD bodies
// (sql / params / writable). Columns + per-table behaviour live in the Screen Designer's
// "Columns" / "General" tabs — the "Open in Screens" link in this editor's header jumps to the
// matching screen when one exists.
//
// Missing slot → a "+ Create" button that adds the empty query stub. No rename yet (delete +
// re-add), matching the rest of the Phase-7 builders.
import { useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { ArrowLeft, Copy, Edit3, ExternalLink, Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Button, Row, SchemaForm, SqlConnectorContext, Stack, useModals, type JsonSchema } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import {
  CRUD_KINDS,
  type CrudKind,
  type QuerySlot,
  newQueryStub,
  pickSchemaProperties,
  removeQueryByName,
  replaceQueryByName,
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

// Phase 3 — General edits only the friendly metadata still on QueryDef (label/description).
// auto_load / audit / max_rows / key_columns / columns moved to Screen — edit them in the
// Screen Designer's General + Columns tabs.
const GENERAL_KEYS = ['type', 'label', 'description']
// per-query body — `name` is left out (renames break grouping); writable hidden for read.
// CRUD table queries carry no declared `params`: reads filter via the screen's FilterPanel and
// writes bind the edited row's columns (+ `:<COL>_ORIGINAL`) — neither uses ParamDefs. So the
// Params field is omitted on every table slot (it lives only on the Custom / Sequence / Lookup
// editors, where the caller actually supplies the binds).
const READ_BODY_KEYS = ['sql']
const WRITE_BODY_KEYS = ['sql', 'writable']

export interface ConnectorsTableEditorProps {
  base: string
  /** The connector this table lives in — threaded into `SqlConnectorContext` so every SQL
   *  editor in the form enables schema-aware autocomplete against that connector's pool. */
  connectorName: string
  slots: Partial<Record<CrudKind, QuerySlot>>
  queries: Record<string, unknown>[]
  queryDefSchema: JsonSchema
  defs: Record<string, JsonSchema>
  onChangeQueries: (next: Record<string, unknown>[]) => void
  onBack: () => void
  onDuplicate?: () => void
  /** Cross-file rename of the table base (all CRUD slots + screen bindings). Parent wires it to
   *  the rename endpoint; absent → no Rename button. */
  onRename?: () => void
  /** When the parent finds a Screen whose `read_query` matches this table's `_get`, it passes
   *  `{app, id}` here so the header shows an "Open in Screens" link that switches the Settings
   *  tab and pre-selects the screen. Absent → no link (no matching screen for this table). */
  screenLink?: { app: string; id: string } | null
}

export default function ConnectorsTableEditor({
  base, connectorName, slots, queries, queryDefSchema, defs, onChangeQueries, onBack, onDuplicate, onRename, screenLink,
}: ConnectorsTableEditorProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const [tab, setTab] = useState<TabKey>(slots.get ? 'general' : 'get')

  const getSlot = slots.get
  const editGet = (patch: Record<string, unknown>) => {
    if (getSlot) {
      // update an existing _get — `undefined` keys mean "unset" (the patch already shaped that).
      const next = { ...getSlot.query, ...patch }
      for (const [k, v] of Object.entries(patch)) if (v === undefined) delete next[k]
      onChangeQueries(replaceQueryByName(queries, getSlot.name, next))
    } else {
      const fresh = { ...newQueryStub(base, 'get'), ...patch }
      for (const [k, v] of Object.entries(patch)) if (v === undefined) delete fresh[k]
      onChangeQueries([...queries, fresh])
    }
  }
  const editCrud = (crud: CrudKind, patch: Record<string, unknown>) => {
    const slot = slots[crud]
    if (!slot) return
    const next = { ...slot.query, ...patch }
    for (const [k, v] of Object.entries(patch)) if (v === undefined) delete next[k]
    onChangeQueries(replaceQueryByName(queries, slot.name, next))
  }
  const createSlot = (crud: CrudKind) => {
    onChangeQueries([...queries, newQueryStub(base, crud)])
  }
  const removeWholeTable = async () => {
    const ok = await modals.confirm({
      title: t('settings.tables.deleteTable'),
      message: t('settings.tables.confirmDelete', { name: base }),
      variant: 'danger',
      confirmLabel: t('common.delete'),
    })
    if (!ok) return
    let next = queries
    for (const crud of CRUD_KINDS) {
      const slot = slots[crud]
      if (slot) next = removeQueryByName(next, slot.name)
    }
    onChangeQueries(next)
    onBack()
  }

  const filledSlots = CRUD_KINDS.filter((c) => slots[c])
  // pre-pick the subset schemas — flatten x_group so the inner SchemaForm has no nested tabs.
  const generalSchema: JsonSchema = pickSchemaProperties(queryDefSchema, GENERAL_KEYS)
  const readBodySchema: JsonSchema = pickSchemaProperties(queryDefSchema, READ_BODY_KEYS)
  const writeBodySchema: JsonSchema = pickSchemaProperties(queryDefSchema, WRITE_BODY_KEYS)

  const renderQueryBody = (crud: CrudKind): ReactNode => {
    const slot = slots[crud]
    if (!slot) {
      return (
        <Empty>
          <Stack gap={10} style={{ alignItems: 'center' }}>
            <div>{t('settings.tables.missingSlot', { crud: crud.toUpperCase(), name: `${base}_${crud}` })}</div>
            <Button $variant="ghost" $size="sm" onClick={() => createSlot(crud)}>
              <Plus size={13} /> {t('settings.tables.createSlot', { crud: crud.toUpperCase() })}
            </Button>
          </Stack>
        </Empty>
      )
    }
    const value = slot.query
    const schema = crud === 'get' ? readBodySchema : writeBodySchema
    return (
      <>
        <Sub><code style={{ fontFamily: fonts.mono }}>{slot.name}</code></Sub>
        <SchemaForm
          schema={schema}
          defs={defs}
          value={value}
          onChange={(v) => {
            // SchemaForm gives us the *whole* picked value; convert into a patch so we don't blow
            // away the keys we didn't pick (label/columns/etc on _get, query name, etc.).
            const patch: Record<string, unknown> = {}
            for (const k of crud === 'get' ? READ_BODY_KEYS : WRITE_BODY_KEYS) patch[k] = v[k]
            if (slot === getSlot) editGet(patch); else editCrud(crud, patch)
          }}
        />
      </>
    )
  }

  const renderTab = (): ReactNode => {
    if (tab === 'general') {
      if (!getSlot) return (
        <Empty>
          <Stack gap={10} style={{ alignItems: 'center' }}>
            <div>{t('settings.tables.needRead', { name: `${base}_get` })}</div>
            <Button $variant="ghost" $size="sm" onClick={() => createSlot('get')}>
              <Plus size={13} /> {t('settings.tables.createSlot', { crud: 'GET' })}
            </Button>
          </Stack>
        </Empty>
      )
      return (
        <>
          <Sub>{t('settings.tables.generalHint')}</Sub>
          <SchemaForm
            schema={generalSchema}
            defs={defs}
            value={getSlot.query}
            onChange={(v) => {
              const patch: Record<string, unknown> = {}
              for (const k of GENERAL_KEYS) patch[k] = v[k]
              editGet(patch)
            }}
          />
        </>
      )
    }
    // Phase 3 — the Columns tab has moved to the Screen Designer (a single source of truth
    // for column display metadata across the grid editor + the dialog form). The "Open in
    // Screens" link in this editor's header jumps to the matching screen.
    const crud = TAB_TO_CRUD[tab]!
    return renderQueryBody(crud)
  }

  return (
    <SqlConnectorContext.Provider value={connectorName}>
    <div>
      <Header>
        <BackBtn type="button" onClick={onBack}><ArrowLeft size={13} /> {t('settings.tables.backToTables')}</BackBtn>
        <Title>{base} <span className="muted">· {filledSlots.length} {t('settings.tables.slot', { count: filledSlots.length })}</span></Title>
        <Row gap={6}>
          {screenLink && (
            // `Link` keeps the SPA navigation in-app (no full reload) and threads the search
            // params Settings/index.tsx reads to switch tabs + pre-select the screen.
            <Button
              as={Link as unknown as React.ElementType}
              $variant="ghost" $size="sm"
              // @ts-expect-error -- Button's polymorphic prop forwarding doesn't know `to`.
              to={`/settings?tab=screens&app=${encodeURIComponent(screenLink.app)}&screen=${encodeURIComponent(screenLink.id)}`}
              title={t('settings.tables.openInScreens')}
            >
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
          <Button $variant="danger" $size="sm" onClick={removeWholeTable}>
            <Trash2 size={13} /> {t('settings.tables.deleteTable')}
          </Button>
        </Row>
      </Header>
      <TabsBar>
        {TAB_ORDER.map((k) => {
          const crud = TAB_TO_CRUD[k]
          const missing = crud ? !slots[crud] : (!getSlot && k === 'general')
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
