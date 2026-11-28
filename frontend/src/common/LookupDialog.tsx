// A list offered AS A TABLE in a dialog — what a dropdown opens when its list is long or has several
// columns (services/options.browsable). The list is drawn with the shared grid (common/LookupTable): the
// code, the label and each extra column under its heading, searched, filtered column by column, sorted and
// grouped as any table of the app. A line clicked is the value. Opened by SearchSelect ("Open as a table"):
// every screen, form and filter that shows a list has it.
import { lazy, Suspense } from 'react'
import { useTranslation } from 'react-i18next'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter } from './Modal'
import { Button } from './Button'
import { Centered } from './Spinner'
import type { Searchable } from '../services/options'

// the grid is loaded with the first dialog opened (a dropdown alone never loads it)
const LookupTable = lazy(() => import('./LookupTable'))

export function LookupDialog<T extends Searchable>({
  title, options, value, cellColumns, onPick, onClose,
}: {
  title?: string
  options: T[]
  value: string
  cellColumns?: { label: string; filterable?: boolean }[]
  onPick: (value: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const extra = options.reduce((n, o) => Math.max(n, o.cells?.length ?? 0), 0)
  return (
    <Overlay onMouseDown={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <Modal style={{ width: `min(${820 + extra * 140}px, 95vw)` }} data-lookup-dialog>
        <ModalHeader>{title || t('lookupDialog.title')}</ModalHeader>
        <ModalBody style={{ display: 'flex', flexDirection: 'column', height: 'min(72vh, 720px)' }}>
          <Suspense fallback={<Centered />}>
            <LookupTable options={options} value={value} cellColumns={cellColumns} onPick={onPick} />
          </Suspense>
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" $size="sm" onClick={onClose}>{t('common.close')}</Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
