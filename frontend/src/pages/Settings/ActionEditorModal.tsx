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
import { useEffect } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { X } from 'lucide-react'
import {
  Button, ModalBody, ModalFooter, ModalHeader, Overlay, ScreenDialogModal, Row,
  type JsonSchema, type SearchSelectOption,
} from '../../common'
import ActionTreeView from './ActionTreeView'
import type { ActionPath } from './actionPath'

type RowDict = Record<string, unknown>

// The action editor sits above the Screen Designer (z-index 400) but below the global
// confirm/prompt modal (z-index 2000) so a confirm-delete fired from inside the editor still
// surfaces correctly. Same Overlay tile as everywhere else, just bumped up.
const RaisedOverlay = styled(Overlay)`z-index: 800;`

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

  // Escape closes — same convention every modal in the app uses.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])

  return createPortal(
    // No backdrop-click-to-close — an outside click must not discard in-progress action edits.
    // Close via the header X / Cancel only. The modal still stops propagation so clicks on its
    // content don't bubble up to the surrounding designer's overlay (which would close the
    // designer entirely — that was the bug in the first cut of this component).
    <RaisedOverlay>
      <ScreenDialogModal onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          <Row gap={10} style={{ alignItems: 'center', justifyContent: 'space-between' }}>
            <span>{rootLabel}</span>
            <Button $variant="ghost" $size="sm" onClick={onClose} title={t('common.close')}>
              <X size={14} />
            </Button>
          </Row>
        </ModalHeader>
        <ModalBody>
          {/* ActionTreeView in editor mode — same component the Visual Designer's Inspector
              uses, just hosted in a focused modal instead of the right column. ``showBreadcrumb``
              defaults to true; the operator pops back to the modal's root via the topmost crumb
              (clicking ``rootLabel`` clears the path → ``onPathChange(null)`` triggers ``onClose``). */}
          <ActionTreeView
            actions={actions}
            onChange={onChange}
            path={path}
            onPathChange={(next) => {
              // ``null`` (or empty path) means the operator clicked the root crumb — close the
              // modal entirely since the parent's tab already shows the same list.
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
        </ModalBody>
        <ModalFooter>
          <Row gap={8}>
            <Button $variant="primary" $size="sm" onClick={onClose}>
              {t('common.close')}
            </Button>
          </Row>
        </ModalFooter>
      </ScreenDialogModal>
    </RaisedOverlay>,
    document.body,
  )
}
