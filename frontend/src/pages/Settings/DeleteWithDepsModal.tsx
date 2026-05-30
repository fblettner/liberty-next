// Destructive counterpart of CloneWithDepsModal — delete a screen / chart / dashboard
// and, with the operator's explicit opt-in, also delete the queries that become orphans
// of the deletion. Queries used by ANY other entity stay (planner enforces this; the
// "preserved" list explains why).
//
// Default behaviour matches the old delete-confirm: just remove the entity. Tick the
// box to also clean up its dedicated queries. The preview is fetched live from
// ``/admin/delete-with-deps/preview`` so the operator sees the impact BEFORE confirming.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Trash2, AlertCircle, ShieldAlert } from 'lucide-react'
import { api, ApiError, authHeaders } from '../../api/client'
import { Banner, Button, Checkbox, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SpinnerRing, Mono } from '../../common'
import { colors, fontSize, fonts } from '../../theme'

export interface DeleteWithDepsModalProps {
  kind: 'screen' | 'chart' | 'dashboard'
  name: string
  scope: string | null
  /** Fires after a successful delete. */
  onDeleted: () => void
  onClose: () => void
}

interface DeletionPlan {
  seed: { kind: string; name: string; scope: string | null }
  delete_queries: Array<{ connector: string; name: string }>
  preserved_queries: Array<{ connector: string; name: string; reason: string; external_usages: Array<{ type: string; label: string }> }>
  warnings: string[]
}

const TITLE_BY_KIND: Record<string, string> = {
  screen: 'Delete screen',
  chart: 'Delete chart',
  dashboard: 'Delete dashboard',
}

export function DeleteWithDepsModal({ kind, name, scope, onDeleted, onClose }: DeleteWithDepsModalProps) {
  const { t } = useTranslation()
  const [alsoDeleteQueries, setAlsoDeleteQueries] = useState(false)
  const [plan, setPlan] = useState<DeletionPlan | null>(null)
  const [planBusy, setPlanBusy] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Live preview — re-runs when the operator ticks / unticks the checkbox so they
  // always see what'll happen with their current choice.
  useEffect(() => {
    if (!alsoDeleteQueries) { setPlan(null); return }
    let cancelled = false
    setPlanBusy(true)
    api.post<DeletionPlan>('/admin/delete-with-deps/preview', {
      kind, name, scope, options: { delete_queries: true },
    })
      .then((r) => { if (!cancelled) setPlan(r) })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
      .finally(() => { if (!cancelled) setPlanBusy(false) })
    return () => { cancelled = true }
  }, [alsoDeleteQueries, kind, name, scope])

  async function submit() {
    setBusy(true); setError(null)
    try {
      const res = await fetch('/admin/delete-with-deps', {
        method: 'POST',
        headers: { ...authHeaders({ 'Content-Type': 'application/json' }) },
        body: JSON.stringify({
          kind, name, scope,
          options: { delete_queries: alsoDeleteQueries },
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({} as Record<string, unknown>))
        throw new Error((body && typeof body === 'object' && 'detail' in body) ? String((body as { detail: unknown }).detail) : `delete failed: ${res.status}`)
      }
      onDeleted()
      onClose()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Overlay onClick={onClose}>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(620px, 96vw)' }}>
        <ModalHeader>
          <ShieldAlert size={16} style={{ verticalAlign: -3, marginRight: 6, color: colors.red.main }} />
          {t(`settings.deleteDeps.${kind}.title`, TITLE_BY_KIND[kind])}
        </ModalHeader>
        <ModalBody>
          <div style={{ marginBottom: 14, color: colors.text.secondary, fontFamily: fonts.sans }}>
            {t('settings.deleteDeps.confirm', 'Are you sure you want to delete')}{' '}
            <Mono>{scope ? `${scope}.${name}` : name}</Mono>?
          </div>

          <Checkbox
            checked={alsoDeleteQueries}
            onChange={setAlsoDeleteQueries}
            label={t('settings.deleteDeps.alsoDeleteQueries', 'Also delete referenced queries that are exclusively used by this entity')}
          />
          <div style={{ color: colors.text.muted, fontSize: fontSize.sm, marginTop: 4, paddingLeft: 24 }}>
            {t('settings.deleteDeps.queriesHint',
              'Queries also used by another screen / lookup / chart / dashboard widget will be preserved.')}
          </div>

          {alsoDeleteQueries && (
            <div style={{ marginTop: 14 }}>
              {planBusy && <div style={{ padding: 8 }}><SpinnerRing size={13} thickness={2} /></div>}
              {plan && (
                <>
                  <div style={{ fontSize: fontSize.sm, color: colors.text.primary, fontWeight: 600, marginBottom: 6 }}>
                    {t('settings.deleteDeps.willDelete', 'Will be deleted')} ({plan.delete_queries.length}):
                  </div>
                  {plan.delete_queries.length === 0 ? (
                    <div style={{ color: colors.text.muted, fontSize: fontSize.sm, paddingLeft: 8 }}>
                      {t('settings.deleteDeps.noOrphans', 'No orphans — every referenced query is also used elsewhere.')}
                    </div>
                  ) : (
                    <ul style={{ margin: '0 0 12px 18px', padding: 0, fontSize: fontSize.sm }}>
                      {plan.delete_queries.map((q, i) => (
                        <li key={i}><Mono>{q.connector}.{q.name}</Mono></li>
                      ))}
                    </ul>
                  )}
                  {plan.preserved_queries.length > 0 && (
                    <>
                      <div style={{ fontSize: fontSize.sm, color: colors.text.primary, fontWeight: 600, marginBottom: 6 }}>
                        {t('settings.deleteDeps.preserved', 'Preserved')} ({plan.preserved_queries.length}):
                      </div>
                      <ul style={{ margin: '0 0 6px 18px', padding: 0, fontSize: fontSize.sm }}>
                        {plan.preserved_queries.map((q, i) => (
                          <li key={i} style={{ marginBottom: 4 }}>
                            <Mono>{q.connector}.{q.name}</Mono>
                            <div style={{ color: colors.text.muted, fontSize: fontSize.micro, marginLeft: 4 }}>{q.reason}</div>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </>
              )}
            </div>
          )}

          {error && (
            <Banner $tone="error" style={{ marginTop: 12 }}>
              <AlertCircle size={14} style={{ marginRight: 6, verticalAlign: -2 }} />
              {error}
            </Banner>
          )}
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose} disabled={busy}>
            {t('common.cancel', 'Cancel')}
          </Button>
          <Button $size="sm" $variant="danger" onClick={() => void submit()} disabled={busy}>
            {busy ? <SpinnerRing size={13} thickness={2} /> : <Trash2 size={13} />}
            {alsoDeleteQueries && plan && plan.delete_queries.length > 0
              ? t('settings.deleteDeps.deleteWithCount', 'Delete + {{n}} quer(y/ies)', { n: plan.delete_queries.length })
              : t('common.delete', 'Delete')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

export default DeleteWithDepsModal
