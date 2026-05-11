import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Globe, Play } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import type { ApiEndpointMeta, ApiResult, ConnectorMeta } from '../../types/connectors'
import { PageLayout, Card, Button, Input, Field, Banner, Centered, Tag, Mono, Row, Stack, Pre, SpinnerRing, FieldLabel } from '../../common'
import { colors, fontSize, fonts } from '../../theme'

const Title = styled.span`
  font-family: ${fonts.mono};
`
const Meta = styled.div`
  font-size: ${fontSize.sm};
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
`
const OkText = styled.span<{ $ok: boolean }>`
  color: ${({ $ok }) => ($ok ? colors.green.main : colors.red.main)};
  font-weight: 600;
`
const ExtractCode = styled.code`
  font-family: ${fonts.mono};
  font-size: ${fontSize.sm};
  color: ${colors.blue.main};
  word-break: break-all;
`

export default function HttpRunner() {
  const { t } = useTranslation()
  const { connector = '', endpoint = '' } = useParams()
  const [meta, setMeta] = useState<ApiEndpointMeta | null>(null)
  const [metaErr, setMetaErr] = useState<string | null>(null)
  const [params, setParams] = useState<Record<string, string>>({})
  const [result, setResult] = useState<ApiResult | null>(null)
  const [runErr, setRunErr] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    setMeta(null)
    setMetaErr(null)
    setResult(null)
    api
      .get<ConnectorMeta>(`/api/connectors/${encodeURIComponent(connector)}`)
      .then((c) => {
        if (c.type !== 'api') throw new Error(`${connector} is not an API connector`)
        const e = c.endpoints.find((x) => x.name === endpoint)
        if (!e) throw new Error(`endpoint ${endpoint} not found on ${connector}`)
        setMeta(e)
        const init: Record<string, string> = {}
        for (const p of e.params) if (p.default != null) init[p.name] = p.default
        setParams(init)
      })
      .catch((e) => setMetaErr(e instanceof ApiError ? e.message : String(e)))
  }, [connector, endpoint])

  const call = useCallback(async () => {
    setBusy(true)
    setRunErr(null)
    const sent: Record<string, string> = {}
    for (const [k, v] of Object.entries(params)) if (v !== '') sent[k] = v
    try {
      setResult(
        await api.post<ApiResult>(`/api/http/${encodeURIComponent(connector)}/${encodeURIComponent(endpoint)}`, {
          params: sent,
        }),
      )
    } catch (e) {
      setRunErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }, [params, connector, endpoint])

  if (metaErr)
    return (
      <PageLayout icon={<Globe size={18} />} title={`${connector}.${endpoint}`}>
        <Banner $tone="error">{metaErr}</Banner>
      </PageLayout>
    )
  if (!meta)
    return (
      <PageLayout icon={<Globe size={18} />} title={`${connector}.${endpoint}`}>
        <Centered />
      </PageLayout>
    )

  return (
    <PageLayout
      icon={<Globe size={18} />}
      title={
        <>
          <Title>
            {connector}.{endpoint}
          </Title>
          <Tag $tone="blue">{meta.method}</Tag>
          <Mono>{meta.path}</Mono>
        </>
      }
      description={meta.label || meta.description || undefined}
    >
      <Stack gap={14}>
        <Card>
          <Row align="flex-end">
            {meta.params.map((p) => (
              <div key={p.name} style={{ minWidth: 160 }}>
                <Field label={p.label ?? p.name}>
                  <Input
                    type="text"
                    placeholder={p.default != null ? t('table.defaultPrefix', { v: p.default }) : ''}
                    value={params[p.name] ?? ''}
                    onChange={(e) => setParams((s) => ({ ...s, [p.name]: e.target.value }))}
                  />
                </Field>
              </div>
            ))}
            <Button $variant="primary" onClick={call} disabled={busy}>
              {busy ? <SpinnerRing size={14} thickness={2} /> : <Play size={14} />}
              {busy ? t('common.calling') : t('common.call')}
            </Button>
          </Row>
          {runErr && (
            <div style={{ marginTop: 12 }}>
              <Banner $tone="error">{runErr}</Banner>
            </div>
          )}
        </Card>

        {result && (
          <Stack gap={10}>
            <Meta>
              <OkText $ok={result.success}>
                {result.success ? t('http.ok') : t('http.failed')} · HTTP {result.status_code}
              </OkText>
              <Mono>{result.url}</Mono>
              <span style={{ color: colors.text.muted }}>
                {result.duration_ms.toFixed(1)} {t('common.ms')}
              </span>
            </Meta>
            {result.error && <Banner $tone="error">{result.error}</Banner>}
            {result.extracted !== null && result.extracted !== undefined && (
              <div>
                <FieldLabel>{t('http.responseField')}</FieldLabel>
                <ExtractCode>{JSON.stringify(result.extracted)}</ExtractCode>
              </div>
            )}
            <Pre>{JSON.stringify(result.json ?? result.body, null, 2)}</Pre>
          </Stack>
        )}
      </Stack>
    </PageLayout>
  )
}
