// Socket.IO client + React context. One persistent connection per logged-in
// user; multiplexes record locks, technical dashboard, and log tail over the
// same socket via Socket.IO events.
//
// **Why Socket.IO and not raw WS**: the prior raw-WS attempt looped on
// open/close because of React effect-identity churn + reconnect handling.
// socket.io-client handles reconnect, transport upgrade, and ack callbacks
// natively — the React layer is just "subscribe to events, expose state via
// hooks". The backend uses python-socketio (see liberty/sio.py).

import {
  createContext, useCallback, useContext, useEffect, useMemo, useRef, useState,
  type ReactNode,
} from "react"
import { io, type Socket } from "socket.io-client"
import { useAuth } from "../auth/AuthContext"
import type {
  DashboardSnapshot, HeldLock, LockPayload, LogEntry,
} from "./types"
import { canonicalLockKey } from "./types"

type SioStatus = "idle" | "connecting" | "open" | "closed"

interface SioContextValue {
  status: SioStatus
  /** This tab's Socket.IO session id. Each browser tab gets its own ``sid`` even when
   *  logged in as the same user — that's how we tell tabs apart for lock ownership.
   *  ``null`` before the first connect or while reconnecting. */
  sid: string | null
  /** Live map of every currently held lock, keyed by canonical room name.
   *  Updated by ``lock.acquired`` / ``lock.released`` broadcasts from the server. */
  locks: Map<string, HeldLock>
  /** Acquire a lock. Resolves once the server responds (ack callback). Never rejects;
   *  failures come back as ``{ok: false, holder?, reason?}``. */
  acquireLock: (payload: LockPayload) => Promise<
    { ok: true; lock: HeldLock } | { ok: false; holder?: HeldLock; reason?: string }
  >
  /** Release a lock — fire-and-forget. The server's broadcast updates the locks map. */
  releaseLock: (payload: LockPayload) => void
  /** Subscribe to the dashboard stream. Returns an unsubscribe function. Snapshots
   *  arrive via the returned callback; ``null`` means the server denied the
   *  subscription (non-superuser). */
  subscribeDashboard: (onSnapshot: (snap: DashboardSnapshot | null) => void) => () => void
  /** Subscribe to the logs stream — buffered replay then live tail. */
  subscribeLogs: (handlers: { onReplay: (entries: LogEntry[]) => void; onLine: (entry: LogEntry) => void }) => () => void
}

const SioContext = createContext<SioContextValue | null>(null)

/** Re-export so consumers don't have to dig into ``./types``. */
export { canonicalLockKey } from "./types"

