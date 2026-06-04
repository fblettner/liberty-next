// Shared "Add scope" dialog for every per-connector Settings editor (Menus / Screens / Dictionary
// / Charts / Dashboards). A scope is always a connector, so this is a single connector dropdown +
// Cancel / OK — the same modal shape used everywhere else in the app (no free-text prompts, no
// button-list). The caller passes the connectors that don't already have content of this kind;
// picking one and clicking OK resolves with that connector name.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Button, Field, SearchSelect, Banner, type SearchSelectOption } from '../../common'

export function AddScopeModal({
  candidates, title, label, emptyHint, onPick, onClose,
}: {
  /** Connector names available to add (those that don't already have this kind of content). */
  candidates: string[]
  title?: string
  /** Label above the dropdown. */
  label?: string
  /** Shown (with no dropdown) when there are no candidates left. */
  emptyHint?: string
  onPick: (connector: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [value, setValue] = useState('')
  const opts: SearchSelectOption[] = candidates.map((c) => ({ value: c, label: c, mono: c }))
  const none = candidates.length === 0
  // No backdrop-click-to-close — outside clicks must not discard the typed scope name (Cancel / Escape).
  return (
    <Overlay>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(440px, 94vw)' }}>
        <ModalHeader>{title ?? t('settings.addScope', 'Add scope')}</ModalHeader>
        <ModalBody>
          {none ? (
            <Banner $tone="info">
              {emptyHint ?? t('settings.addScopeEmpty', 'Every connector already has content here. Add a connector in the Connectors tab first.')}
            </Banner>
          ) : (
            <Field label={label ?? t('settings.addScopePick', 'Connector')}>
              <SearchSelect value={value} onChange={setValue} options={opts}
                placeholder={t('common.pick', 'Pick…')} />
            </Field>
          )}
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose}>{t('common.cancel', 'Cancel')}</Button>
          <Button $size="sm" $variant="primary" disabled={none || !value}
            onClick={() => { if (value) { onPick(value); onClose() } }}>
            {t('common.ok', 'OK')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}

export default AddScopeModal
