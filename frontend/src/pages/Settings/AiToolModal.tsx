// One tool of ai.toml: an id (new ones only; the model sees it as `<app>_<id>`), the plugin
// function it calls, the description the model picks it by, and the permission it needs.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CallableSelect, Field, Input, Textarea } from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import type { AiToolBody } from '../../types/config'
import { colors, fontSize } from '../../theme'
import { useDirtyGuard } from '../../tabs/dirtyGuard'


export function AiToolModal({ app, initial, takenIds, onSave, onClose }: {
  app: string
  /** null = a new tool. */
  initial: (AiToolBody & { id: string }) | null
  takenIds: string[]
  onSave: (id: string, body: AiToolBody) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const [id, setId] = useState(initial?.id ?? '')
  const [callable, setCallable] = useState(initial?.callable ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [permission, setPermission] = useState(initial?.permission ?? '')
  const idOk = !!initial || (/^[a-z][a-z0-9_]{0,40}$/.test(id) && !takenIds.includes(id))
  const callableOk = /^[A-Za-z_][\w.]*:[A-Za-z_]\w*$/.test(callable)
  const dirty = initial
    ? callable !== initial.callable || description !== initial.description || (permission || '') !== (initial.permission || '')
    : !!(id || callable || description)
  useDirtyGuard(dirty)          // the shell asks before a closed tab or a reload loses these edits
  return (
    <EditorModalShell title={initial ? `${t('settings.ai.tool')} · ${app}_${initial.id}` : t('settings.ai.addTool')} onClose={onClose}
      dirty={dirty} saveDisabled={!idOk || !callableOk || !description.trim()}
      onSave={() => onSave(id, { callable, description: description.trim(), permission: permission.trim() || undefined })}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '4px 0' }}>
        {!initial && (
          <Field label={t('settings.ai.toolId')}>
            <Input value={id} onChange={(e) => setId(e.target.value)} placeholder="object_access" />
            {id && !idOk && <span style={{ color: colors.red.main, fontSize: fontSize.micro }}>{t('settings.ai.toolIdRule')}</span>}
            {id && idOk && <span style={{ color: colors.text.muted, fontSize: fontSize.micro }}>{t('settings.ai.toolName', { name: `${app}_${id}` })}</span>}
          </Field>
        )}
        <Field label={t('settings.ai.toolCallable')}>
          <CallableSelect value={callable} onChange={setCallable} />
          <span style={{ color: colors.text.muted, fontSize: fontSize.micro }}>{t('settings.ai.toolCallableHint')}</span>
        </Field>
        <Field label={t('settings.ai.toolDescription')}>
          <Textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={5} style={{ fontFamily: 'inherit' }} />
        </Field>
        <Field label={t('settings.ai.toolPermission')}>
          <Input value={permission} onChange={(e) => setPermission(e.target.value)} placeholder={callable ? `plugin:${callable}` : 'plugin:<callable>'} />
        </Field>
      </div>
    </EditorModalShell>
  )
}
