// Schema-aware SQL completion for Monaco — powers the SqlEditor's table + column suggestions
// in the config builder. The completion provider is **registered once per Monaco instance**;
// SqlEditor instances attach their connector's schema to their model via `attachPoolSchema`,
// and the provider looks the model up in a WeakMap (so a closed editor's schema is GC'd).
//
// Context detection is pattern-based, not a real parser — three cases cover ~80% of operator
// typing without the cost of pulling in `pgsql-parser`:
//   1. After FROM / JOIN / INTO / UPDATE → table names.
//   2. After `<ident>.` → that table's columns (resolving aliases declared earlier in the
//      same statement via `FROM <table> [AS] <alias>` / `JOIN <table> [AS] <alias>`).
//   3. Inside a SELECT clause (between SELECT and FROM) → columns of any table named after
//      FROM in the same statement.
import type * as Monaco from 'monaco-editor/esm/vs/editor/editor.api'
import type { PoolSchema, PoolTable } from './poolSchema'
import { findTable } from './poolSchema'

const modelSchemas = new WeakMap<Monaco.editor.ITextModel, PoolSchema>()
let providerRegistered = false

/** Attach a connector's pool schema to a Monaco model so the completion provider can find it.
 *  Re-attaching for the same model is a no-op overwrite — the editor's own `onMount` handler
 *  calls this once per mount, and an editor re-mount creates a fresh model anyway. */
export function attachPoolSchema(
  monaco: typeof Monaco, model: Monaco.editor.ITextModel, schema: PoolSchema,
): void {
  modelSchemas.set(model, schema)
  ensureProvider(monaco)
}

function ensureProvider(monaco: typeof Monaco): void {
  if (providerRegistered) return
  providerRegistered = true
  monaco.languages.registerCompletionItemProvider('sql', {
    // Trigger on `.` (column-of-table) and whitespace (after a keyword); Monaco still calls
    // us on every Ctrl-Space too, so users get the full list without typing a trigger char.
    triggerCharacters: ['.', ' ', '\n'],
    provideCompletionItems(model, position) {
      const schema = modelSchemas.get(model)
      if (!schema) return { suggestions: [] }
      const textBefore = model.getValueInRange({
        startLineNumber: 1, startColumn: 1,
        endLineNumber: position.lineNumber, endColumn: position.column,
      })
      const word = model.getWordUntilPosition(position)
      const range: Monaco.IRange = {
        startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
        startColumn: word.startColumn, endColumn: word.endColumn,
      }
      const ctx = analyzeContext(textBefore)
      if (ctx === null) return { suggestions: [] }
      if (ctx.kind === 'table') {
        return { suggestions: schema.tables.map((t) => tableSuggestion(monaco, t, range)) }
      }
      if (ctx.kind === 'columnOf') {
        // Try the literal name first; fall back to resolving the alias from the statement.
        const aliasMap = parseAliases(textBefore)
        const tableName = aliasMap.get(ctx.target.toLowerCase()) ?? ctx.target
        const t = findTable(schema, tableName)
        if (!t) return { suggestions: [] }
        return { suggestions: t.columns.map((c) => columnSuggestion(monaco, c.name, c.type, range)) }
      }
      // 'columnAny' — inside a SELECT clause; suggest columns from every FROM/JOIN target in the
      // same statement, plus the table names themselves (a SELECT can reference table.col too).
      const fromTables = parseFromTables(textBefore, schema)
      const seenCols = new Set<string>()
      const suggestions: Monaco.languages.CompletionItem[] = []
      for (const t of fromTables) {
        for (const c of t.columns) {
          const key = c.name.toLowerCase()
          if (seenCols.has(key)) continue
          seenCols.add(key)
          suggestions.push(columnSuggestion(monaco, c.name, c.type, range))
        }
      }
      // Also offer table names so the user can write `users.id` from a fresh prompt
      for (const t of fromTables) suggestions.push(tableSuggestion(monaco, t, range))
      return { suggestions }
    },
  })
}

interface Context {
  kind: 'table' | 'columnOf' | 'columnAny'
  /** For `columnOf`, the identifier or alias before the dot. */
  target: string
}

