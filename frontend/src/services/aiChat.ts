// The chat drawer's plain logic: which starter questions apply to the page, the model the
// operator picked on this device, and the conversation kept across drawer open / close and
// page reloads (localStorage, last HISTORY_MAX messages). No React here.
import type { ChatMessage, StarterGroup } from '../types/ai'

export const HISTORY_MAX = 40
const HISTORY_KEY = 'liberty.ai.history'
const MODEL_KEY = 'liberty.ai.model'

/** The starter questions for the page: the most specific key wins — ``app/screen``, then
 *  ``app``, then ``*``. */
export function pickStarters(starters: Record<string, string[]> | undefined, app?: string | null, screen?: string | null): string[] {
  if (!starters) return []
  const keys = [app && screen ? `${app}/${screen}` : null, app || null, '*']
  for (const k of keys) {
    const list = k ? starters[k] : undefined
    if (list && list.length > 0) return list
  }
  return []
}

/** The starter groups for the page: the most specific key that has GROUPS wins; without any,
 *  the flat starters of the most specific key become one unnamed group. */
export function pickStarterGroups(
  groups: Record<string, StarterGroup[]> | undefined, starters: Record<string, string[]> | undefined, app?: string | null, screen?: string | null,
): StarterGroup[] {
  const keys = [app && screen ? `${app}/${screen}` : null, app || null, '*']
  for (const k of keys) {
    const list = k && groups ? groups[k] : undefined
    if (list && list.length > 0) return list.filter((g) => g.starters.length > 0)
  }
  const flat = pickStarters(starters, app, screen)
  return flat.length ? [{ label: null, starters: flat }] : []
}

export function loadHistory(): ChatMessage[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed.filter((m): m is ChatMessage =>
      !!m && typeof m === 'object' && ((m as ChatMessage).role === 'user' || (m as ChatMessage).role === 'assistant') && typeof (m as ChatMessage).content === 'string')
  } catch { return [] }
}

/** Keeps the last HISTORY_MAX messages, starting on a user turn so the API's "first message
 *  is a user message" rule holds after the cut. */
export function trimHistory(messages: ChatMessage[]): ChatMessage[] {
  let kept = messages.slice(-HISTORY_MAX)
  while (kept.length > 0 && kept[0].role !== 'user') kept = kept.slice(1)
  return kept
}

export function saveHistory(messages: ChatMessage[]): void {
  try {
    const kept = trimHistory(messages)
    if (kept.length === 0) localStorage.removeItem(HISTORY_KEY)
    else localStorage.setItem(HISTORY_KEY, JSON.stringify(kept))
  } catch { /* storage blocked — the conversation just does not survive the drawer */ }
}

export function loadModel(): string | null {
  try { return localStorage.getItem(MODEL_KEY) } catch { return null }
}

export function saveModel(model: string | null): void {
  try {
    if (model) localStorage.setItem(MODEL_KEY, model)
    else localStorage.removeItem(MODEL_KEY)
  } catch { /* ignore */ }
}
