// Three small ghost-button + icon controls that share the same EditQueryModal mount,
// rendered everywhere a query is picked (chart editor, dictionary lookup / sequence,
// screen editor's read/update/insert/delete query fields, …). Same SVGs + tooltips
// everywhere — operator sees a consistent "edit / clone / add" trio.
//
// All three are self-contained: they manage their own modal-open state + (for add /
// clone) the name-prompt flow + the EditQueryModal mount. Callers only need to drop
// them next to the picker:
//
//   <Row gap={6}>
//     <SearchSelect value={queryName} options={...} onChange={...} />
//     <EditQueryButton connector={connector} queryName={queryName} onSaved={refetch} />
//     <CloneQueryButton connector={connector} queryName={queryName} existingNames={names} onSaved={refetch} />
//     <AddQueryButton connector={connector} existingNames={names} onSaved={refetch} />
//   </Row>
//
// EditQueryButton renders nothing when ``connector`` or ``queryName`` is empty — editing
// requires both. CloneQueryButton renders nothing when the SOURCE name is empty. AddQueryButton
// renders whenever ``connector`` is set (operator can always create a new query on that connector).
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Edit3, Copy, Plus } from 'lucide-react'
import { Button, useModals } from '../../common'
import { api } from '../../api/client'
import { EditQueryModal } from './EditQueryModal'
import { flattenConnectorSections } from './connectorTables'
import { validateId, suggestCloneId } from '../../services/idValidator'

export interface EditQueryButtonProps {
  connector: string | null | undefined
  queryName: string | null | undefined
  onSaved?: () => void
  title?: string
  size?: 'sm' | 'md'
}

// ── Edit ────────────────────────────────────────────────────────────────────────

export function EditQueryButton({ connector, queryName, onSaved, title, size = 'sm' }: EditQueryButtonProps) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  if (!connector || !queryName) return null
  return (
    <>
      <Button
        $variant="ghost" $size={size} type="button"
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

// ── Clone ───────────────────────────────────────────────────────────────────────

export interface CloneQueryButtonProps extends EditQueryButtonProps {
  /** Existing query names on this connector — used for the validator's duplicate check
   *  + the suggested clone name (``<source>_copy`` / ``_copy2`` / …). When omitted the
   *  validator skips the dupe check and EditQueryModal's save would fail loudly on the
   *  backend duplicate-name check; passing it is strongly recommended. */
  existingNames?: string[]
}

export function CloneQueryButton({ connector, queryName, onSaved, title, size = 'sm', existingNames }: CloneQueryButtonProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const [seed, setSeed] = useState<{ queryName: string; seed: Record<string, unknown> } | null>(null)
  if (!connector || !queryName) return null
  const startClone = async () => {
    // Fetch the source query so the seed is a deep copy of its current on-disk values.
    // Same endpoint EditQueryModal hits — cheap (the registry is in memory on the backend).
    try {
      const d = await api.get<{ connectors: Record<string, Record<string, unknown>> }>('/admin/config/connectors/parsed')
      const conns = flattenConnectorSections(d.connectors)
      const arr = (conns[connector]?.queries ?? []) as Record<string, unknown>[]
      const src = arr.find((q) => q && (q.name as string) === queryName)
      if (!src) return
      const existing = existingNames ?? arr.map((q) => String(q.name ?? ''))
      const newName = (await modals.prompt({
        title: t('settings.editQuery.cloneTitle', 'Clone query'),
        message: t('settings.editQuery.clonePrompt', 'New name for the copy of "{{name}}":', { name: queryName }),
        defaultValue: suggestCloneId(queryName, existing),
        placeholder: 'snake_case',
        submitLabel: t('common.clone', 'Clone'),
        validate: (v) => {
          if (v === queryName) return { error: t('settings.tables.duplicateSameName', 'Pick a different name.') }
          return validateId({ kind: 'query', proposed: v, existing, mode: 'clone' })
        },
      }))?.trim()
      if (!newName) return
      // Deep-copy via JSON round-trip — QueryDef is a plain JSON shape. Drop the old
      // name field; EditQueryModal re-injects the new name on load.
      const deep = JSON.parse(JSON.stringify(src)) as Record<string, unknown>
      delete deep.name
      setSeed({ queryName: newName, seed: deep })
    } catch {
      // Silent failure — the operator can retry. A surfaced banner would clutter the
      // page when the registry is just temporarily unreachable.
    }
  }
  return (
    <>
      <Button
        $variant="ghost" $size={size} type="button"
        onClick={() => void startClone()}
        title={title ?? t('settings.editQuery.clone', 'Clone query')}
        aria-label={title ?? t('settings.editQuery.clone', 'Clone query')}
      >
        <Copy size={13} />
      </Button>
      {seed && (
        <EditQueryModal
          connector={connector}
          queryName={seed.queryName}
          seed={seed.seed}
          onClose={() => setSeed(null)}
          onSaved={onSaved}
        />
      )}
    </>
  )
}

// ── Add ─────────────────────────────────────────────────────────────────────────

export interface AddQueryButtonProps {
  connector: string | null | undefined
  onSaved?: () => void
  title?: string
  size?: 'sm' | 'md'
  /** Existing query names — same purpose as CloneQueryButton's. Strongly recommended. */
  existingNames?: string[]
}

export function AddQueryButton({ connector, onSaved, title, size = 'sm', existingNames }: AddQueryButtonProps) {
  const { t } = useTranslation()
  const modals = useModals()
  const [seed, setSeed] = useState<{ queryName: string; seed: Record<string, unknown> } | null>(null)
  if (!connector) return null
  const startAdd = async () => {
    const existing = existingNames ?? []
    const newName = (await modals.prompt({
      title: t('settings.editQuery.addTitle', 'New query'),
      message: t('settings.editQuery.addPrompt', 'Name for the new query:'),
      placeholder: 'snake_case',
      submitLabel: t('common.add', 'Add'),
      validate: (v) => validateId({ kind: 'query', proposed: v, existing, mode: 'add' }),
    }))?.trim()
    if (!newName) return
    // Sensible blank — custom query (the unclassified bucket) with empty SQL. The form
    // lets the operator switch type / fill SQL before saving.
    setSeed({ queryName: newName, seed: { type: 'custom', sql: '' } })
  }
  return (
    <>
      <Button
        $variant="ghost" $size={size} type="button"
        onClick={() => void startAdd()}
        title={title ?? t('settings.editQuery.add', 'Add query')}
        aria-label={title ?? t('settings.editQuery.add', 'Add query')}
      >
        <Plus size={13} />
      </Button>
      {seed && (
        <EditQueryModal
          connector={connector}
          queryName={seed.queryName}
          seed={seed.seed}
          onClose={() => setSeed(null)}
          onSaved={onSaved}
        />
      )}
    </>
  )
}

export default EditQueryButton
