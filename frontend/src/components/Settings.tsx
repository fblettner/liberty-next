import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { SlidersHorizontal, Save, RefreshCw } from 'lucide-react'
import { api, ApiError } from '../api'
import { PageLayout, Button, Banner, Centered, Row, Stack, SpinnerRing, Mono } from '../ui'
import { colors, fontSize, fonts, radius } from '../theme'

interface ConfigDoc {
  path: string
  content: string
}

const Editor = styled.textarea`
  width: 100%;
  min-height: 56vh;
  resize: vertical;
  padding: 12px 14px;
  border-radius: ${radius.md};
  border: 1px solid ${colors.border};
  background: ${colors.bg.input};
  color: ${colors.text.primary};
  font-family: ${fonts.mono};
  font-size: ${fontSize.base};
  line-height: 1.5;
  outline: none;
  white-space: pre;
  overflow: auto;
  tab-size: 2;
  &:focus { border-color: ${colors.blue.main}; }
`

const Hint = styled.p`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
  line-height: 1.5;
  margin: 0;
`

export function Settings() {
  const { t } = useTranslation()
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
        <Editor
          spellCheck={false}
          value={content}
          onChange={(e) => {
            setContent(e.target.value)
            setDirty(e.target.value !== doc.content)
            setStatus(null)
          }}
        />
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
