// "Add to a menu": a saved analysis or dashboard published as a menu entry (POST /admin/menus/publish —
// liberty/web/admin/menu_publish.py). The entry opens the Explorer on it, its result at once. The menus are
// the configuration: a superuser's. A private object is refused by the server — share it first.
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Overlay, Modal, ModalHeader, ModalBody, ModalFooter, Button, Field, Input, Banner, SearchSelect } from '../../common'
import type { MenuNode, MenusByApp } from '../../types/menus'

/** The folders of a menu, each with its path ("Reports › Sales"), in the order of the menu. */
export function menuFolders(items: MenuNode[] | undefined, trail: string[] = []): { id: string; label: string }[] {
  return (items ?? []).flatMap((n) => (n.type ? [] : [{ id: n.id, label: [...trail, n.label].join(' › ') }, ...menuFolders(n.items, [...trail, n.label])]))
}

export function PublishModal({ app, model, kind, id, name, shared, menus, onDone, onClose }: {
  app: string; model: string; kind: 'analysis' | 'dashboard'; id: string; name: string; shared: boolean
  menus: MenusByApp | null
  onDone: (label: string, menuLabel: string) => void
  onClose: () => void
}) {
  const { t } = useTranslation()
  const apps = useMemo(() => Object.values(menus ?? {}).map((m) => ({ value: m.app, label: m.label || m.app, mono: m.app })), [menus])
  const [menuApp, setMenuApp] = useState(menus?.[app] ? app : apps[0]?.value ?? app)
  const [parent, setParent] = useState('')
  const [label, setLabel] = useState(name)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const folders = useMemo(() => menuFolders(menus?.[menuApp]?.items), [menus, menuApp])
  const submit = async () => {
    setBusy(true); setError(null)
    try {
      await api.post('/admin/menus/publish', { app, model, kind, id, menu_app: menuApp, label: label.trim(), parent: parent || null })
      onDone(label.trim(), menus?.[menuApp]?.label || menuApp)
    } catch (e) { setError(e instanceof ApiError ? e.message : String(e)) } finally { setBusy(false) }
  }
  return (
    <Overlay>
      <Modal onClick={(e) => e.stopPropagation()} style={{ width: 'min(480px, 94vw)' }} data-publish-modal>
        <ModalHeader>{t('explorer.publish.title', { name })}</ModalHeader>
        <ModalBody>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {!shared && <Banner $tone="warning">{t('explorer.publish.private')}</Banner>}
            <Field label={t('explorer.publish.menu')}><SearchSelect value={menuApp} options={apps} onChange={(v) => { setMenuApp(v || app); setParent('') }} /></Field>
            <Field label={t('explorer.publish.folder')}><SearchSelect value={parent} options={folders.map((f) => ({ value: f.id, label: f.label }))} anyLabel={t('explorer.publish.top')} onChange={(v) => setParent(v ?? '')} /></Field>
            <Field label={t('explorer.publish.label')}><Input value={label} onChange={(e) => setLabel(e.target.value)} /></Field>
            {error && <Banner $tone="error">{error}</Banner>}
          </div>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onClose} disabled={busy}>{t('common.cancel')}</Button>
          <Button $size="sm" $variant="primary" disabled={busy || !label.trim() || !shared} onClick={() => void submit()}>{t('explorer.publish.add')}</Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
