// Unsaved changes, known to the shell. A page that holds edits not yet saved says so with
// `useDirtyGuard(dirty)`; the shell then asks before what would lose them: closing the tab, closing every
// tab, switching the language (every tab is drawn again), an application switch that closes tabs — and the
// browser itself asks before a reload or a closed window. A tab left open in the background loses nothing
// (every open tab stays mounted): only what ends a tab is guarded — and the tab strip marks it, so the user
// sees which page still holds edits after moving to another one.
//
// The registry is plain module state (no React): which tab holds unsaved edits, by whom. One page may hold
// several sources (a builder and an editor window open on top of it); the tab is clean when none is left.
import { createContext, useContext, useEffect, useId, useSyncExternalStore } from 'react'

/** The tab a page is drawn in (set by the tab host); `null` outside one (a window, a test). */
export const TabIdContext = createContext<string | null>(null)

const OUTSIDE = '(page)'
const dirty = new Map<string, Set<string>>()
const watchers = new Set<() => void>()
let stamp = ''          // the dirty tabs as one string: what a watcher compares

function onBeforeUnload(e: BeforeUnloadEvent): void {
  e.preventDefault()
  e.returnValue = ''     // what makes the browser ask (its own wording)
}

function sync(): void {
  const now = [...dirty.keys()].sort().join('\n')
  if (now !== stamp) { stamp = now; for (const fn of watchers) fn() }
  if (typeof window === 'undefined') return
  if (dirty.size > 0) window.addEventListener('beforeunload', onBeforeUnload)
  else window.removeEventListener('beforeunload', onBeforeUnload)
}

/** A source of unsaved edits appears in a tab. */
export function markDirty(tabId: string | null, source: string): void {
  const id = tabId ?? OUTSIDE
  const set = dirty.get(id) ?? new Set<string>()
  set.add(source)
  dirty.set(id, set)
  sync()
}

/** That source has nothing unsaved any more (saved, discarded, or gone). */
export function clearDirty(tabId: string | null, source: string): void {
  const id = tabId ?? OUTSIDE
  const set = dirty.get(id)
  if (!set) return
  set.delete(source)
  if (set.size === 0) dirty.delete(id)
  sync()
}

export const isTabDirty = (tabId: string): boolean => dirty.has(tabId)
export const anyDirty = (): boolean => dirty.size > 0
/** The tabs that hold unsaved edits, among those given (all of them when none is given). */
export const dirtyTabs = (among?: string[]): string[] => [...dirty.keys()].filter((id) => id !== OUTSIDE && (!among || among.includes(id)))

/** Be told when the set of pages holding unsaved edits changes (not at each edit); returns how to stop. */
export function watchDirty(fn: () => void): () => void {
  watchers.add(fn)
  return () => { watchers.delete(fn) }
}

/** hook: the ids of the tabs that hold unsaved edits, followed live (the tab strip's mark). */
export function useDirtyTabs(): Set<string> {
  const now = useSyncExternalStore(watchDirty, () => stamp, () => '')
  return new Set(now ? now.split('\n') : [])
}

/** Says to the shell that this page holds unsaved edits while *isDirty* is true. */
export function useDirtyGuard(isDirty: boolean): void {
  const tabId = useContext(TabIdContext)
  const source = useId()
  useEffect(() => {
    if (!isDirty) return
    markDirty(tabId, source)
    return () => clearDirty(tabId, source)
  }, [isDirty, tabId, source])
}
