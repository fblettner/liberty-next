// The chat drawer's plain logic: which starter questions apply to the page, the model the
// operator picked on this device, and the conversation kept across drawer open / close and
// page reloads (localStorage, last HISTORY_MAX messages), and the conversations and notes kept for
// the user on the server (the saved-views store, /api/views — kinds `chat` and `ai_note`). No React here.
import { api } from '../api/client'
import type { AiNote, ChatMessage, Conversation, StarterGroup } from '../types/ai'

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

/** The starter groups for the page: the most specific key wins, whatever form it has — its
 *  groups when it carries some, else its flat starters as one unnamed group. A screen's flat
 *  list therefore shows on that screen even when the app has themed groups. */
export function pickStarterGroups(
  groups: Record<string, StarterGroup[]> | undefined, starters: Record<string, string[]> | undefined, app?: string | null, screen?: string | null,
): StarterGroup[] {
  const keys = [app && screen ? `${app}/${screen}` : null, app || null, '*']
  for (const k of keys) {
    if (!k) continue
    const list = groups?.[k]
    if (list && list.length > 0) return list.filter((g) => g.starters.length > 0)
    const flat = starters?.[k]
    if (flat && flat.length > 0) return [{ label: null, starters: flat }]
  }
  return []
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


// ── conversations and notes, kept for the user on the server ─────────────────────────────

const CONVERSATION_KEY = 'liberty.ai.conversation'
const VIEW_KEY = 'conversations'
/** The most conversations kept per user: saving one more drops the oldest. */
export const CONVERSATIONS_MAX = 30
const TITLE_MAX = 60

interface SavedView<P> { name: string; payload: P; updated_at: string | null }

export function newConversationId(): string {
  return `c-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

/** The conversation the drawer is on (this device); a new id when there is none. */
export function currentConversationId(): string {
  try {
    const id = localStorage.getItem(CONVERSATION_KEY)
    if (id) return id
  } catch { /* ignore */ }
  const id = newConversationId()
  setCurrentConversationId(id)
  return id
}

export function setCurrentConversationId(id: string): void {
  try { localStorage.setItem(CONVERSATION_KEY, id) } catch { /* ignore */ }
}

/** A conversation's title: its first question, on one line, cut. */
export function titleOf(messages: ChatMessage[]): string {
  const first = messages.find((m) => m.role === 'user')?.content ?? ''
  const line = first.replace(/\s+/g, ' ').trim()
  return line.length > TITLE_MAX ? `${line.slice(0, TITLE_MAX - 1)}…` : line
}

/** The conversations beyond CONVERSATIONS_MAX, oldest first — what a save removes. */
export function overflow(conversations: Conversation[]): Conversation[] {
  const byAge = [...conversations].sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
  return byAge.slice(CONVERSATIONS_MAX)
}

/** The user's conversations, the latest first. */
export async function listConversations(): Promise<Conversation[]> {
  const r = await api.get<{ views: SavedView<Partial<Conversation>>[] }>(`/api/views/chat?key=${VIEW_KEY}`)
  return r.views
    .map((v) => ({ id: v.name, title: v.payload.title || v.name, app: v.payload.app ?? null, updated_at: v.updated_at, messages: v.payload.messages ?? [] }))
    .sort((a, b) => (b.updated_at ?? '').localeCompare(a.updated_at ?? ''))
}

/** Save (create or replace) a conversation; an empty one is not kept. */
export async function saveConversation(id: string, messages: ChatMessage[], app: string | null): Promise<void> {
  const kept = trimHistory(messages)
  if (kept.length === 0) return
  await api.put('/api/views/chat', { key: VIEW_KEY, name: id, payload: { title: titleOf(kept), app, messages: kept } })
}

export async function deleteConversation(id: string): Promise<void> {
  await api.del(`/api/views/chat?key=${VIEW_KEY}&name=${encodeURIComponent(id)}`)
}

/** The user's notes for an application and for every application (`*`). */
export async function listNotes(app: string | null): Promise<AiNote[]> {
  const keys = ['*', ...(app && app !== '*' ? [app] : [])]
  const out: AiNote[] = []
  for (const key of keys) {
    const r = await api.get<{ views: SavedView<{ text?: string }>[] }>(`/api/views/ai_note?key=${encodeURIComponent(key)}`)
    for (const v of r.views) if (v.payload.text) out.push({ id: v.name, app: key, text: v.payload.text })
  }
  return out
}

/** Save a note the user accepted on its card. */
export async function saveNote(app: string, text: string): Promise<void> {
  await api.put('/api/views/ai_note', { key: app || '*', name: `n-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`, payload: { text } })
}

export async function deleteNote(note: AiNote): Promise<void> {
  await api.del(`/api/views/ai_note?key=${encodeURIComponent(note.app)}&name=${encodeURIComponent(note.id)}`)
}