function analyzeContext(textBefore: string): Context | null {
  // Cut off at the most recent statement separator so multi-statement scripts don't bleed context.
  const stmt = textBefore.split(';').pop() ?? textBefore
  // Strip the word currently being typed (so `FROM foo|` resolves to "table-position after FROM",
  // not "we're already inside the table name").
  const head = stmt.replace(/\w*$/, '').trimEnd()
  // 1. `<ident>.<cursor>` → columnOf <ident>. (No space between dot and cursor — Monaco's
  //    `replaceWordAtPosition` already stripped trailing word chars.)
  const dot = head.match(/(\w+)\.\s*$/)
  if (dot) return { kind: 'columnOf', target: dot[1], }
  // 2. Table-expecting keywords (FROM / JOIN / INTO / UPDATE) — case-insensitive.
  //    Match trailing `KEYWORD` or `KEYWORD …,` (subsequent table in a FROM list).
  if (/\b(?:FROM|JOIN|INTO|UPDATE|TABLE)\s*(?:,\s*)?$/i.test(head)) {
    return { kind: 'table', target: '' }
  }
  // 3. SELECT clause (after SELECT, before FROM in the same statement) — column position.
  if (/\bSELECT\b/i.test(stmt) && !/\bFROM\b/i.test(stmt.split(/\bSELECT\b/i).slice(-1)[0] ?? '')) {
    return { kind: 'columnAny', target: '' }
  }
  // Conservative default: no suggestions — Monaco's keyword highlighter still runs.
  return null
}

/** Collect `FROM <table> [AS] <alias>` and `JOIN <table> [AS] <alias>` aliases declared so far
 *  in the statement, so `WHERE u.|` resolves `u` → `users`. */
function parseAliases(textBefore: string): Map<string, string> {
  const m = new Map<string, string>()
  const stmt = textBefore.split(';').pop() ?? textBefore
  const re = /\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)\s+(?:AS\s+)?([A-Za-z_]\w*)\b/gi
  let match
  while ((match = re.exec(stmt)) !== null) {
    const table = (match[1] ?? '').split('.').pop() ?? match[1]
    const alias = match[2] ?? ''
    if (table && alias && !/^(WHERE|ON|JOIN|INNER|LEFT|RIGHT|OUTER|GROUP|ORDER|HAVING|LIMIT)$/i.test(alias)) {
      m.set(alias.toLowerCase(), table)
    }
  }
  return m
}

/** Return the tables named after FROM/JOIN in the current statement (best-effort — resolves via the
 *  pool schema's case-insensitive lookup). Used by the SELECT-clause completion path. */
function parseFromTables(textBefore: string, schema: PoolSchema): PoolTable[] {
  const stmt = textBefore.split(';').pop() ?? textBefore
  const re = /\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)/gi
  const out: PoolTable[] = []
  const seen = new Set<string>()
  let m
  while ((m = re.exec(stmt)) !== null) {
    const name = (m[1] ?? '').split('.').pop() ?? m[1]
    if (!name || seen.has(name.toLowerCase())) continue
    seen.add(name.toLowerCase())
    const t = findTable(schema, name)
    if (t) out.push(t)
  }
  return out
}

function tableSuggestion(
  monaco: typeof Monaco, t: PoolTable, range: Monaco.IRange,
): Monaco.languages.CompletionItem {
  const sch = t.schema ? `${t.schema}.` : ''
  return {
    label: { label: `${sch}${t.name}`, description: t.kind === 'view' ? 'view' : `${t.columns.length} col` },
    kind: t.kind === 'view'
      ? monaco.languages.CompletionItemKind.Interface
      : monaco.languages.CompletionItemKind.Struct,
    insertText: t.name,
    range,
    detail: t.columns.slice(0, 6).map((c) => c.name).join(', ') + (t.columns.length > 6 ? ', …' : ''),
    sortText: `0_${t.name}`, // tables before columns when both are offered
  }
}

function columnSuggestion(
  monaco: typeof Monaco, name: string, type: string | undefined, range: Monaco.IRange,
): Monaco.languages.CompletionItem {
  return {
    label: { label: name, description: type ?? '' },
    kind: monaco.languages.CompletionItemKind.Field,
    insertText: name,
    range,
    sortText: `1_${name}`,
  }
}
