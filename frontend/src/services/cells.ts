// Plain-TS helpers for rendering query-result cells (no React here).

/** Format an arbitrary SQL value for a table cell — `null`/`undefined` → "null"
 *  (flagged via `isNull` so the caller can style it), objects → JSON, else String. */
export function cellText(v: unknown): { text: string; isNull: boolean } {
  if (v === null || v === undefined) return { text: 'null', isNull: true }
  if (typeof v === 'object') return { text: JSON.stringify(v), isNull: false }
  return { text: String(v), isNull: false }
}
