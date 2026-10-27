// One playbook of ai.toml: an id (new ones only), a title the assistant picks it by, an
// optional one-line description, and the procedure itself in Markdown.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Field, Input, Textarea } from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import type { PlaybookBody } from '../../types/config'
import { colors, fontSize } from '../../theme'

export function AiPlaybookModal({ initial, takenIds, onSave, onClose }: {
  /** null = a new playbook. */
  initial: (PlaybookBody & { id: string }) | null
  takenIds: string[]
  onSave: (id: string, body: PlaybookBody) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [id, setId] = useState(initial?.id ?? '')
  const [title, setTitle] = useState(initial?.title ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [body, setBody] = useState(initial?.body ?? '')
  const idOk = !!initial || (/^[A-Za-z][A-Za-z0-9_-]{0,60}$/.test(id) && !takenIds.includes(id))
  const dirty = initial ? title !== initial.title || (description || '') !== (initial.description || '') || body !== initial.body : !!(id || title || body)
  return (
    <EditorModalShell title={initial ? `${t('settings.ai.playbook')} · ${initial.id}` : t('settings.ai.addPlaybook')} onClose={onClose}
      dirty={dirty} saveDisabled={!idOk || !title.trim() || !body.trim()}
      onSave={() => onSave(id, { title: title.trim(), description: description.trim() || undefined, body })}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '4px 0' }}>
        {!initial && (
          <Field label={t('settings.ai.playbookId')}>
            <Input value={id} onChange={(e) => setId(e.target.value)} placeholder="create_user" />
            {id && !idOk && <span style={{ color: colors.red.main, fontSize: fontSize.micro }}>{t('settings.ai.playbookIdRule')}</span>}
          </Field>
        )}
        <Field label={t('settings.ai.playbookTitle')}><Input value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
        <Field label={t('settings.ai.playbookDescription')}><Input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        <Field label={t('settings.ai.playbookBody')}>
          <Textarea value={body} onChange={(e) => setBody(e.target.value)} rows={14} spellCheck={false} style={{ fontFamily: 'inherit' }} />
        </Field>
      </div>
    </EditorModalShell>
  )
}
