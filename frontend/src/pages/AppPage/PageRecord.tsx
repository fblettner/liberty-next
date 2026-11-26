// A screen's record form, for a page an application ships (`sdk.Record`, contract version 8): the page keeps the
// user where he is and still adds or changes a record through the SCREEN — its fields, its rules per value, its
// lookups, its defaults, its save and its hooks — instead of re-making a form of its own.
//
//   <sdk.Record app="nomajde" screen="f00950" mode="add" values={{ FSUSER: 'APMGR', FSSETY: '1' }} onClose=… onSaved=… />
//
// `add`: the form opens on `values`. `edit`: `values` holds the record's key values — the record is read through the
// screen's own read (filtered on them) and opened; without a match the form opens on `values`. The form is the data
// screen's own (loaded when first used: it is heavy).
import { lazy, Suspense, useEffect, useState } from 'react'
import { api, ApiError } from '../../api/client'
import { Banner, Centered } from '../../common'
import type { Column, QueryResult } from '../../types/connectors'
import type { ScreenDetail } from '../../types/screens'

const ScreenDialog = lazy(() => import('../TableView/ScreenDialog').then((m) => ({ default: m.ScreenDialog })))

export function PageRecord({ app, screen: screenId, mode = 'add', values, onClose, onSaved }: {
  app: string
  screen: string
  mode?: 'add' | 'edit'
  /** the values the form opens on (`add`), or the record's key values (`edit`) — by column NAME */
  values?: Record<string, unknown>
  onClose: () => void
  onSaved?: () => void
}) {
  const [screen, setScreen] = useState<ScreenDetail | null>(null)
  const [columns, setColumns] = useState<Column[]>([])
  const [record, setRecord] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const seed = JSON.stringify(values ?? {})

  useEffect(() => {
    let cancelled = false
    const given = JSON.parse(seed) as Record<string, unknown>
    const load = async () => {
      try {
        const detail = await api.get<ScreenDetail>(`/api/screens/${encodeURIComponent(app)}/${encodeURIComponent(screenId)}`)
        const connector = detail.connector || app
        const narrow = mode === 'edit'
          ? Object.entries(given).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v ?? ''))}&${encodeURIComponent(k)}_op=equals`)
          : ['_limit=1']                                  // an add needs the columns only
        const qs = [...narrow, `_screen=${encodeURIComponent(screenId)}`, `_app=${encodeURIComponent(app)}`].join('&')
        const res = await api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(detail.read_query)}?${qs}`)
        if (cancelled) return
        setScreen(detail)
        setColumns(res.columns)
        setRecord(mode === 'edit' && res.rows.length === 1 ? res.rows[0] : given)
      } catch (e) {
        if (!cancelled) setError(e instanceof ApiError ? e.message : String(e))
      }
    }
    void load()
    return () => { cancelled = true }
  }, [app, screenId, mode, seed])

  if (error) return <Banner $tone="error" style={{ margin: 12 }}>{error}</Banner>
  if (!screen || !record) return <Centered />
  return (
    <Suspense fallback={<Centered />}>
      <ScreenDialog open mode={mode} screen={screen} columns={columns} row={record} connector={screen.connector || app}
        keyColumns={screen.key_columns ?? []} onClose={onClose} onSaved={() => onSaved?.()} />
    </Suspense>
  )
}
