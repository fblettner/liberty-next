import { useCallback, useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Globe, Play } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import type { ApiEndpointMeta, ApiResult, ConnectorMeta } from '../../types/connectors'
import { PageLayout, Card, Button, Input, Field, Banner, ParamInput, Centered, Tag, Mono, Row, Stack, Pre, SpinnerRing, FieldLabel } from '../../common'
import { colors, fontSize, fonts } from '../../theme'
import { useWorkspace } from '../../workspace/WorkspaceContext'
import { findMenuLabel } from '../../services/menuLabels'

const Sub = styled.span`display: inline-flex; align-items: center; gap: 8px; flex-wrap: wrap;`
// what each parameter expects, under the fields
const ParamNotes = styled.div`
  margin-top: 10px; display: flex; flex-direction: column; gap: 3px;
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; white-space: pre-wrap; word-break: break-word;
  b { color: ${colors.text.secondary}; font-weight: 600; }
  code { font-family: ${fonts.mono}; }
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

export default function HttpRunner({ connector, endpoint }: { connector: string; endpoint: string }) {
  const { t } = useTranslation()
  const { menus } = useWorkspace()
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

  const menuLabel = findMenuLabel(menus, { kind: 'http', connector, target: endpoint })
  const friendlyName = meta?.description || meta?.label || menuLabel || `${connector}.${endpoint}`

  if (metaErr)
    return (
      <PageLayout icon={<Globe size={18} />} title={friendlyName}>
        <Banner $tone="error">{metaErr}</Banner>
      </PageLayout>
    )
  if (!meta)
    return (
      <PageLayout icon={<Globe size={18} />} title={friendlyName}>
        <Centered />
      </PageLayout>
    )

  return (
    <PageLayout
      icon={<Globe size={18} />}
      title={
        <>
          {friendlyName}
          <Tag $tone="blue">{meta.method}</Tag>
          <Mono>{meta.path}</Mono>
        </>
      }
      description={
        <Sub>
          <Mono>{connector}.{endpoint}</Mono>
          {menuLabel && menuLabel !== friendlyName ? <span>· {menuLabel}</span> : null}
        </Sub>
      }
    >
      <Stack gap={14}>
        <Card>
          <Row align="flex-end">
            {meta.params.map((p) => (
              <div key={p.name} style={{ minWidth: 160 }}>
                <Field label={p.label ?? p.name}>
                  {p.lookup || p.choices?.length ? (
                    <ParamInput def={p} value={params[p.name] ?? ''} names={meta.params.map((x) => x.name)} values={params}
                      onChange={(v) => setParams((s) => ({ ...s, [p.name]: v }))} />
                  ) : (
                  <Input
                    type="text"
                    placeholder={p.default != null && p.default !== '' ? t('table.defaultPrefix', { v: p.default }) : (p.example ?? '')}
                    title={[p.help, p.example ? t('settings.api.endpoint.paramExampleIs', { v: p.example }) : ''].filter(Boolean).join(' — ')}
                    value={params[p.name] ?? ''}
                    onChange={(e) => setParams((s) => ({ ...s, [p.name]: e.target.value }))}
                  />
                  )}
                </Field>
              </div>
            ))}
            <Button $variant="primary" onClick={call} disabled={busy}>
              {busy ? <SpinnerRing size={14} thickness={2} /> : <Play size={14} />}
              {busy ? t('common.calling') : t('common.call')}
            </Button>
          </Row>
          {meta.params.some((p) => p.help || p.example) && (
            <ParamNotes>
              {meta.params.filter((p) => p.help || p.example).map((p) => (
                <span key={p.name}>
                  <b>{p.label ?? p.name}</b>{p.help ? ` — ${p.help}` : ''}
                  {p.example ? <> — {t('settings.api.endpoint.paramExampleIs', { v: '' })}<code>{p.example}</code></> : null}
                </span>
              ))}
            </ParamNotes>
          )}
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
            {result.warnings && <Banner $tone="warning">{result.warnings}</Banner>}
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
