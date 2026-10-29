// The record form's row lock (Phase 9 — Socket.IO): acquired when an existing row opens, released
// on close; another session's lock comes back as ``foreignLock`` (the form turns read-only and shows
// "Locked by …"). Owned by ScreenDialog.
import { useEffect, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useSio, useLockState } from '../../sio/SioContext'
import type { HeldLock, LockPayload } from '../../sio/types'
import type { ScreenDetail } from '../../types/screens'
import type { Row } from './dialogHelpers'
import type { DialogMode } from './ScreenDialog'

export function useRecordLock({ open, mode, screen, keyColumns, row, valueFor }: {
  open: boolean
  mode: DialogMode
  screen: ScreenDetail
  keyColumns?: string[]
  row: Row
  /** The row's value of a field, matched case-insensitively. */
  valueFor: (field: string, src: Row) => unknown
}): { foreignLock: HeldLock | null; formatLockTime: (ts: number) => string } {
  const { t } = useTranslation()
  // ── record lock (Phase 9 — Socket.IO) ────────────────────────────────────────────────
  // On open in ``edit`` mode we acquire a row-level lock so a concurrent edit by another
  // user opens the dialog read-only with a "Locked by Alice (Xm ago)" banner. ``add`` mode
  // skips locking — the row doesn't exist yet, there's no composite key to lock.
  //
  // **Why we destructure the callbacks instead of depending on the whole ``sio`` object**:
  // ``useSio()`` returns a memoised value whose identity rebuilds every time ``locks`` or
  // ``status`` changes. Putting that value in the effect's deps caused a tight loop on every
  // broadcast — acquire → broadcast → ``locks`` updates → ``sio`` identity changes → effect
  // cleanup releases → effect re-runs and acquires again. ``acquireLock`` / ``releaseLock``
  // are wrapped in ``useCallback([])`` on the provider so their identities are stable; depend
  // on those refs and the loop is gone.
  const { acquireLock, releaseLock } = useSio()
  const lockPayload = useMemo<LockPayload | null>(() => {
    // a read-only screen writes nothing — no lock to take (it only blocked other readers)
    if (screen.read_only) return null
    if (mode !== 'edit') {
      console.info("[lock] skip — mode is", mode)
      return null
    }
    if (!keyColumns || keyColumns.length === 0) {
      console.info("[lock] skip — keyColumns is",
        keyColumns, "for screen", `${screen.app}.${screen.id}`)
      return null
    }
    const kv: Record<string, string> = {}
    for (const k of keyColumns) {
      const v = valueFor(k, row)
      if (v == null || v === '') {
        console.info("[lock] skip — key column", k, "is null/empty in row.",
          { keyColumns, rowKeys: Object.keys(row), screen: `${screen.app}.${screen.id}` })
        return null
      }
      kv[k] = String(v)
    }
    return { app: screen.app, screen: screen.id, key_values: kv }
  }, [mode, keyColumns, row, valueFor, screen.app, screen.id, screen.read_only])

  // Watch the live lock map for this payload. ``locks`` updates as the server's
  // ``lock.acquired`` / ``lock.released`` broadcasts arrive; when Alice releases her lock,
  // ``foreignLock`` clears and the form becomes editable — no reopen needed.
  const { lock: heldLock, ownedByMe } = useLockState(lockPayload)
  const foreignLock = heldLock && !ownedByMe ? heldLock : null

  // Acquire on open / release on close. ``cancelled`` covers a fast close while the ack
  // is still in flight — releases the lock if we happened to win it after unmount.
  //
  // Diagnostic console logs (Phase 9) so a "no lock visible in dashboard" report can be
  // diagnosed without re-instrumenting the build. Open DevTools → Console and look for the
  // ``[lock]`` entries: ``no payload`` (keyColumns missing / row empty), ``acquire request``
  // (effect fired the emit), ``result`` (server ack or denial), ``release on cleanup``
  // (effect cleanup, expected on dialog close).
  useEffect(() => {
    if (!open || lockPayload == null) return
    let cancelled = false
    console.info("[lock] acquire request", lockPayload)
    void (async () => {
      const result = await acquireLock(lockPayload)
      console.info("[lock] result", result)
      if (cancelled && result.ok) releaseLock(lockPayload)
    })()
    return () => {
      cancelled = true
      console.info("[lock] release on cleanup", lockPayload)
      releaseLock(lockPayload)
    }
    // ``mode`` is used only for the diagnostic log; the actual reactive trigger is
    // ``lockPayload`` (which itself depends on mode). Listed in deps for the lint rule.
  }, [open, lockPayload, acquireLock, releaseLock])

  // Human-friendly "X minutes ago" for the banner. Recomputed every render — the WS broadcasts
  // already drive re-renders frequently enough that no setInterval is needed.
  const formatLockTime = (ts: number): string => {
    const diff = Date.now() / 1000 - ts
    if (diff < 60) return t('dialog.lockedSecondsAgo', { count: Math.max(1, Math.floor(diff)) })
    if (diff < 3600) return t('dialog.lockedMinutesAgo', { count: Math.floor(diff / 60) })
    return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  return { foreignLock, formatLockTime }
}
