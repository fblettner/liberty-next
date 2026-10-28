// The token picker's plain logic (no React): splice a token into a text at the caret, and the
// token sets of the templated fields.

/** *text* with *token* put between *start* and *end* (the selection, replaced); a caret past the
 *  end appends. Returns the new text and where the caret goes. */
export function insertAt(text: string, start: number | null | undefined, end: number | null | undefined, token: string): { text: string; caret: number } {
  const s = Math.max(0, Math.min(start ?? text.length, text.length))
  const e = Math.max(s, Math.min(end ?? s, text.length))
  return { text: text.slice(0, s) + token + text.slice(e), caret: s + token.length }
}

export interface Placeholder { name: string; label: string }

/** A job event's fields (notification rules with a job trigger). */
export const JOB_EVENT_TOKENS: Placeholder[] = [
  { name: 'JOB', label: 'Job id' }, { name: 'RUN_ID', label: 'Run id' }, { name: 'STATE', label: 'Run state' },
  { name: 'ERROR', label: 'Error message' }, { name: 'DURATION_MINUTES', label: 'Duration (minutes)' },
  { name: 'ROWS', label: 'Rows affected' }, { name: 'EXPECTED_AT', label: 'Expected / deadline time' },
]
/** A screen event's fields (the written row's columns come on top). */
export const SCREEN_EVENT_TOKENS: Placeholder[] = [
  { name: 'USER', label: 'Who wrote it' }, { name: 'APP', label: 'Application' }, { name: 'SCREEN', label: 'Screen' }, { name: 'EVENT', label: 'insert / update / delete' },
]
/** The notify step's fields. */
export const NOTIFY_STEP_TOKENS: Placeholder[] = [
  { name: 'JOB', label: 'Job id' }, { name: 'RUN_ID', label: 'Run id' }, { name: 'FILES', label: 'The attached file names' }, { name: 'DATE', label: "Today's date" },
]