export function SioProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth()
  const userId = user?.id ?? null
  const [status, setStatus] = useState<SioStatus>("idle")
  const [locks, setLocks] = useState<Map<string, HeldLock>>(() => new Map())
  // This tab's Socket.IO sid. Each browser tab gets a new sid even when logged in as the
  // same user — drives lock ownership in ``useLockState`` (so two tabs of the same user
  // editing the same record correctly show the second tab as locked-by-someone-else).
  const [sid, setSid] = useState<string | null>(null)

  // Subscriber refs — held outside React state so adding/removing a handler
  // doesn't trigger a render cascade. The provider re-renders only on `status`
  // / `locks` changes (the things consumers actually need to know about).
  const socketRef = useRef<Socket | null>(null)
  const dashboardHandlers = useRef<Set<(s: DashboardSnapshot | null) => void>>(new Set())
  const logHandlers = useRef<Set<{ onReplay: (e: LogEntry[]) => void; onLine: (e: LogEntry) => void }>>(new Set())

  // Connect once per logged-in user. Single effect with ``[userId]`` as the
  // only dependency — no `useCallback` whose identity can shift, no React-
  // internal re-trigger path. The whole connection lifecycle stays inline.
  useEffect(() => {
    if (!userId) {
      setStatus("idle")
      setLocks(new Map())
      setSid(null)
      socketRef.current?.disconnect()
      socketRef.current = null
      return
    }

    // Pick up the token from localStorage where AuthContext stores it. We pass
    // it via Socket.IO's ``auth`` payload — it's the standard handshake-time
    // auth mechanism; the server's ``connect`` handler reads it.
    let token: string | null = null
    try {
      const raw = localStorage.getItem("liberty.tokens")
      if (raw) token = (JSON.parse(raw) as { access_token: string }).access_token
    } catch {
      return
    }
    if (!token) return

    setStatus("connecting")
    // Same-origin connection. socket.io-client picks ``ws://`` or ``wss://``
    // based on ``window.location`` automatically; passing ``undefined`` for the
    // URL means "connect back to the page's origin".
    const socket = io({
      path: "/socket.io",
      auth: { token, client_id: window.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2) },
      // Reconnect with exponential backoff up to 30s. Socket.IO handles this
      // itself — the previous raw-WS implementation had to reinvent it badly.
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionDelayMax: 30000,
      // We're confident in our backend; let the transport upgrade naturally.
      transports: ["websocket", "polling"],
    })
    socketRef.current = socket

    socket.on("connect", () => {
      setStatus("open")
      // ``socket.id`` is the server-assigned sid for this connection. Surfacing it lets
      // ``useLockState`` distinguish "this tab holds the lock" from "another tab of the
      // same user holds it" — two browser tabs always get distinct sids.
      setSid(socket.id ?? null)
      // On reconnect: re-subscribe dashboard / logs if the consumers are still
      // mounted. The server forgot we'd subscribed when the previous socket
      // dropped; resubscribing here makes the reconnect transparent to the UI.
      if (dashboardHandlers.current.size > 0) socket.emit("dashboard.subscribe")
      if (logHandlers.current.size > 0) socket.emit("logs.subscribe")
    })
    socket.on("disconnect", () => { setStatus("closed"); setSid(null) })
    socket.on("connect_error", (err) => {
      console.warn("[sio] connect_error", err.message)
      setStatus("closed")
    })

    // Lock broadcasts — every connected client receives these (including the
    // originator), so the UI has one code path for self-vs-other lock state.
    socket.on("lock.acquired", (lock: HeldLock & { room: string }) => {
      setLocks((m) => {
        const next = new Map(m)
        next.set(lock.room, lock)
        return next
      })
    })
    socket.on("lock.released", (lock: HeldLock & { room: string }) => {
      setLocks((m) => {
        const next = new Map(m)
        next.delete(lock.room)
        return next
      })
    })

    socket.on("dashboard:snapshot", (snap: DashboardSnapshot) => {
      dashboardHandlers.current.forEach((h) => h(snap))
    })
    socket.on("log:replay", (payload: { entries: LogEntry[] }) => {
      logHandlers.current.forEach((h) => h.onReplay(payload.entries ?? []))
    })
    socket.on("log:line", (entry: LogEntry) => {
      logHandlers.current.forEach((h) => h.onLine(entry))
    })

    return () => {
      socket.disconnect()
      socketRef.current = null
    }
  }, [userId])

  // ── public API ──────────────────────────────────────────────────────────────
  const acquireLock = useCallback((payload: LockPayload) => {
    return new Promise<{ ok: true; lock: HeldLock } | { ok: false; holder?: HeldLock; reason?: string }>((resolve) => {
      const s = socketRef.current
      if (!s || !s.connected) {
        resolve({ ok: false, reason: "not connected" })
        return
      }
      // Socket.IO's ack callback — the server's handler returns a dict, which
      // arrives here as the single ack argument. Timeout via Socket.IO's own
      // timeout helper (5s); after that the promise resolves as `ok: false`.
      s.timeout(5000).emit("lock", payload, (err: unknown, ack: { ok: boolean; lock?: HeldLock; holder?: HeldLock; reason?: string }) => {
        if (err) {
          resolve({ ok: false, reason: String(err) })
          return
        }
        if (ack?.ok && ack.lock) {
          resolve({ ok: true, lock: ack.lock })
        } else {
          resolve({ ok: false, holder: ack?.holder, reason: ack?.reason })
        }
      })
    })
  }, [])

  const releaseLock = useCallback((payload: LockPayload) => {
    const s = socketRef.current
    if (!s || !s.connected) return
    s.emit("unlock", payload)
  }, [])

  const subscribeDashboard = useCallback((onSnapshot: (snap: DashboardSnapshot | null) => void) => {
    dashboardHandlers.current.add(onSnapshot)
    const s = socketRef.current
    if (s && s.connected) {
      s.emit("dashboard.subscribe", undefined, (ack: { ok: boolean; reason?: string }) => {
        if (!ack?.ok) onSnapshot(null)
      })
    }
    return () => {
      dashboardHandlers.current.delete(onSnapshot)
      if (dashboardHandlers.current.size === 0) {
        const ss = socketRef.current
        if (ss && ss.connected) ss.emit("dashboard.unsubscribe")
      }
    }
  }, [])

  const subscribeLogs = useCallback((handlers: { onReplay: (e: LogEntry[]) => void; onLine: (e: LogEntry) => void }) => {
    logHandlers.current.add(handlers)
    const s = socketRef.current
    if (s && s.connected) {
      s.emit("logs.subscribe", undefined, (ack: { ok: boolean; reason?: string }) => {
        if (!ack?.ok) handlers.onReplay([])  // surface deny — caller checks emptiness via subsequent state
      })
    }
    return () => {
      logHandlers.current.delete(handlers)
      if (logHandlers.current.size === 0) {
        const ss = socketRef.current
        if (ss && ss.connected) ss.emit("logs.unsubscribe")
      }
    }
  }, [])

  const value = useMemo<SioContextValue>(() => ({
    status, sid, locks, acquireLock, releaseLock, subscribeDashboard, subscribeLogs,
  }), [status, sid, locks, acquireLock, releaseLock, subscribeDashboard, subscribeLogs])

  return <SioContext.Provider value={value}>{children}</SioContext.Provider>
}

