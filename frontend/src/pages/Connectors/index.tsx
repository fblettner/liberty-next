import { useMemo } from 'react'
import { Link } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { LayoutGrid, Database, Globe } from 'lucide-react'
import { PageLayout, Card, Banner, Centered, Tag, Mono, Stack } from '../../common'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { SqlQueryMeta } from '../../types/connectors'
import { groupQueriesByTable, CRUD_KINDS, type CrudKind } from '../Settings/connectorTables'

const ConnHead = styled.div`
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
  font-size: ${fontSize.md};
  font-weight: 600;
  color: ${colors.text.primary};
`

const ItemList = styled.ul`
  list-style: none;
  margin: 0;
  padding: 0;
`

const Item = styled.li`
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 7px 6px;
  border-radius: ${radius.sm};
  border-top: 1px solid ${colors.border};
  &:first-of-type { border-top: none; }
  &:hover { background: var(--hover-subtle); }
`

const ItemName = styled(Link)`
  font-family: ${fonts.mono};
  font-size: ${fontSize.base};
  font-weight: 600;
  color: ${colors.blue.main};
  min-width: 220px;
`

const ItemDesc = styled.span`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
`

// Per-table block — groups the `<base>_get|put|post|delete` quartet under the table's friendly
// description (the read query's `description`, falls back to its `label`, then the base name).
// Same grouping the Settings → Connectors Tables view uses, surfaced on the read-only catalog
// page so operators see the screen-level identity instead of four near-identical CRUD names.
const TableBlock = styled.div`
  display: flex; flex-direction: column; gap: 4px;
  padding: 8px 8px 6px;
  border-radius: ${radius.md};
  border: 1px solid ${colors.border};
  background: ${colors.bg.input};
`
const TableHead = styled.div`
  display: flex; align-items: baseline; gap: 10px; padding: 0 4px 4px;
`
const TableTitle = styled(Link)`
  font-family: ${fonts.sans};
  font-size: ${fontSize.md};
  font-weight: 600;
  color: ${colors.text.primary};
  flex-shrink: 0;
  &:hover { color: ${colors.blue.main}; }
`
const TableMono = styled.span`
  font-family: ${fonts.mono};
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
`
const Slot = styled(Link)<{ $on?: boolean }>`
  display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: ${radius.sm};
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; font-weight: 600;
  border: 1px solid ${({ $on }) => ($on ? colors.green.border : colors.border)};
  color: ${({ $on }) => ($on ? colors.green.main : colors.text.muted)};
  background: ${({ $on }) => ($on ? colors.green.bg : 'transparent')};
  pointer-events: ${({ $on }) => ($on ? 'auto' : 'none')};
  opacity: ${({ $on }) => ($on ? 1 : 0.5)};
  text-decoration: none;
  &:hover { border-color: ${colors.blue.border}; color: ${colors.blue.main}; }
`
const Slots = styled.div`display: inline-flex; gap: 4px; flex-shrink: 0;`

// Group the connector's queries into table-level blocks, then render each block with its
// description heading + CRUD slot chips + a footer for any loose (non-CRUD) queries.
function renderSqlTables(connectorName: string, queries: SqlQueryMeta[], t: (k: string) => string) {
  const grouped = groupQueriesByTable(queries as unknown as Record<string, unknown>[])
  return (
    <Stack gap={8}>
      {grouped.tables.map((g) => {
        // Read-query description is the friendly table title (v1's tbl_label). Falls back to
        // the read query's label, then the base name (the technical prefix). The clickable
        // title routes to the read query — that's the screen the user opens.
        const get = g.slots.get?.query as Record<string, unknown> | undefined
        const description = (typeof get?.description === 'string' && get.description) || null
        const label = (typeof get?.label === 'string' && get.label) || null
        const title = description || label || g.base
        const getName = g.slots.get?.name
        const titleHref = getName
          ? `/sql/${encodeURIComponent(connectorName)}/${encodeURIComponent(getName)}`
          : '#'
        return (
          <TableBlock key={g.base}>
            <TableHead>
              <TableTitle to={titleHref}>{title}</TableTitle>
              <TableMono>{g.base}</TableMono>
              <Slots>
                {CRUD_KINDS.map((c: CrudKind) => {
                  const slot = g.slots[c]
                  const href = slot ? `/sql/${encodeURIComponent(connectorName)}/${encodeURIComponent(slot.name)}` : '#'
                  return (
                    <Slot key={c} to={href} $on={!!slot} title={slot ? slot.name : `${g.base}_${c} (missing)`}>
                      {c.toUpperCase()}
                    </Slot>
                  )
                })}
              </Slots>
            </TableHead>
          </TableBlock>
        )
      })}
      {grouped.loose.length > 0 && (
        // Non-CRUD-named queries (utility queries / reports / one-off SELECTs) — kept in a flat
        // list under the tables so they're still reachable. No grouping, since they don't belong
        // to a table; show description as the friendly text.
        <Stack gap={2}>
          {grouped.loose.map(({ name, query }) => {
            const q = query as Record<string, unknown>
            const desc = (typeof q.description === 'string' && q.description) || (typeof q.label === 'string' && q.label) || ''
            const writable = !!q.writable
            const stmt = typeof q.statement_type === 'string' ? q.statement_type : ''
            return (
              <Item key={name}>
                <ItemName to={`/sql/${encodeURIComponent(connectorName)}/${encodeURIComponent(name)}`}>{name}</ItemName>
                {stmt && <Tag>{stmt}</Tag>}
                {writable && <Tag $tone="orange">{t('connectors.writable')}</Tag>}
                <ItemDesc>{desc}</ItemDesc>
              </Item>
            )
          })}
        </Stack>
      )}
    </Stack>
  )
}

export default function Connectors() {
  const { t } = useTranslation()
  const { connectors: all, error, currentApp } = useWorkspace()
  // The header workspace picker scopes the list to one connector ("(all apps)" = no scope).
  const connectors = useMemo(
    () => (all == null ? null : currentApp ? all.filter((c) => c.name === currentApp) : all),
    [all, currentApp],
  )

  const body = error ? (
    <Banner $tone="error">{error}</Banner>
  ) : !connectors ? (
    <Centered />
  ) : connectors.length === 0 ? (
    <ItemDesc>{t('connectors.empty')}</ItemDesc>
  ) : (
    <Stack gap={14}>
      {connectors.map((c) => (
        <Card key={c.name}>
          <ConnHead>
            {c.type === 'sql' ? <Database size={16} color="var(--blue-main)" /> : <Globe size={16} color="var(--blue-main)" />}
            {c.name}
            <Tag $tone="blue">{c.type}</Tag>
            {c.type === 'api' && c.base_url ? <Mono>{c.base_url}</Mono> : null}
          </ConnHead>
          {c.type === 'sql' && renderSqlTables(c.name, c.queries, t)}
          {c.type === 'api' && (
            <ItemList>
              {c.endpoints.map((e) => (
                <Item key={e.name}>
                  <ItemName to={`/http/${encodeURIComponent(c.name)}/${encodeURIComponent(e.name)}`}>{e.name}</ItemName>
                  <Tag>{e.method}</Tag>
                  <Mono>{e.path}</Mono>
                  <ItemDesc>{e.description ?? e.label ?? ''}</ItemDesc>
                </Item>
              ))}
            </ItemList>
          )}
        </Card>
      ))}
    </Stack>
  )

  return (
    <PageLayout icon={<LayoutGrid size={18} />} title={t('connectors.title')} description={t('connectors.subtitle')}>
      {body}
    </PageLayout>
  )
}
