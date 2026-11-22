// Who a message goes to, as the dispatcher reads it: ``user:<name>``, ``role:<name>`` (every
// active user holding the role) or ``email:<address>``. A word without its kind goes nowhere —
// the dispatcher ignores it — so the editors pick the kind and the name, and say when a line
// that was typed has none. No React here.

export type RecipientKind = 'role' | 'user' | 'email'
export const RECIPIENT_KINDS: RecipientKind[] = ['role', 'user', 'email']

export interface Recipient { kind: RecipientKind | null; name: string }

/** A line of a recipients list, taken apart; ``kind`` is null when it names none it knows. */
export function parseRecipient(text: string): Recipient {
  const at = text.indexOf(':')
  const kind = at > 0 ? text.slice(0, at).trim().toLowerCase() : ''
  return (RECIPIENT_KINDS as string[]).includes(kind)
    ? { kind: kind as RecipientKind, name: text.slice(at + 1).trim() }
    : { kind: null, name: text.trim() }
}

/** The line for *name* of *kind*; '' when there is no name. */
export function recipientOf(kind: RecipientKind, name: string): string {
  const n = name.trim()
  return n ? `${kind}:${n}` : ''
}

/** *list* with *name* of *kind* added, once. */
export function addRecipient(list: string[], kind: RecipientKind, name: string): string[] {
  const line = recipientOf(kind, name)
  return !line || list.includes(line) ? list : [...list, line]
}

/** The lines of *list* the dispatcher would ignore (no kind, or no name). */
export function unreadable(list: string[]): string[] {
  return list.filter((line) => { const r = parseRecipient(line); return r.kind === null || r.name === '' })
}

/** A line that looks like an address typed without its kind gets it — ``a@example.com`` →
 *  ``email:a@example.com``; anything else is left for the person to say what it is. */
export function repaired(line: string): string {
  const r = parseRecipient(line)
  return r.kind === null && /^[^\s@:]+@[^\s@:]+\.[^\s@:]+$/.test(r.name) ? `email:${r.name}` : line
}
