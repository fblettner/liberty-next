// Self-contained screen visual builder modal. Fetches its own `/admin/config/screens/parsed` +
// schema on open, lets the operator edit one screen via the existing ScreenEditor, and PUTs the
// updated screens.toml on Save. Decoupled from ScreensBuilder's page-level state so any editor
// can open the visual builder for a specific screen — clicked from the Screens list, jumped to
// from a Connectors → CRUD table — without a tab switch.
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import {
  Banner, Centered, FrameworkEnumsContext, useModals,
  type FrameworkEnums, type JsonSchema,
} from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import type { ConfigSchemas, ScreensDoc, Screen } from '../../types/config'
import ScreenEditor from './ScreenEditor'
import { colors, fonts } from '../../theme'

type AppScreens = Record<string, Record<string, Screen>>

interface Props {
  app: string
  screenId: string
  onClose: () => void
  /** Called after a successful Save (the screens.toml is committed + reloaded by then). */
  onSaved?: () => void
}

export function ScreenDesignerModal({ app, screenId, onClose, onSaved }: Props) {
  const { t } = useTranslation()
  const modals = useModals()
  const [doc, setDoc] = useState<AppScreens | null>(null)
  const [schema, setSchema] = useState<JsonSchema | null>(null)
  const [enums, setEnums] = useState<FrameworkEnums | null>(null)
  const [value, setValue] = useState<Record<string, unknown> | null>(null)
  const [currentId, setCurrentId] = useState(screenId)
  const [original, setOriginal] = useState<string>('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [fullscreen, setFullscreen] = useState(true)
  const closedRef = useRef(false)

  // Fetch schema + screens once on mount.
  useEffect(() => {
    let cancelled = false
    Promise.all([
      api.get<ConfigSchemas>('/admin/config/schema'),
      api.get<ScreensDoc>('/admin/config/screens/parsed'),
    ])
      .then(([s, d]) => {
        if (cancelled) return
        const defs = (s.screens.$defs ?? {}) as Record<string, JsonSchema>
        const screen = (defs.Screen ?? {}) as JsonSchema
        setSchema({ ...screen, $defs: defs })
        setEnums(s.framework_enums)
        setDoc(d.screens)
        const v = d.screens[app]?.[screenId] as unknown as Record<string, unknown> | undefined
        if (v) { setValue(v); setOriginal(JSON.stringify(v)) }
        else setError(t('settings.screens.notFound', 'Screen [{{app}}.{{id}}] not found.', { app, id: screenId }))
      })
      .catch((e) => { if (!cancelled) setError(e instanceof ApiError ? e.message : String(e)) })
    return () => { cancelled = true }
  }, [app, screenId, t])

  const dirty = !!value && JSON.stringify(value) !== original

  // Track id renames inside the editor — the dict key + sibling refs follow on Save.
  const onChange = (v: Record<string, unknown>) => {
    setValue(v)
    const nextId = typeof v.id === 'string' && v.id.trim() ? v.id.trim() : currentId
    if (nextId !== currentId) setCurrentId(nextId)
  }

  const save = async () => {
    if (!doc || !value) return
    setBusy(true); setError(null)
    try {
      const appCur = { ...(doc[app] ?? {}) }
      const newId = (typeof value.id === 'string' && value.id.trim()) ? value.id.trim() : currentId
      if (newId !== screenId) delete appCur[screenId]
      appCur[newId] = { ...(value as unknown as Screen), id: newId }
      // Same sibling-rename machinery ScreensBuilder used inline: when the screen's id changes,
      // walk the other screens in this app and rewrite ``row_click_screen`` + nested-table-tab
      // ``screen`` references that pointed at the old id.
      if (newId !== screenId) {
        for (const [otherId, otherScreen] of Object.entries(appCur)) {
          if (otherId === newId) continue
          const sc = otherScreen as unknown as Record<string, unknown>
          let mut: Record<string, unknown> | null = null
          if (sc.row_click_screen === screenId) mut = { ...sc, row_click_screen: newId }
          const dlg = sc.dialog as Record<string, unknown> | undefined
          if (dlg && Array.isArray(dlg.tabs)) {
            const tabs = dlg.tabs as Record<string, unknown>[]
            let tabsChanged = false
            const nextTabs = tabs.map((tab) => {
              if (tab?.type === 'nested_table' && tab.screen === screenId) {
                tabsChanged = true
                return { ...tab, screen: newId }
              }
              return tab
            })
            if (tabsChanged) mut = { ...(mut ?? sc), dialog: { ...dlg, tabs: nextTabs } }
          }
          if (mut) appCur[otherId] = mut as unknown as Screen
        }
      }
      const next = { ...doc, [app]: appCur }
      await api.put<{ saved: boolean }>('/admin/config/screens/parsed', { screens: next })
      await api.post('/admin/reload')
      closedRef.current = true
      onSaved?.()
      onClose()
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e))
    } finally { setBusy(false) }
  }

  const cancel = async () => {
    if (closedRef.current) return
    if (!dirty) { onClose(); return }
    const choice = await modals.choose<'discard' | 'save' | 'keep'>({
      title: t('settings.screens.designer.unsavedTitle', 'Unsaved changes'),
      message: t('settings.screens.designer.unsavedMsg', 'You have unsaved changes in this screen. Save them, discard them, or keep editing?'),
      options: [
        { value: 'discard', label: t('settings.screens.designer.discard', 'Discard'), variant: 'danger' },
        { value: 'save', label: t('common.save'), variant: 'primary' },
        { value: 'keep', label: t('settings.screens.designer.keepEditing', 'Keep editing'), variant: 'ghost', autoFocus: true },
      ],
      cancelValue: 'keep',
    })
    if (choice === 'keep' || choice == null) return
    if (choice === 'discard') onClose()
    else if (choice === 'save') await save()
  }
  // Escape is handled by EditorModalShell (it calls onClose → ``cancel`` → the unsaved prompt).

  // Loading / error states.
  const inner = (!schema || !doc) ? <Centered />
    : error ? <Banner $tone="error">{error}</Banner>
    : !value ? <Centered />
    : (
      <ScreenEditor
        app={app}
        id={currentId}
        value={value}
        schema={schema}
        siblingScreenIds={Object.keys(doc[app] ?? {}).filter((i) => i !== currentId)}
        onChange={onChange}
      />
    )

  return (
    // Context propagates across the shell's portal — ScreenEditor (inside) still gets the enums.
    <FrameworkEnumsContext.Provider value={enums}>
      <EditorModalShell
        variant="visual"
        title={(
          <>
            {t('settings.screens.designerTitle')} ·{' '}
            <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontWeight: 400 }}>
              [screens.{app}.{currentId}]
            </span>
          </>
        )}
        onClose={() => void cancel()}
        onSave={() => void save()}
        dirty={dirty}
        saveDisabled={!dirty}
        busy={busy}
        fullscreen={fullscreen}
        onToggleFullscreen={() => setFullscreen((v) => !v)}
        scrollBody={false}  /* ScreenEditor manages its own per-tab scroll */
      >
        {inner}
      </EditorModalShell>
    </FrameworkEnumsContext.Provider>
  )
}

export default ScreenDesignerModal
