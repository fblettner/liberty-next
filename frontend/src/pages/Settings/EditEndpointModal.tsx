// One endpoint of an API connector, edited on its own — opened in place from the Studio's map (the twin of
// EditQueryModal for a query). The same editor as the connector page's; saving writes connectors.toml and reloads.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Banner, Centered, useModals } from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts } from '../../theme'
import { type Endpoint, EndpointEditor } from './ApiConnectorEditor'
import { flattenConnectorSections } from './connectorTables'

type Doc = Record<string, Record<string, unknown>>

export function EditEndpointModal({ connector, endpointName, onClose, onSaved }: {
  connector: string; endpointName: string; onClose: () => void; onSaved?: () => void
}) {
  const { t } = useTranslation()
  const modals = useModals()
  const [conns, setConns] = useState<Doc | null>(null)
  const [original, setOriginal] = useState('')
  const [index, setIndex] = useState(-1)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    let cancelled = false
    api.get<{ connectors: Doc }>('/admin/config/connectors/parsed')
      .then((d) => {
        if (cancelled) return
        const flat = flattenConnectorSections(d.connectors) as Doc
        const list = (Array.isArray(flat[connector]?.endpoints) ? flat[connector].endpoints : []) as Endpoint[]
        setIndex(list.findIndex((e) => e && e.name === endpointName))      // by position: the name may be edited here
        setConns(flat); setOriginal(JSON.stringify(flat))
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [connector, endpointName])
  const list = (conns && Array.isArray(conns[connector]?.endpoints) ? conns[connector].endpoints : []) as Endpoint[]
  const endpoint = index >= 0 ? list[index] : null
  const dirty = conns != null && JSON.stringify(conns) !== original
  const update = (next: Endpoint) => {
    if (!conns || index < 0) return
    const copy = list.slice(); copy[index] = next
    setConns({ ...conns, [connector]: { ...conns[connector], endpoints: copy } })
  }
  const save = async () => {
    if (!conns) return
    setBusy(true); setError(null)
    try {
      await api.put('/admin/config/connectors/parsed', { connectors: conns })
      await api.post('/admin/reload')
      onSaved?.(); onClose()
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }
  const cancel = async () => {
    if (dirty && !(await modals.confirm({ title: t('settings.editQuery.unsavedTitle'), message: t('settings.editQuery.unsavedMsg') }))) return
    onClose()
  }
  return (
    <EditorModalShell
      title={<>{t('studio.endpoint')} · <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontSize: fontSize.sm, fontWeight: 400 }}>{connector}.{endpointName}</span></>}
      onClose={() => void cancel()} onSave={() => void save()} dirty={dirty} saveDisabled={!dirty || !endpoint} busy={busy}
      overlayZIndex={900} frameStyle={{ width: 'min(1320px, 96vw)', height: 'min(860px, 92vh)' }}
    >
      {error && <Banner $tone="error">{error}</Banner>}
      {conns == null ? <Centered /> : endpoint == null
        ? <Banner $tone="info">{t('settings.editQuery.notFound', { q: endpointName, c: connector })}</Banner>
        : <EndpointEditor value={endpoint} onChange={update} />}
    </EditorModalShell>
  )
}