export function useSio(): SioContextValue {
  const ctx = useContext(SioContext)
  if (!ctx) throw new Error("useSio must be used within <SioProvider>")
  return ctx
}

/** Look up a single lock's current holder + whether it's mine. Returns
 *  ``null`` for an unlocked record. ``ownedByMe`` is what the dialog uses to
 *  decide read-only vs editable.
 *
 *  Ownership compares by **Socket.IO sid** (this tab's connection) — *not* by
 *  user id. Two browser tabs of the same user open the same record → both
 *  carry the same ``user_id`` but distinct ``sid``s, so the second tab
 *  correctly sees ``ownedByMe = false`` and opens read-only with the banner.
 *  v1's locking had the same property: each browser window was its own
 *  Socket.IO room member, and locks were per-connection. */
export function useLockState(payload: LockPayload | null): { lock: HeldLock | null; ownedByMe: boolean } {
  const { sid, locks } = useSio()
  if (!payload) return { lock: null, ownedByMe: false }
  const lock = locks.get(canonicalLockKey(payload)) ?? null
  const ownedByMe = lock != null && sid != null && lock.sid === sid
  return { lock, ownedByMe }
}

/** Subscribe to the dashboard stream for the calling component's lifetime.
 *  Returns ``null`` until the first snapshot arrives, then the latest. Updates
 *  whenever the server pushes (lock change / 5s pool-stat refresh).
 *  ``"denied"`` means the server refused the subscription (non-superuser). */
export function useDashboardSnapshot(): DashboardSnapshot | null | "denied" {
  const { subscribeDashboard, status } = useSio()
  const [snap, setSnap] = useState<DashboardSnapshot | null | "denied">(null)
  useEffect(() => {
    if (status !== "open") return
    return subscribeDashboard((s) => setSnap(s === null ? "denied" : s))
  }, [subscribeDashboard, status])
  return snap
}

/** Subscribe to the logs stream. ``entries`` grows as live lines arrive;
 *  client-side capped at ``maxClientBuffer`` (default 1000) so a long session
 *  doesn't balloon memory. */
export function useLogStream(opts?: { maxClientBuffer?: number }): { entries: LogEntry[]; denied: boolean } {
  const { subscribeLogs, status } = useSio()
  const max = opts?.maxClientBuffer ?? 1000
  const [entries, setEntries] = useState<LogEntry[]>([])
  const [denied, setDenied] = useState(false)
  useEffect(() => {
    if (status !== "open") return
    setDenied(false)
    return subscribeLogs({
      onReplay: (replay) => setEntries(replay.slice(-max)),
      onLine: (line) => setEntries((prev) => {
        const next = prev.length >= max ? prev.slice(prev.length - max + 1) : prev.slice()
        next.push(line)
        return next
      }),
    })
  }, [subscribeLogs, max, status])
  return { entries, denied }
}
