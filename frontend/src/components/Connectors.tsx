import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { LayoutGrid, Database, Globe } from 'lucide-react'
import { api, ApiError } from '../api'
import type { ConnectorMeta } from '../types'
import { PageLayout, Card, Banner, Centered, Tag, Mono, Stack } from '../ui'
import { colors, fontSize, fonts, radius } from '../theme'

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

export function Connectors() {
  const { t } = useTranslation()
  const [connectors, setConnectors] = useState<ConnectorMeta[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .get<{ connectors: ConnectorMeta[] }>('/api/connectors')
      .then((r) => setConnectors(r.connectors))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])

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
          <ItemList>
            {c.type === 'sql' &&
              c.queries.map((q) => (
                <Item key={q.name}>
                  <ItemName to={`/sql/${encodeURIComponent(c.name)}/${encodeURIComponent(q.name)}`}>{q.name}</ItemName>
                  <Tag>{q.statement_type}</Tag>
                  {q.writable && <Tag $tone="orange">{t('connectors.writable')}</Tag>}
                  <ItemDesc>{q.label ?? q.description ?? ''}</ItemDesc>
                </Item>
              ))}
            {c.type === 'api' &&
              c.endpoints.map((e) => (
                <Item key={e.name}>
                  <ItemName to={`/http/${encodeURIComponent(c.name)}/${encodeURIComponent(e.name)}`}>{e.name}</ItemName>
                  <Tag>{e.method}</Tag>
                  <Mono>{e.path}</Mono>
                  <ItemDesc>{e.label ?? e.description ?? ''}</ItemDesc>
                </Item>
              ))}
          </ItemList>
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
