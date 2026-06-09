// Move a query / table / sequence / lookup definition from one connector to another. Mirrors
// AddScopeModal's shape (single connector dropdown + Cancel/OK) but runs POST /admin/config/move
// then /admin/reload, and surfaces the result: how many references were auto-rewritten, plus any
// "manual refs" the move couldn't safely rewrite (a screen whose connector is shared with queries
// staying behind) — each linking straight to the offending editor.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Button, Field, SearchSelect, Banner, type SearchSelectOption } from '../../common'
import { api, ApiError } from '../../api/client'
import { deepLinkToUrl } from './FindUsagesModal'

type ManualRef = { where: string; reason: string; deep_link: Record<string, unknown> }
type MoveResult = { total_refs: number; files: Record<string, number>; manual_refs: ManualRef[] }

export function MoveQueryModal({
  kind, name, fromConnector, candidates, onMoved, onClose,
}: {
  kind: 'table' | 'query' | 'sequence' | 'lookup'
  name: string
  fromConnector: string
  /** SQL connector names the definition can move to (everything except the source). */
  candidates: string[]
  /** Called after a successful move + reload so the parent can refresh its config. */
  onMoved: () => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<MoveResult | null>(null)
  const opts: SearchSelectOption[] = candidates.map((c) => ({ value: c, label: c, mono: c }))
  const none = candidates.length === 0

  const run = async () => {
    if (!value) return
    setBusy(true); setError(null)
    try {
      const res = await api.post<MoveResult>('/admin/config/move', {
        kind, name, from_connector: fromConnector, to_connector: value,
      })
      await api.post('/admin/reload')
      setResult(res)
      onMoved()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  return (
    <Overlay>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(520px, 94vw)' }}>
        <ModalHeader>{t('settings.connectors.moveTitle', 'Move {{kind}} "{{name}}"', { kind, name })}</ModalHeader>
        <ModalBody>
          {result ? (
            <>
              <Banner $tone={result.manual_refs.length ? 'warning' : 'ok'}>
                {t('settings.connectors.moveDone',
                  'Moved {{name}} to {{to}}. {{refs}} reference(s) rewritten.',
                  { name, to: value, refs: result.total_refs })}
              </Banner>
              {result.manual_refs.length > 0 && (
                <div style={{ marginTop: 12 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                    {t('settings.connectors.moveManual', 'Fix these by hand (connector shared with queries left behind):')}
                  </div>
                  <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, lineHeight: 1.6 }}>
                    {result.manual_refs.map((m, i) => {
                      const url = deepLinkToUrl(m.deep_link)
                      return (
                        <li key={i}>
                          {url ? <Link to={url} onClick={onClose}>{m.where}</Link> : <strong>{m.where}</strong>}
                          {' — '}{m.reason}
                        </li>
                      )
                    })}
                  </ul>
                </div>
              )}
            </>
          ) : none ? (
            <Banner $tone="info">
              {t('settings.connectors.moveNoTarget', 'No other SQL connector to move to — create one in the Connectors tab first.')}
            </Banner>
          ) : (
            <Field label={t('settings.connectors.moveTo', 'Move to connector')}>
              <SearchSelect value={value} onChange={setValue} options={opts} placeholder={t('common.pick', 'Pick…')} />
            </Field>
          )}
          {error && <Banner $tone="error" style={{ marginTop: 10 }}>{error}</Banner>}
        </ModalBody>
        <ModalFooter>
          {result ? (
            <Button $size="sm" $variant="primary" onClick={onClose}>{t('common.done', 'Done')}</Button>
          ) : (
            <>
              <Button $size="sm" $variant="ghost" onClick={onClose} disabled={busy}>{t('common.cancel', 'Cancel')}</Button>
              <Button $size="sm" $variant="primary" disabled={none || !value || busy} onClick={() => void run()}>
                {busy ? t('common.working', 'Working…') : t('settings.connectors.moveButton', 'Move')}
              </Button>
            </>
          )}
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

export default MoveQueryModal
