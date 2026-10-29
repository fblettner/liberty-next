// The grid's row context menu (slice 6) — `Screen.row_menu` actions fired on right-click or from
// the row's leading handle, run through the shared action runner. Owned by ResultTable; the
// prompt-before-fire plumbing (`requestPrompt`) stays there, shared with the screen's toolbar
// actions.
import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import type { DrillTarget } from '../../services/drill'
import type { Action, ScreenDetail } from '../../types/screens'
import { colors, fontSize, fonts, radius } from '../../theme'
import { runChain, type ActionRunnerDeps } from './actionRunner'
import type { DataRow } from './resultColumns'

// Row context menu (slice 6) — a small floating panel anchored at the right-click coords.
// `position: fixed` so the page scroll doesn't drift the menu out of place mid-action; sized to
// the content with a sane min/max so a single-action menu doesn't shrink to a sliver.
const RowMenuBox = styled.div`
  position: fixed; z-index: 500; min-width: 200px; max-width: 320px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.dropdown}; color: ${colors.text.primary};
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.32);
  display: flex; flex-direction: column; padding: 4px 0;
`
const RowMenuItem = styled.button<{ $busy?: boolean }>`
  display: flex; align-items: center; gap: 8px; padding: 7px 14px; border: none; background: transparent;
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  cursor: ${({ $busy }) => ($busy ? 'default' : 'pointer')};
  opacity: ${({ $busy }) => ($busy ? 0.5 : 1)};
  text-align: left;
  & .id { font-family: ${fonts.mono}; color: ${colors.text.muted}; font-size: ${fontSize.micro}; margin-left: auto; }
  &:hover { background: var(--hover-subtle); }
  &:disabled { opacity: 0.4; cursor: default; }
`
const RowMenuErr = styled.div`
  padding: 6px 14px 8px; color: ${colors.red.main};
  font-size: ${fontSize.micro}; font-family: ${fonts.sans}; max-width: 320px; word-break: break-word;
`

