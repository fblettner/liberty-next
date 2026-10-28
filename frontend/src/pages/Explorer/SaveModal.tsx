// Save / Save as for an analysis or a dashboard: a name, a description, an audience (groups the
// picker), visible to everyone or only me. Saving a shared object changes it for everyone, the
// modal says so; "Save as" always makes the caller's own copy.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Button, Field, Input, Banner, Checkbox } from '../../common'
import type { SavedItem } from '../../services/explorerSaved'

export function SaveModal({ kind, current, asNew, onSave, onClose }: {
  kind: 'analyses' | 'dashboards'
  current: SavedItem<unknown> | null
  asNew: boolean
  onSave: (fields: { name: string; description: string; audience: string; shared: boolean }) => Promise<void>
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [name, setName] = useState(asNew && current ? `${current.name} (2)` : current?.name ?? '')
  const [description, setDescription] = useState(current?.description ?? '')
  const [audience, setAudience] = useState(current?.audience ?? '')
  const [shared, setShared] = useState(asNew ? false : current?.shared ?? false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const replacingShared = !asNew && !!current && current.shared
  const submit = async () => {
    setBusy(true); setError(null)
    try { await onSave({ name: name.trim(), description: description.trim(), audience: audience.trim(), shared }); onClose() } catch (e) { setError(e instanceof Error ? e.message : String(e)) } finally { setBusy(false) }
  }
  return (
    <Overlay>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(480px, 94vw)' }}>
        <ModalHeader>{asNew ? t('explorer.saveAs') : t(kind === 'analyses' ? 'explorer.saveAnalysis' : 'explorer.saveDashboard')}</ModalHeader>
        <ModalBody>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <Field label={t('explorer.name')}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label={t('explorer.description')}><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
            <Field label={t('explorer.audience')}><Input value={audience} onChange={(e) => setAudience(e.target.value)} placeholder={t('explorer.audiencePlaceholder')} /></Field>
            <Checkbox checked={shared} onChange={setShared} label={t('explorer.visibleToEveryone')} />
            {replacingShared && <Banner $tone="warning">{t('explorer.sharedWarning')}</Banner>}
            {error && <Banner $tone="error">{error}</Banner>}
          </div>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</Button>
          <Button $size="sm" $variant="primary" disabled={busy || !name.trim()} onClick={() => void submit()}>{t('common.save')}</Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
