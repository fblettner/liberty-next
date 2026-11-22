// Shared ID validator used by every Settings editor's Add / Rename / Clone prompt.
//
// Two layers of check:
//   * hard `error` — blocks submit. Pattern violation OR same-kind-same-scope duplicate.
//   * soft `warning` — allows submit but flags the operator. Cross-kind dupes (lookup id
//     `1` + enum id `1` in same scope — runtime resolves correctly via the entry's
//     ``rules`` field, but operator may confuse them) and numeric-id v1 migration hints.
//
// All validation is client-side over already-loaded state — no server round-trip. The
// backend rename endpoints still enforce uniqueness as a safety net for concurrent edits.

/** The naming convention per kind. UPPER_SNAKE for dict entries / enums (matches v1's
 *  dd_id / enum convention), lowercase snake_case for everything else. */
export const ID_PATTERNS: Record<string, RegExp> = {
  // dictionary
  entries: /^[A-Z_][A-Z0-9_]*$/,
  enums: /^[A-Z_][A-Z0-9_]*$/,
  // numeric ids accepted (v1 migration produces them) but flagged as warning below.
  lookups: /^([a-zA-Z_][a-zA-Z0-9_]*|\d+)$/,
  sequences: /^([a-zA-Z_][a-zA-Z0-9_]*|\d+)$/,
  framework_enums: /^[A-Z_][A-Z0-9_]*$/,
  // connectors / queries / screens / pools / etc.
  connector: /^[a-z_][a-z0-9_]*$/,
  pool: /^[a-z_][a-z0-9_]*$/,
  query: /^[a-z_][a-z0-9_]*$/,
  screen: /^[a-z_][a-z0-9_]*$/,
  menu_item: /^[a-z_][a-z0-9_]*$/,
  chart: /^[a-z_][a-z0-9_]*$/,
  dashboard: /^[a-z_][a-z0-9_]*$/,
}

/** Operator-facing hint shown in the prompt's placeholder / error text. */
export const ID_HINTS: Record<string, string> = {
  entries: 'UPPER_SNAKE_CASE (e.g. USR_ID)',
  enums: 'UPPER_SNAKE_CASE (e.g. STATUS)',
  lookups: 'snake_case (e.g. user_lookup) — numeric ids accepted but discouraged',
  sequences: 'snake_case (e.g. user_id_seq) — numeric ids accepted but discouraged',
  framework_enums: 'UPPER_SNAKE_CASE — overrides a bundled framework enum id',
  connector: 'snake_case (e.g. nomajde, ais_connection)',
  pool: 'snake_case (e.g. default, nomajde_prod)',
  query: 'snake_case (e.g. users_get, customer_aging)',
  screen: 'snake_case (e.g. security_users)',
  menu_item: 'snake_case (e.g. users, accounting_folder)',
  chart: 'snake_case (e.g. users_by_app)',
  dashboard: 'snake_case (e.g. nomaflow_overview)',
}

export interface ValidateIdOptions {
  /** Which kind this id belongs to — used to look up the pattern + hint. Use the SAME
   *  key as ID_PATTERNS (e.g. 'entries', 'lookups', 'screen'). */
  kind: string
  /** The proposed id (typically the user's input, pre-trim). */
  proposed: string
  /** Existing ids in the SAME kind + scope, EXCLUDING the current one for rename / clone. */
  existing: string[]
  /** Optional: other-kind ids in the same scope, for cross-kind warnings. Keys are kind
   *  names, values are the id lists. The validator skips ``opts.kind`` itself if present. */
  crossKindIds?: Record<string, string[]>
  /** add | rename | clone — affects message wording. Defaults to 'add'. */
  mode?: 'add' | 'rename' | 'clone'
  /** When true (rename / clone), the proposed id may equal the old one — treated as a
   *  no-op. The caller usually handles "unchanged" outside this function, but in case
   *  it slips through we don't fire a duplicate-error against the current name itself. */
  currentName?: string
}

export interface ValidateIdResult {
  /** Hard error — submit must be blocked + message shown red. */
  error?: string
  /** Soft warning — submit is allowed + message shown amber. */
  warning?: string
}

/** Validate *proposed* under the rules for *kind*. Returns ``{}`` when fully valid. */
export function validateId(opts: ValidateIdOptions): ValidateIdResult {
  const v = (opts.proposed ?? '').trim()
  if (!v) return { error: 'required' }
  if (opts.currentName && v === opts.currentName) return {}      // no-op rename — caller handles

  // Pattern check.
  const pattern = ID_PATTERNS[opts.kind]
  if (pattern && !pattern.test(v)) {
    const hint = ID_HINTS[opts.kind] ?? 'invalid characters'
    // said as what to type, not as a rule: no space, no accent, no punctuation
    const letters = pattern.test('A') && !pattern.test('a') ? 'capital letters' : pattern.test('a') && !pattern.test('A') ? 'small letters' : 'letters'
    return { error: `Use ${letters}, digits and _ only, without space or accent — ${hint}` }
  }

  // Duplicate within same kind+scope — hard error.
  if (opts.existing.includes(v)) {
    return { error: `"${v}" already exists in this scope — pick another name (or use Rename on the existing one)` }
  }

  // Cross-kind warnings (dictionary kinds — lookup vs enum vs sequence in the same scope).
  const warnings: string[] = []
  if (opts.crossKindIds) {
    for (const [otherKind, ids] of Object.entries(opts.crossKindIds)) {
      if (otherKind === opts.kind) continue
      if (ids.includes(v)) warnings.push(`also used as a ${otherKind.replace(/s$/, '')} in this scope (runtime works — the entry's "rules" field picks the right one, but it's confusing)`)
    }
  }

  // Numeric-id flag — v1 migration produces these for lookups / sequences; encourage cleanup.
  if (/^\d+$/.test(v) && (opts.kind === 'lookups' || opts.kind === 'sequences')) {
    warnings.push(`numeric ids are a v1 migration artefact — a descriptive name is easier to recognise in screens / Find usages`)
  }

  return warnings.length > 0 ? { warning: warnings.join(' · ') } : {}
}

/** A technical id made from a label a person typed: small letters, accents dropped, anything
 *  else turned into ``_`` — "Customer orders (late)" → ``customer_orders_late``. A label that
 *  starts with a digit gets a leading ``_`` (an id starts with a letter). */
export function idFromLabel(label: string): string {
  const base = label.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
  return /^[0-9]/.test(base) ? `_${base}` : base
}

/** *id* made free among *existing*: ``orders`` → ``orders_2`` → ``orders_3``… */
export function freeId(id: string, existing: string[]): string {
  if (!existing.includes(id)) return id
  for (let n = 2; ; n++) if (!existing.includes(`${id}_${n}`)) return `${id}_${n}`
}

/** What a "new object" window gives back when the person types its NAME: the label as typed and
 *  the id made from it, free among *existing*; null when the name has nothing an id can be made of. */
export function newObjectFromLabel(label: string, existing: string[]): { id: string; label: string } | null {
  const id = idFromLabel(label)
  return id ? { id: freeId(id, existing), label: label.trim() } : null
}

/** Suggest a unique id for a Clone operation. Appends ``_copy`` / ``_copy2`` / ``_copy3``
 *  until the result is free in *existing*. */
export function suggestCloneId(originalId: string, existing: string[]): string {
  const taken = new Set(existing)
  let candidate = `${originalId}_copy`
  let i = 2
  while (taken.has(candidate)) {
    candidate = `${originalId}_copy${i}`
    i += 1
  }
  return candidate
}
