// Order-preserving dict-key rename. v1 idiom: `delete obj[old]; obj[new] = val` works but moves
// the new key to the *end* of the iteration order — which shows up in the builder's left nav as
// the renamed item jumping to the bottom of the list. Operators expect a rename to be in-place;
// rebuild the object in original key order with the matching key swapped to keep that intuition.
//
// Returns a *new* object (immutable update friendly with React.setState); the original is
// untouched. A no-op rename (same key) or an unknown oldKey returns the input unchanged.

/** Rename one key of a flat record while preserving insertion order. */
export function renameKey<T>(obj: Record<string, T>, oldKey: string, newKey: string): Record<string, T> {
  if (oldKey === newKey || !(oldKey in obj)) return obj
  const out: Record<string, T> = {}
  for (const k of Object.keys(obj)) {
    out[k === oldKey ? newKey : k] = obj[k]
  }
  return out
}

/** Validate a proposed rename against the local dict's keys. Returns an error-key for i18n
 *  (callers translate it) or ``null`` when the rename is acceptable. Empty / whitespace-only
 *  new key → ``'empty'``; collision with an existing different key → ``'exists'``; same-as-old
 *  → ``'unchanged'`` (treated as a soft no-op by callers).
 *
 *  The set of taken keys is the FULL set including the old one — callers pass in
 *  ``Object.keys(theDict)``. We special-case the old==new check above and only fail on a
 *  *different* existing key. */
export function validateRename(
  oldKey: string,
  newKey: string,
  takenKeys: Iterable<string>,
): 'empty' | 'unchanged' | 'exists' | null {
  const trimmed = newKey.trim()
  if (trimmed === '') return 'empty'
  if (trimmed === oldKey) return 'unchanged'
  for (const k of takenKeys) {
    if (k === trimmed && k !== oldKey) return 'exists'
  }
  return null
}