export function useRowMenu({
  screen, connector, editMode, requestPrompt, sharedActions, drillInlineDefault, navigate, onCurrentRow, onSaved, onDrill,
}: {
  screen?: ScreenDetail | null
  connector: string
  /** The grid's batch edit: no row menu while it is on. */
  editMode: boolean
  requestPrompt: ActionRunnerDeps['requestPrompt']
  sharedActions: ActionRunnerDeps['sharedActions'] | null
  /** The app's default for a drill whose action does not say (`[app] drill_inline`). */
  drillInlineDefault: boolean
  navigate: (url: string) => void
  onCurrentRow?: (row: DataRow) => void
  onSaved?: () => void
  onDrill?: (target: DrillTarget) => void
}): {
  rowMenu: Action[]
  openRowMenu: (row: DataRow, e: React.MouseEvent<HTMLElement>) => void
  runRowAction: (a: Action, ctx: DataRow) => Promise<void>
  rowMenuHandle: { icon: 'drill' | 'menu'; title: string; onClick: (row: DataRow, e: React.MouseEvent<HTMLElement>) => void }
  rowMenuPopup: ReactNode
} {
  const { t } = useTranslation()
  // Row context menu (slice 6) — fires `Screen.row_menu` actions on right-click. The menu is a
  // floating overlay anchored at the click coords. We store the row + position; the menu component
  // closes on click-outside / Escape. Each item's ParamBinds resolve against the row's values
  // (the v2 form of v1's row-level action context — same Action shape used by dialog on_save).
  const rowMenu: Action[] = useMemo(
    () => (Array.isArray(screen?.row_menu) ? (screen!.row_menu as Action[]) : []),
    [screen],
  )
  const [menuState, setMenuState] = useState<{ row: DataRow; x: number; y: number } | null>(null)
  const closeMenu = useCallback(() => setMenuState(null), [])
  const openRowMenu = useCallback((row: DataRow, e: React.MouseEvent<HTMLElement>) => {
    if (rowMenu.length === 0 || editMode) return
    onCurrentRow?.(row)
    setMenuState({ row, x: e.clientX, y: e.clientY })
  }, [rowMenu.length, editMode, onCurrentRow])

  // Row-menu action runner — delegates to :func:`runChain` from ``actionRunner.ts`` (Slice B).
  // The runner handles ChainAction recursion, IfAction branching, LoopAction iteration, and
  // dotted-path ``ParamBind.source`` resolution against the chain context — so a migrated
  // multi-step workflow (NOMAJDE's F00926 "Import Security" landed on a row-menu, hypothetically)
  // executes end-to-end. ``formCtx = row`` so plain ``source: 'USR_ID'`` references still read
  // from the clicked row (the existing v2 / hand-written row-menu semantics). ``deps.navigate``
  // wires the v1 "drill into another table" pattern — opens ``/sql/<connector>/<to>`` with the
  // resolved binds as URL params; the destination's TableView seeds its param form from those.
  const [menuBusy, setMenuBusy] = useState<string | null>(null)  // action id while it's running
  const [menuError, setMenuError] = useState<string | null>(null)
  const runRowAction = useCallback(async (a: Action, ctx: DataRow) => {
    setMenuBusy(a.id); setMenuError(null)
    const result = await runChain([a], {}, ctx, {
      defaultConnector: connector,
      requestPrompt,
      sharedActions: sharedActions ?? undefined,
      navigate: (to, conn, params, screen, inline) => {
        if ((inline ?? drillInlineDefault) && onDrill) {
          // the in-place drill: the parent TableView opens the target over this grid
          setMenuBusy(null); closeMenu()
          onDrill({ connector: conn, query: to, screen: screen ?? null, params })
          return
        }
        const qs = new URLSearchParams()
        for (const [k, v] of Object.entries(params)) {
          if (v != null && v !== '') qs.set(k, String(v))
        }
        // A pinned screen opens the dedicated /screen/<conn>/<id> route (that exact screen's
        // columns / filters / dialog apply); otherwise the query route, resolved by read query.
        const base = screen
          ? `/screen/${encodeURIComponent(conn)}/${encodeURIComponent(screen)}`
          : `/sql/${encodeURIComponent(conn)}/${encodeURIComponent(to)}`
        const url = base + (qs.toString() ? `?${qs.toString()}` : '')
        // Close the menu *before* navigating — leaving it open during the route change makes the
        // overlay flicker on the destination page until the document-mousedown listener fires.
        setMenuBusy(null); closeMenu()
        navigate(url)
      },
    })
    setMenuBusy(null)
    if (!result.ok) { setMenuError(result.error || (a.label || a.id)); return }
    if (result.cancelled) { closeMenu(); return }   // soft cancel from a prompt — no error
    closeMenu(); onSaved?.()
    // Notify-action messages collect on ``result.warnings``; surface them in the menu's
    // error/status slot (no global toast yet — same convention as before).
    if (result.warnings.length > 0) {
      console.info('row-menu warnings:', result.warnings)
    }
  }, [connector, onSaved, closeMenu, navigate, requestPrompt, sharedActions, onDrill, drillInlineDefault])
  // The row's action handle (the grid's leading cell): a single navigate action fires at once —
  // a drill — the handle showing the action's label; several actions open the row menu.
  const rowMenuHandle = useMemo(() => {
    // a lone navigate / open_url fires at once from the row handle (a drill), no menu
    const only = rowMenu.length === 1 && (rowMenu[0].type === 'navigate' || rowMenu[0].type === 'open_url') ? rowMenu[0] : null
    return only
      ? { icon: 'drill' as const, title: only.label || only.id, onClick: (row: DataRow) => { onCurrentRow?.(row); void runRowAction(only, row) } }
      : { icon: 'menu' as const, title: t('table.rowMenu'), onClick: openRowMenu }
  }, [rowMenu, runRowAction, openRowMenu, onCurrentRow, t])

  // Close the row context menu on Escape / click-outside. The overlay itself stops propagation
  // (see the menu render below), so clicking *inside* the menu doesn't close it.
  useEffect(() => {
    if (!menuState) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeMenu() }
    const onClick = () => closeMenu()
    document.addEventListener('keydown', onKey)
    document.addEventListener('mousedown', onClick)
    return () => {
      document.removeEventListener('keydown', onKey)
      document.removeEventListener('mousedown', onClick)
    }
  }, [menuState, closeMenu])

  // Row context menu (slice 6) — a floating panel anchored at the right-click coords. Each
  // item runs its action against the picked row; `mousedown` inside the menu is stopped from
  // bubbling so the document-level click-outside listener doesn't close it underneath us.
  const rowMenuPopup = menuState && (
    <RowMenuBox
      style={{ top: menuState.y, left: menuState.x }}
      onMouseDown={(e) => e.stopPropagation()}
      role="menu"
    >
      {rowMenu.map((a) => (
        <RowMenuItem
          key={a.id}
          role="menuitem"
          disabled={menuBusy != null && menuBusy !== a.id}
          $busy={menuBusy === a.id}
          onClick={() => runRowAction(a, menuState.row)}
          title={a.id}
        >
          <span>{a.label || a.id}</span>
          <span className="id">{a.type}</span>
        </RowMenuItem>
      ))}
      {menuError && <RowMenuErr>{menuError}</RowMenuErr>}
    </RowMenuBox>
  )

  return { rowMenu, openRowMenu, runRowAction, rowMenuHandle, rowMenuPopup }
}
