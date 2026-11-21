// The studio's two dialogs on a service (a shared action): RUN it with inputs and see the answer it
// would give as an endpoint, and EXPOSE it as an endpoint of an API connector.
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Play, Share2 } from 'lucide-react'
import { Banner, Button, Checkbox, Field, Input, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SearchSelect } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'

export interface StudioInput { name: string; label: string | null; default: string | null; required: boolean }
export interface StudioEndpoint { connector: string; endpoint: string; label: string | null; writable: boolean }
export interface StudioService { id: string; label: string | null; description: string | null; steps: number; inputs: StudioInput[]; endpoints: StudioEndpoint[] }
interface RunResult { ok: boolean; error?: string; errors?: string[]; messages?: string[]; steps?: string[]; returned?: Record<string, unknown>; duration_ms?: number }

const Grid = styled.div`display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px;`
const Answer = styled.pre`
  margin: 0; padding: 10px 12px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.primary}; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; white-space: pre-wrap; overflow-wrap: anywhere;
  max-height: 260px; overflow: auto;
`
const Head = styled.div`font-size: ${fontSize.micro}; font-weight: 600; color: ${colors.text.secondary}; text-transform: uppercase; letter-spacing: 0.04em;`
const Steps = styled.ol`margin: 0; padding-left: 18px; color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};`
const Note = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; line-height: 1.5;`

export function StudioRunModal({ service, onClose }: { service: StudioService; onClose: () => void }) {
  const { t } = useTranslation()
  const [values, setValues] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<RunResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const run = async () => {
    setBusy(true); setError(null)
    try {
      setResult(await api.post<RunResult>('/admin/studio/run', { action: service.id, inputs: values }))
    } catch (e) {
      setResult(null); setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }
  // what the endpoint would answer: what the action returns, with its messages
  const answer = useMemo(() => {
    if (!result?.ok) return ''
    return JSON.stringify({ ...(result.returned ?? {}), ...(result.messages?.length ? { messages: result.messages } : {}) }, null, 2)
  }, [result])
  return (
    <Overlay onClick={onClose} style={{ zIndex: 600 }}>
      <Modal style={{ width: 'min(720px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('settings.studio.runTitle', { id: service.label || service.id })}</ModalHeader>
        <ModalBody>
          <Note>{t('settings.studio.runHint')}</Note>
          {service.inputs.length > 0 ? (
            <Grid>
              {service.inputs.map((i) => (
                <Field key={i.name} label={`${i.label || i.name}${i.required ? ' *' : ''}`}>
                  <Input
                    type="text" value={values[i.name] ?? ''} placeholder={i.default != null && i.default !== '' ? t('table.defaultPrefix', { v: i.default }) : ''}
                    onChange={(e) => setValues((v) => ({ ...v, [i.name]: e.target.value }))}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !busy) void run() }}
                  />
                </Field>
              ))}
            </Grid>
          ) : <Note>{t('settings.studio.noInputs')}</Note>}
          {error && <Banner $tone="error">{error}</Banner>}
          {result && !result.ok && <Banner $tone="error">{result.error || (result.errors ?? []).join(' · ') || t('settings.studio.failed')}</Banner>}
          {result?.ok && (
            <>
              <Banner $tone="ok">{t('settings.studio.ran', { ms: Math.round(result.duration_ms ?? 0) })}</Banner>
              <Head>{t('settings.studio.answer')}</Head>
              <Answer>{answer}</Answer>
            </>
          )}
          {result && (result.steps?.length ?? 0) > 0 && (
            <>
              <Head>{t('settings.studio.stepsRan')}</Head>
              <Steps>{result.steps!.map((s, k) => <li key={k}>{s}</li>)}</Steps>
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" onClick={onClose}>{t('common.close')}</Button>
          <Button $variant="primary" disabled={busy} onClick={() => void run()}><Play size={14} /> {t('settings.studio.run')}</Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

export function StudioExposeModal({ service, connectors, defaultConnector, onClose, onDone }: {
  service: StudioService; connectors: string[]; defaultConnector: string; onClose: () => void; onDone: (message: string) => void
}) {
  const { t } = useTranslation()
  // the installation's API connectors — and the studio's own, created on first use, when it is not there yet
  const options = useMemo(() => {
    const list = connectors.map((c) => ({ value: c, label: c }))
    if (!connectors.includes(defaultConnector)) list.push({ value: defaultConnector, label: t('settings.studio.newConnector', { name: defaultConnector }) })
    return list
  }, [connectors, defaultConnector, t])
  const [connector, setConnector] = useState(connectors.includes(defaultConnector) || connectors.length === 0 ? defaultConnector : connectors[0])
  const [name, setName] = useState(service.id)
  const [writable, setWritable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const expose = async () => {
    setBusy(true); setError(null)
    try {
      const made = await api.post<{ connector: string; endpoint: string; permission: string; path: string }>(
        '/admin/studio/expose', { action: service.id, connector, name: name.trim() || service.id, writable })
      onDone(t('settings.studio.exposed', { path: made.path, permission: made.permission }))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }
  return (
    <Overlay onClick={onClose} style={{ zIndex: 600 }}>
      <Modal style={{ width: 'min(620px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('settings.studio.exposeTitle', { id: service.label || service.id })}</ModalHeader>
        <ModalBody>
          <Note>{t('settings.studio.exposeHint')}</Note>
          <Field label={t('settings.studio.connector')}>
            <SearchSelect value={connector} options={options} onChange={(v: string | null) => setConnector(v || defaultConnector)} />
          </Field>
          <Field label={t('settings.studio.endpointName')}>
            <Input type="text" value={name} onChange={(e) => setName(e.target.value)} style={{ fontFamily: fonts.mono }} />
          </Field>
          <Checkbox checked={writable} onChange={setWritable} label={t('settings.studio.writable')} />
          {service.inputs.length > 0 && <Note>{t('settings.studio.exposeInputs', { names: service.inputs.map((i) => i.name).join(', ') })}</Note>}
          {error && <Banner $tone="error">{error}</Banner>}
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" onClick={onClose}>{t('common.cancel')}</Button>
          <Button $variant="primary" disabled={busy} onClick={() => void expose()}><Share2 size={14} /> {t('settings.studio.expose')}</Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
