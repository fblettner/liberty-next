// Focused modal for editing one action at a time — used by ScreenEditor's Actions + Row
// menu tabs to host :class:`ActionTreeView` in editor mode without competing with the
// surrounding screen-editor tabs for vertical real estate.
//
// Why a modal rather than inline expansion: ScreenEditor's Actions tab carries 7 distinct
// hook lists (on_load / on_save / on_cancel / screen.actions / on_insert / on_update /
// on_delete) — even with the new list-mode rendering (one flat row per action) the
// chain-editor body would push everything below it off-screen. A modal opens above the tab,
// the editor gets the whole viewport-ish frame, and the underlying tab keeps showing the
// full set of lists so the operator doesn't lose context.
//
// **Portaling** — the surrounding Screen Designer modal uses ``backdrop-filter`` on its
// frame, which creates a containing block for ``position: fixed`` descendants. Without the
// portal, our Overlay would be positioned relative to the designer modal (not the viewport)
// and clicks would land on weird coordinates. ``createPortal(<>, document.body)`` escapes
// the designer's stacking context entirely. The z-index sits at 800 so we paint above the
// designer (z-index 400) but below the global ``useModals`` confirm/prompt overlay
// (z-index 2000) — a confirm-delete from inside the action editor still surfaces correctly.
import { useTranslation } from 'react-i18next'
import { Button, type JsonSchema, type SearchSelectOption } from '../../common'
import { EditorModalShell } from '../../common/EditorModalShell'
import ActionTreeView from './ActionTreeView'
import type { ActionPath } from './actionPath'

type RowDict = Record<string, unknown>

export interface ActionEditorModalProps {
  /** The list this modal is editing — the root for ``ActionTreeView`` to walk. */
  actions: RowDict[]
  /** Persists changes back to the source slot (dialog.on_save, screen.row_menu, …). */
  onChange: (next: RowDict[]) => void
  /** Current path within ``actions``. */
  path: ActionPath
  /** Path change handler — also closes the modal when set to ``null``. */
  onPathChange: (next: ActionPath | null) => void
  /** The screen JSON schema's ``$defs`` map. */
  defs: Record<string, JsonSchema>
  /** Screen's effective connector — fallback for actions without their own. */
  effectiveConnector: string
  /** Pencil-button raise → EditQueryModal in the surrounding screen. */
  onEditQuery: (connector: string, queryName: string) => void
  /** Breadcrumb root label — typically the hook's display name (``On save``, ``Row context
   *  menu``, …) so the operator knows which list they're editing. */
  rootLabel: string
  /** The firing screen's read-query columns — threaded down to ``ParamBindList`` so the
   *  source dropdown surfaces the firing row's column names alongside the chain-context
   *  candidates. Pass ``screen.columns`` from the parent. */
  screenReadColumns?: RowDict[]
  /** Extra ``source`` autocomplete options for this hook (e.g. on_duplicate's SOURCE_<col>). */
  extraSourceOptions?: SearchSelectOption[]
  /** Fired when the modal should close (click outside / Esc / X / Close button / breadcrumb
   *  to root). */
  onClose: () => void
}

export default function ActionEditorModal({
  actions, onChange, path, onPathChange, defs, effectiveConnector, onEditQuery, rootLabel,
  screenReadColumns, extraSourceOptions, onClose,
}: ActionEditorModalProps) {
  const { t } = useTranslation()

  // Sits above the Screen Designer (400) but below the global confirm/prompt overlay (2000), so a
  // confirm-delete fired from inside still surfaces. No Save — edits are live via onChange; the
  // footer is a single Close.
  return (
    <EditorModalShell
      variant="screen"
      overlayZIndex={800}
      title={rootLabel}
      onClose={onClose}
      footer={<Button $variant="primary" $size="sm" onClick={onClose}>{t('common.close')}</Button>}
    >
      {/* ActionTreeView in editor mode — same component the Visual Designer's Inspector uses. The
          operator pops back to the root via the topmost crumb (clearing the path → onClose). */}
      <ActionTreeView
        actions={actions}
        onChange={onChange}
        path={path}
        onPathChange={(next) => {
          if (next == null || next.length === 0) onClose()
          else onPathChange(next)
        }}
        defs={defs}
        effectiveConnector={effectiveConnector}
        onEditQuery={onEditQuery}
        rootLabel={rootLabel}
        screenReadColumns={screenReadColumns}
        extraSourceOptions={extraSourceOptions}
      />
    </EditorModalShell>
  )
}
