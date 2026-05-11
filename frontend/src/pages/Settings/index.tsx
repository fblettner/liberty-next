import '../../services/monaco' // side effect: bundle Monaco locally + configure its worker (no CDN)
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { SlidersHorizontal, Save, RefreshCw } from 'lucide-react'
import MonacoEditor from '@monaco-editor/react'
import { api, ApiError } from '../../api/client'
import { PageLayout, Button, Banner, Centered, Row, Stack, SpinnerRing, Mono, useIsLight } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

interface ConfigDoc {
  path: string
  content: string
}

const EditorFrame = styled.div`
  border: 1px solid ${colors.border};
  border-radius: ${radius.md};
  overflow: hidden;
  height: 58vh;
  min-height: 320px;
  /* Monaco paints its own background; keep it from clashing with the page card. */
  background: ${colors.bg.input};
`

const Hint = styled.p`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
  line-height: 1.5;
  margin: 0;
`

export default function Settings() {
  const { t } = useTranslation()
  const isLight = useIsLight()
  const [doc, setDoc] = useState<ConfigDoc | null>(null)
  const [content, setContent] = useState('')
  const [dirty, setDirty] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api
      .get<ConfigDoc>('/admin/config/connectors')
      .then((d) => {
        setDoc(d)
        setContent(d.content)
      })
      .catch((e) =>
        setError(e instanceof ApiError ? (e.status === 403 ? t('settings.superuserRequired') : e.message) : String(e)),
      )
  }, [t])

  async function save() {
    setBusy(true)
    setError(null)
    setStatus(null)
    try {
      await api.put<{ saved: boolean }>('/admin/config/connectors', { content })
      setDirty(false)
      setStatus(t('settings.savedHint'))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  async function reload() {
    setBusy(true)
    setError(null)
    setStatus(null)
    try {
      const r = await api.post<{ connectors: string[]; pools: string[] }>('/admin/reload')
      setStatus(
        t('settings.reloaded', {
          connectors: r.connectors.join(', ') || `(${t('common.none')})`,
          pools: r.pools.join(', ') || `(${t('common.none')})`,
        }),
      )
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  if (error && !doc)
    return (
      <PageLayout icon={<SlidersHorizontal size={18} />} title={t('settings.title')}>
        <Banner $tone="error">{error}</Banner>
      </PageLayout>
    )
  if (!doc)
    return (
      <PageLayout icon={<SlidersHorizontal size={18} />} title={t('settings.title')}>
        <Centered />
      </PageLayout>
    )

  return (
    <PageLayout icon={<SlidersHorizontal size={18} />} title={t('settings.title')} description={<Mono>{doc.path}</Mono>}>
      <Stack gap={12}>
        <EditorFrame>
          <MonacoEditor
            height="100%"
            language="ini"
            theme={isLight ? 'light' : 'vs-dark'}
            value={content}
            loading={<Centered />}
            onChange={(v) => {
              const next = v ?? ''
              setContent(next)
              setDirty(next !== doc.content)
              setStatus(null)
            }}
            options={{
              fontSize: parseInt(fontSize.base, 10),
              fontFamily: fonts.mono,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              tabSize: 2,
              renderWhitespace: 'boundary',
              wordWrap: 'on',
              automaticLayout: true,
            }}
          />
        </EditorFrame>
        <Row>
          <Button $variant="primary" onClick={save} disabled={busy || !dirty}>
            {busy ? <SpinnerRing size={14} thickness={2} /> : <Save size={14} />}
            {t('settings.save')}
          </Button>
          <Button onClick={reload} disabled={busy || dirty}>
            {busy ? <SpinnerRing size={14} thickness={2} /> : <RefreshCw size={14} />}
            {t('settings.reload')}
          </Button>
          {dirty && <span style={{ color: colors.text.muted, fontSize: fontSize.sm }}>{t('settings.unsaved')}</span>}
          {status && <span style={{ color: colors.green.main, fontSize: fontSize.sm }}>{status}</span>}
          {error && <span style={{ color: colors.red.main, fontSize: fontSize.sm }}>{error}</span>}
        </Row>
        <Hint>{t('settings.hint')}</Hint>
      </Stack>
    </PageLayout>
  )
}
