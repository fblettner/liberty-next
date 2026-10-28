// A detail row opened in its entity's screen dialog: the screen definition is fetched, its read
// query run with the row's key values as parameters (and the exact row picked by key when the
// query returns more), then the shared ScreenDialog shows the record — edits save through the
// screen as anywhere else. Without a match the dialog opens on the values the Explorer has.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, Centered } from '../../common'
import { ScreenDialog } from '../TableView/ScreenDialog'
import type { Column, QueryResult } from '../../types/connectors'
import type { ScreenDetail } from '../../types/screens'
import type { ExplorerEntity } from '../../types/explorer'

export function RecordDialog({ app, entity, row, onClose, onSaved }: {
  app: string
  entity: ExplorerEntity
  /** the detail row, keyed by column NAME (upper case) */
  row: Record<string, unknown>
  onClose: () => void
  onSaved?: () => void
}) {
  const { t } = useTranslation()
  const [screen, setScreen] = useState<ScreenDetail | null>(null)
  const [columns, setColumns] = useState<Column[]>([])
  const [record, setRecord] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const connector = entity.connector || app

  useEffect(() => {
    let cancelled = false
    const keys = entity.key.map((k) => k.toUpperCase())
    const load = async () => {
      try {
        const detail = await api.get<ScreenDetail>(`/api/screens/${encodeURIComponent(app)}/${encodeURIComponent(entity.screen!)}`)
        const qs = [...keys.map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(String(row[k] ?? ''))}`), `_screen=${encodeURIComponent(entity.screen!)}`, `_app=${encodeURIComponent(app)}`].join('&')
        const res = await api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(detail.read_query)}?${qs}`)
        if (cancelled) return
        const same = (r: Record<string, unknown>) => keys.every((k) => {
          const hit = Object.keys(r).find((c) => c.toUpperCase() === k)
          return hit !== undefined && String(r[hit] ?? '') === String(row[k] ?? '')
        })
        const found = res.rows.find(same) ?? (res.rows.length === 1 ? res.rows[0] : null)
        setScreen(detail)
        setColumns(res.columns)
        setRecord(found ?? row)
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e))
      }
    }
    void load()
    return () => { cancelled = true }
  }, [app, entity, row, connector])

  if (error) return <Banner $tone="error" style={{ margin: 12 }}>{error} <button type="button" onClick={onClose}>{t('common.close')}</button></Banner>
  if (!screen || !record) return <Centered />
  return (
    <ScreenDialog open nested mode="edit" screen={screen} columns={columns} row={record} connector={screen.connector || connector}
      keyColumns={screen.key_columns ?? entity.key} onClose={onClose} onSaved={() => { onSaved?.(); onClose() }} />
  )
}
