// "New list from a table…": from a screen's column whose kind is a list read from a table, make
// the list in one step — pick the table and its code / label columns — instead of creating a query
// in Connectors, then a list in the Dictionary, then coming back to pick it. The query and the
// list are saved at once (they are new objects; nothing existing is changed) and the list is
// handed back to be picked.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ListPlus } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, SpinnerRing } from '../../common'
import { takenNames, withList, withListQuery, type NewList } from '../../services/listFromTable'
import type { ConnectorsDoc, DictionaryDoc } from '../../types/config'
import { ScaffoldQueryModal } from './ScaffoldQueryModal'

type Docs = { connectors: Record<string, Record<string, unknown>>; dictionary: Record<string, unknown> }

export function NewListFromTable({ app, connector, onCreated }: {
  /** The application whose dictionary receives the list. */
  app: string
  /** The connector whose database holds the table (the screen's). */
  connector: string
  /** The new list's name, once saved: the caller picks it and reads the dictionary again. */
  onCreated: (listId: string) => void | Promise<void>
}) {
  const { t } = useTranslation()
  const [docs, setDocs] = useState<Docs | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const start = async () => {
    setBusy(true); setError(null)
    try {
      const [c, d] = await Promise.all([api.get<ConnectorsDoc>('/admin/config/connectors/parsed'), api.get<DictionaryDoc>('/admin/config/dictionary/parsed')])
      setDocs({ connectors: c.connectors as Docs['connectors'], dictionary: d.dictionary as unknown as Record<string, unknown> })
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }
  const save = async (result: NewList) => {
    if (!docs) return
    // the list lives in the application's dictionary; its query in the connector that reads the table
    const list: NewList = app === connector ? result : { ...result, dictEntry: { ...result.dictEntry, connector } }
    setBusy(true); setError(null)
    try {
      await api.put('/admin/config/connectors/parsed', { connectors: withListQuery(docs.connectors, connector, list) })
      await api.put('/admin/config/dictionary/parsed', { dictionary: withList(docs.dictionary, app, list) })
      await api.post('/admin/reload')
      setDocs(null)
      await onCreated(list.dictId)
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }
  const taken = docs ? takenNames(docs.connectors, docs.dictionary, connector, app) : null
  return (
    <div data-new-list style={{ marginTop: 6 }}>
      <Button type="button" $variant="ghost" $size="sm" onClick={() => void start()} disabled={busy}>
        {busy ? <SpinnerRing size={13} thickness={2} /> : <ListPlus size={13} />} {t('settings.screens.newList.button')}
      </Button>
      {error && <Banner $tone="error" style={{ marginTop: 6 }}>{error}</Banner>}
      {docs && taken && (
        <ScaffoldQueryModal kind="lookup" connector={connector} existingDictIds={taken.lists} existingQueryNames={taken.queries}
          onSave={(result) => void save(result)} onCancel={() => setDocs(null)} />
      )}
    </div>
  )
}
