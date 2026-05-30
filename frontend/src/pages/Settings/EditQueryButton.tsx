// Tiny ghost-button + Edit3 icon that opens the existing EditQueryModal for a given
// (connector, queryName). One component, used everywhere a query is picked — chart
// editor, lookup definition, sequence definition, screen editor's read/update/insert/
// delete query fields, etc — so the SVG + tooltip + behaviour stay identical.
//
// The button is self-contained (manages its own modal-open state + the EditQueryModal
// mount), so callers only need to drop it next to the picker:
//
//   <Row gap={6}>
//     <SearchSelect value={queryName} options={...} onChange={...} />
//     <EditQueryButton connector={connector} queryName={queryName} onSaved={refetch} />
//   </Row>
//
// Hidden (renders nothing) when either ``connector`` or ``queryName`` is empty —
// editing requires both. The ``onSaved`` callback fires after a successful save +
// reload so the caller can refresh its own state (rename of the query etc.).
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Edit3 } from 'lucide-react'
import { Button } from '../../common'
import { EditQueryModal } from './EditQueryModal'

export interface EditQueryButtonProps {
  connector: string | null | undefined
  queryName: string | null | undefined
  onSaved?: () => void
  /** Optional tooltip override. Defaults to "Edit query". */
  title?: string
  /** Optional size override (default sm to match the surrounding controls). */
  size?: 'sm' | 'md'
}

export function EditQueryButton({ connector, queryName, onSaved, title, size = 'sm' }: EditQueryButtonProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  if (!connector || !queryName) return null
  return (
    <>
      <Button
        $variant="ghost"
        $size={size}
        type="button"
        onClick={() => setOpen(true)}
        title={title ?? t('settings.editQuery.edit', 'Edit query')}
        aria-label={title ?? t('settings.editQuery.edit', 'Edit query')}
      >
        <Edit3 size={13} />
      </Button>
      {open && (
        <EditQueryModal
          connector={connector}
          queryName={queryName}
          onClose={() => setOpen(false)}
          onSaved={onSaved}
        />
      )}
    </>
  )
}

export default EditQueryButton
