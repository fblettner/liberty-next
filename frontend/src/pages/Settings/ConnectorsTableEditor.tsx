// Unified per-table editor: presents a `<base>_<get|put|post|delete>` quartet (or whichever slots
// exist) as ONE configurable object — a tab strip across the top:
//   General · Columns · Read · Update · Insert · Delete
// `General` (label/description/auto_load/max_rows/key_columns) and `Columns` (the display hints) are
// table-level — `columns` is a result-schema annotation, it only lives on the read query, so both
// tabs write to `<base>_get`. The four CRUD tabs edit only the body of their respective query
// (sql / params / writable). Missing slot → a "+ Create" button that adds the empty query stub.
// No rename yet (delete + re-add), matching the rest of the Phase-7 builders.
import { useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { ArrowLeft, Copy, Plus, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button, Row, SchemaForm, SchemaNavigator, Stack, type JsonSchema } from '../../common'
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
type TabKey = 'general' | 'columns' | CrudKind

const TAB_ORDER: TabKey[] = ['general', 'columns', 'get', 'put', 'post', 'delete']
const TAB_TO_CRUD: Partial<Record<TabKey, CrudKind>> = { get: 'get', put: 'put', post: 'post', delete: 'delete' }

// table-level fields (sit on the _get query — only place where `columns` etc. make sense)
const GENERAL_KEYS = ['label', 'description', 'auto_load', 'max_rows', 'key_columns']
const COLUMNS_KEYS = ['columns']
// per-query body — `name` is left out (renames break grouping); writable hidden for read.
const READ_BODY_KEYS = ['sql', 'params']
const WRITE_BODY_KEYS = ['sql', 'params', 'writable']

export interface ConnectorsTableEditorProps {
  base: string
  slots: Partial<Record<CrudKind, QuerySlot>>
  queries: Record<string, unknown>[]
  queryDefSchema: JsonSchema
  defs: Record<string, JsonSchema>
  onChangeQueries: (next: Record<string, unknown>[]) => void
  onBack: () => void
  onDuplicate?: () => void
}

export default function ConnectorsTableEditor({
  base, slots, queries, queryDefSchema, defs, onChangeQueries, onBack, onDuplicate,
}: ConnectorsTableEditorProps) {
  const { t } = useTranslation()
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
  const removeWholeTable = () => {
    if (!window.confirm(t('settings.tables.confirmDelete', { name: base }))) return
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
  const columnsSchema: JsonSchema = { ...pickSchemaProperties(queryDefSchema, COLUMNS_KEYS), $defs: defs }
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
    if (tab === 'columns') {
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
          <Sub>{t('settings.tables.columnsHint')}</Sub>
          <SchemaNavigator
            root={{
              label: t('settings.tables.columnsCrumb', { name: base }),
              schema: columnsSchema,
              value: { columns: (getSlot.query.columns as unknown[] | undefined) ?? [] },
              onChange: (v) => editGet({ columns: Array.isArray(v.columns) && (v.columns as unknown[]).length ? v.columns : undefined }),
            }}
          />
        </>
      )
    }
    const crud = TAB_TO_CRUD[tab]!
    return renderQueryBody(crud)
  }

  return (
    <div>
      <Header>
        <BackBtn type="button" onClick={onBack}><ArrowLeft size={13} /> {t('settings.tables.backToTables')}</BackBtn>
        <Title>{base} <span className="muted">· {filledSlots.length} {t('settings.tables.slot', { count: filledSlots.length })}</span></Title>
        <Row gap={6}>
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
          const missing = crud ? !slots[crud] : (!getSlot && (k === 'general' || k === 'columns'))
          return (
            <TabBtn key={k} type="button" $active={tab === k} $missing={missing} onClick={() => setTab(k)}>
              {t(`settings.tables.tab.${k}`)}{missing && <span className="badge">+</span>}
            </TabBtn>
          )
        })}
      </TabsBar>
      {renderTab()}
    </div>
  )
}
