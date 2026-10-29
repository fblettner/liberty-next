// The SQL Wizard's pure logic (no React): the WHERE / ORDER / JOIN shapes, the alias suggestion, the
// statement-type sniffing, the SELECT generator and the parser that reads an existing SELECT back into
// the builder (tables, joins, columns, WHERE, ORDER BY). Moved from common/SqlWizardModal.tsx, which
// re-exports what it exported before.
import type { PoolColumn, PoolTable } from './poolSchema'


// One row in the WHERE list. We keep `op` as a literal string so it serializes cleanly into
// generated SQL; `IS NULL` / `IS NOT NULL` have no value.
export const WHERE_OPS = ['=', '<>', '<', '>', '<=', '>=', 'LIKE', 'IN', 'IS NULL', 'IS NOT NULL'] as const
export type WhereOp = (typeof WHERE_OPS)[number]
export interface WhereRow { col: string; op: WhereOp; value: string }

export interface OrderRow { col: string; dir: 'ASC' | 'DESC' }

export const JOIN_TYPES = ['INNER', 'LEFT', 'RIGHT'] as const
export type JoinType = (typeof JOIN_TYPES)[number]
// One equi-join condition: ``<left> = <right>``, each an ``alias.col`` id (left from a preceding
// table, right from this table). A list of these AND's together — JDE keys are often composite.
export interface JoinCond { left: string; right: string }
// One table in the FROM/JOIN chain. ``join`` is undefined for the base table (the FROM); the rest
// carry a join type + the join condition. The condition is normally a list of equi-join pairs built
// from the source/target dropdowns; ``raw`` holds a free-text ON instead (set when an existing query
// uses a condition the pair model can't express — ``TRIM(x) IS NULL``, a function predicate, …).
export interface JoinTable {
  schema: string | null
  name: string
  alias: string
  columns: PoolColumn[]
  join?: { type: JoinType; conds: JoinCond[]; raw?: string }
}
// A unique, stable alias for a table — the table's own name (uppercased), so a join reads clearly
// (``F0004.DTSY``) instead of a cryptic truncation. A self-join's second copy gets a numeric suffix.
export function suggestAlias(name: string, taken: Set<string>): string {
  const base = (name.replace(/[^A-Za-z0-9]/g, '') || 't').toUpperCase()
  let a = base, n = 2
  while (taken.has(a.toUpperCase())) { a = `${base}${n}`; n += 1 }
  return a
}

/** The kind of statement the wizard builds — a CRUD slot's tab (read→SELECT, update→UPDATE,
 *  insert→INSERT, delete→DELETE). SELECT uses the full column/where/order/join builder; the others
 *  reuse the shared CRUD generator (common/sqlBuild.ts). */
export type WizardStatementType = 'SELECT' | 'INSERT' | 'UPDATE' | 'DELETE'
export const STMT_TO_CRUD: Record<Exclude<WizardStatementType, 'SELECT'>, 'post' | 'put' | 'delete'> = {
  INSERT: 'post', UPDATE: 'put', DELETE: 'delete',
}

/** The leading keyword of a statement (or 'OTHER' for CTE/MERGE/… the builder won't touch, null when
 *  empty). Drives the read-only guard: a slot whose SQL doesn't lead with its expected keyword opens
 *  read-only instead of being rebuilt into the wrong shape. */
export function firstSqlKeyword(sql: string): WizardStatementType | 'OTHER' | null {
  const m = sql.trim().match(/^([A-Za-z]+)/)
  if (!m) return null
  const kw = m[1].toUpperCase()
  return (kw === 'SELECT' || kw === 'INSERT' || kw === 'UPDATE' || kw === 'DELETE') ? kw : 'OTHER'
}

export function isNullary(op: WhereOp): boolean {
  return op === 'IS NULL' || op === 'IS NOT NULL'
}

/** SQL fragment for one WHERE row. Empty value on a non-nullary op → drop it (the row hasn't been
 *  filled in yet; the operator can leave it half-built without breaking the preview). Values are
 *  quoted as strings; numbers can be typed unquoted on a value the operator owns (we don't try
 *  to infer types). `IN` accepts a comma-separated list. */
export function whereFragment(w: WhereRow): string | null {
  if (!w.col) return null
  if (isNullary(w.op)) return `${w.col} ${w.op}`
  const v = w.value.trim()
  if (!v) return null
  if (w.op === 'IN') {
    const parts = v.split(',').map((p) => p.trim()).filter(Boolean).map(quoteIfNonNumeric)
    if (!parts.length) return null
    return `${w.col} IN (${parts.join(', ')})`
  }
  return `${w.col} ${w.op} ${quoteIfNonNumeric(v)}`
}

export function quoteIfNonNumeric(v: string): string {
  if (/^-?\d+(\.\d+)?$/.test(v)) return v               // bare number
  if (/^:[A-Za-z_]\w*$/.test(v)) return v               // a `:bind` reference — keep as-is (operator's intent)
  return `'${v.replace(/'/g, "''")}'`                  // SQL-quoted, single-quote-escaped
}

export function generateSql(
  joins: JoinTable[], cols: Set<string>, wheres: WhereRow[], orders: OrderRow[],
): string {
  if (joins.length === 0 || !joins[0].name || cols.size === 0) return ''
  const single = joins.length === 1
  // Column ids are ``<alias>.<col>`` internally; emit them bare for a single table (matches the
  // migrator's output) and alias-qualified once a join is involved (so duplicate names disambiguate).
  // Upper-case the identifier by convention — DB introspection returns columns in whatever case the
  // pool folds to (lower on Postgres, upper on Oracle), and a query should read uniformly regardless.
  const colName = (id: string) => (single ? (id.split('.').slice(1).join('.') || id) : id).toUpperCase()
  // Preserve table-then-column order so the SELECT reads predictably.
  const selected: string[] = []
  for (const j of joins) for (const c of j.columns) { const id = `${j.alias}.${c.name}`; if (cols.has(id)) selected.push(colName(id)) }
  if (selected.length === 0) return ''
  const tref = (j: JoinTable) => (j.schema ? `${j.schema}.${j.name}` : j.name) + (single ? '' : ` ${j.alias}`)
  let sql = `SELECT\n    ${selected.join(',\n    ')}\nFROM\n    ${tref(joins[0])}`
  for (let i = 1; i < joins.length; i++) {
    const j = joins[i]
    // A raw ON expression wins; otherwise build equi-join conditions AND'd together (ids upper-cased
    // to match the column convention).
    const on = (j.join?.raw ?? '').trim()
      ? (j.join?.raw ?? '').trim()
      : (j.join?.conds ?? [])
          .filter((c) => c.left && c.right)
          .map((c) => `${c.left.toUpperCase()} = ${c.right.toUpperCase()}`)
          .join(' AND ')
    sql += `\n${j.join?.type ?? 'INNER'} JOIN ${tref(j)}${on ? ` ON ${on}` : ''}`
  }
  const wf = wheres.map((w) => whereFragment({ ...w, col: colName(w.col) })).filter((x): x is string => x !== null)
  if (wf.length) sql += `\nWHERE\n    ${wf.join('\n    AND ')}`
  const ob = orders.filter((o) => o.col).map((o) => `${colName(o.col)} ${o.dir}`)
  if (ob.length) sql += `\nORDER BY\n    ${ob.join(', ')}`
  return sql
}


/** Best-effort parser for the simple ``SELECT <cols> FROM <table> [WHERE …] [ORDER BY …]``
 *  shape the migrator emits. When the parse succeeds, pre-fills the wizard's widgets so
 *  opening on an existing query reflects what's already there instead of starting with
 *  "all columns selected, no WHERE, no ORDER BY". Returns ``null`` when the SQL is too
 *  complex (joins, set ops, subqueries in the SELECT list, …) — the wizard then falls
 *  back to the all-columns default. Conservatively narrow on purpose: a partial parse
 *  that *looks* right would silently drop information.
 *
 *  Recognised pieces:
 *  - SELECT list: bare ``COL`` entries separated by commas at depth 0; aliases (``COL AS A``
 *    or ``COL A``) and expressions (``f(COL)``, ``a + b``) cause an abort. The migrator
 *    only ever emits bare column names.
 *  - WHERE clause: ``COL <op> <value>`` per AND-separated row. ``<value>`` is a string
 *    literal (``'foo'``), a number, or a ``:bind`` ref — all three round-trip through
 *    ``whereFragment``. OR-separated clauses fall back to no-WHERE.
 *  - ORDER BY: ``COL [ASC|DESC]`` comma-separated. Default direction ``ASC``.
 *
 *  Only fires when the target *table* is known (so the parsed columns can be validated
 *  against its schema — typos / aliases are caught and the parse aborts).
 */
export function parseExistingSelect(
  sql: string, table: PoolTable | undefined,
): { cols: Set<string>; wheres: WhereRow[]; orders: OrderRow[] } | null {
  if (!sql.trim() || !table) return null
  // Find the top-level SELECT / FROM / WHERE / ORDER BY at paren-depth 0.
  const upper = sql.toUpperCase()
  const tokens = locateTopLevelTokens(sql, upper, ['SELECT', 'FROM', 'WHERE', 'ORDER BY'])
  const sel = tokens.SELECT
  const fr = tokens.FROM
  if (sel === -1 || fr === -1 || sel >= fr) return null
  // Anything past FROM that *isn't* WHERE / ORDER BY also stops a clean parse — a JOIN /
  // UNION / GROUP BY would yield wrong column metadata against the picked single table.
  const wh = tokens.WHERE
  const ob = tokens['ORDER BY']
  const fromEnd = Math.min(...[wh, ob, sql.length].filter((x) => x > fr))
  const fromText = sql.slice(fr + 'FROM'.length, fromEnd).trim()
  // FROM must be exactly the picked table (qualified or not). Detect joins by looking for
  // ``JOIN`` or commas inside the FROM clause at depth 0.
  if (/[,]|\bJOIN\b/i.test(fromText)) return null
  const colsText = sql.slice(sel + 'SELECT'.length, fr).trim().replace(/^DISTINCT\s+/i, '')
  // ``SELECT *`` → all columns selected; otherwise split on top-level commas.
  let parsedCols: Set<string>
  if (colsText === '*') {
    parsedCols = new Set(table.columns.map((c) => c.name))
  } else {
    const colSpec = splitTopLevel(colsText, ',')
    const wanted: string[] = []
    const known = new Map(table.columns.map((c) => [c.name.toLowerCase(), c.name]))
    for (const raw of colSpec) {
      const tok = raw.trim()
      // Only accept bare identifiers (with optional table qualifier). Aliases / expressions
      // → abort the parse rather than misrepresent the query.
      const m = tok.match(/^([A-Za-z_][A-Za-z_0-9$]*)(?:\.([A-Za-z_][A-Za-z_0-9$]*))?$/)
      if (!m) return null
      const colName = m[2] ?? m[1]
      const actual = known.get(colName.toLowerCase())
      if (!actual) return null   // unknown column on this table — bail
      wanted.push(actual)
    }
    parsedCols = new Set(wanted)
  }
  // WHERE — AND-separated bare ``col op value`` rows. The migrated migrator doesn't emit
  // operator-side WHEREs (those are applied at runtime); a hand-written one might.
  const wheres: WhereRow[] = []
  if (wh !== -1) {
    const whereEnd = ob !== -1 && ob > wh ? ob : sql.length
    const whereText = sql.slice(wh + 'WHERE'.length, whereEnd).trim()
    // Bail on OR — the wizard's WHERE editor only supports AND.
    if (/\bOR\b/i.test(whereText)) return null
    const parts = whereText.split(/\bAND\b/i).map((p) => p.trim()).filter(Boolean)
    for (const part of parts) {
      const parsed = parseWhereClause(part, table)
      if (!parsed) return null
      wheres.push(parsed)
    }
  }
  // ORDER BY — bare comma-separated ``col [ASC|DESC]``.
  const orders: OrderRow[] = []
  if (ob !== -1) {
    const obText = sql.slice(ob + 'ORDER BY'.length).trim()
    const parts = splitTopLevel(obText, ',')
    const known = new Map(table.columns.map((c) => [c.name.toLowerCase(), c.name]))
    for (const raw of parts) {
      const m = raw.trim().match(/^([A-Za-z_][A-Za-z_0-9$]*)(?:\s+(ASC|DESC))?$/i)
      if (!m) return null
      const actual = known.get(m[1].toLowerCase())
      if (!actual) return null
      orders.push({ col: actual, dir: (m[2]?.toUpperCase() === 'DESC' ? 'DESC' : 'ASC') })
    }
  }
  return { cols: parsedCols, wheres, orders }
}

/** Find the first occurrence of each keyword *at paren-depth 0 outside string literals*.
 *  Returns ``{<keyword>: index}`` with ``-1`` for missing keywords. */
export function locateTopLevelTokens(sql: string, upper: string, keywords: string[]): Record<string, number> {
  const out: Record<string, number> = Object.fromEntries(keywords.map((k) => [k, -1]))
  let depth = 0
  let inSingle = false
  let inDouble = false
  for (let i = 0; i < sql.length; i++) {
    const c = sql[i]
    if (inSingle) { if (c === "'" && sql[i - 1] !== '\\') inSingle = false; continue }
    if (inDouble) { if (c === '"' && sql[i - 1] !== '\\') inDouble = false; continue }
    if (c === "'") { inSingle = true; continue }
    if (c === '"') { inDouble = true; continue }
    if (c === '(') { depth++; continue }
    if (c === ')') { depth--; continue }
    if (depth !== 0) continue
    for (const k of keywords) {
      if (out[k] !== -1) continue
      if (upper.startsWith(k, i)
        && (i === 0 || !/[A-Z_]/.test(upper[i - 1]))
        && (i + k.length >= upper.length || !/[A-Z_]/.test(upper[i + k.length]))) {
        out[k] = i
      }
    }
  }
  return out
}

/** Split *text* on *sep* at top-level (depth-0, outside string literals). */
export function splitTopLevel(text: string, sep: string): string[] {
  const out: string[] = []
  let depth = 0
  let inSingle = false
  let inDouble = false
  let start = 0
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inSingle) { if (c === "'" && text[i - 1] !== '\\') inSingle = false; continue }
    if (inDouble) { if (c === '"' && text[i - 1] !== '\\') inDouble = false; continue }
    if (c === "'") { inSingle = true; continue }
    if (c === '"') { inDouble = true; continue }
    if (c === '(') { depth++; continue }
    if (c === ')') { depth--; continue }
    if (depth === 0 && c === sep) {
      out.push(text.slice(start, i))
      start = i + 1
    }
  }
  out.push(text.slice(start))
  return out
}

export const _WHERE_OPS_SORTED = [...WHERE_OPS].sort((a, b) => b.length - a.length)  // longest first so `<=` beats `<`

/** Parse a single ``col op value`` (or ``col IS [NOT] NULL``, or ``col IN (...)``) clause
 *  against *table*'s schema. Returns ``null`` for shapes the wizard's WHERE editor can't
 *  faithfully represent. */
export function parseWhereClause(part: string, table: PoolTable): WhereRow | null {
  const known = new Map(table.columns.map((c) => [c.name.toLowerCase(), c.name]))
  // IS NULL / IS NOT NULL — nullary.
  let m = part.match(/^([A-Za-z_][A-Za-z_0-9$]*)\s+IS\s+(NOT\s+)?NULL$/i)
  if (m) {
    const actual = known.get(m[1].toLowerCase())
    if (!actual) return null
    return { col: actual, op: m[2] ? 'IS NOT NULL' : 'IS NULL', value: '' }
  }
  // IN (...) — list of literals / numbers / binds. Pull whatever's between the parens.
  m = part.match(/^([A-Za-z_][A-Za-z_0-9$]*)\s+IN\s*\((.*)\)$/i)
  if (m) {
    const actual = known.get(m[1].toLowerCase())
    if (!actual) return null
    const items = splitTopLevel(m[2], ',').map((s) => stripValueLiteral(s.trim())).filter((s) => s !== null)
    return { col: actual, op: 'IN', value: items.join(', ') }
  }
  // Generic ``col <op> <value>``. Match the longest operator first so ``<=`` doesn't
  // accidentally match ``<`` first.
  for (const op of _WHERE_OPS_SORTED) {
    if (op === 'IS NULL' || op === 'IS NOT NULL' || op === 'IN') continue
    const idx = part.search(new RegExp(`(\\s|^)${op.replace(/[<>=]/g, '\\$&')}(\\s|$)`, 'i'))
    if (idx === -1) continue
    const left = part.slice(0, idx).trim()
    const right = part.slice(idx + op.length + 1).trim()
    const colMatch = left.match(/^([A-Za-z_][A-Za-z_0-9$]*)$/)
    if (!colMatch) continue
    const actual = known.get(colMatch[1].toLowerCase())
    if (!actual) continue
    const value = stripValueLiteral(right)
    if (value === null) continue
    return { col: actual, op: op as WhereOp, value }
  }
  return null
}

/** Convert a SQL value literal back to its wizard form (the inverse of
 *  :func:`quoteIfNonNumeric`). Single-quoted strings → bare text (with ``''`` unescaped);
 *  numbers and ``:bind`` refs pass through. Anything else (function calls, expressions)
 *  → ``null`` to abort the parse. */
export function stripValueLiteral(s: string): string | null {
  const t = s.trim()
  if (!t) return null
  if (t.startsWith("'") && t.endsWith("'")) {
    return t.slice(1, -1).replace(/''/g, "'")
  }
  if (/^-?\d+(\.\d+)?$/.test(t)) return t
  if (/^:[A-Za-z_]\w*$/.test(t)) return t
  return null
}

// ─── multi-table FROM/JOIN round-trip (for re-opening a saved join query) ───────────────────────

export interface ParsedTable { schema: string | null; name: string; alias: string; joinType?: JoinType; onText?: string }

/** Parse a SELECT's FROM/JOIN chain into an ordered table list (base first). Each entry keeps the
 *  schema EXPRESSION (a portable ``#SCHEMA.X#`` token preserved verbatim), the table name, the alias
 *  the query used (or the table name when none — JDE writes ON refs against the bare name), and for
 *  joins the type + the raw ON text. Returns ``null`` when the FROM isn't a plain table chain (comma
 *  joins, CROSS / FULL JOIN, a subquery in FROM, set ops) — the caller falls back to base-pick. */
export function parseTableChain(sql: string): ParsedTable[] | null {
  if (!sql.trim()) return null
  const upper = sql.toUpperCase()
  const tokens = locateTopLevelTokens(sql, upper, ['FROM', 'WHERE', 'ORDER BY', 'GROUP BY', 'HAVING'])
  const fr = tokens.FROM
  if (fr === -1) return null
  const ends = [tokens.WHERE, tokens['ORDER BY'], tokens['GROUP BY'], tokens.HAVING, sql.length].filter((x) => x > fr)
  const fromText = sql.slice(fr + 'FROM'.length, Math.min(...ends))
  const depths = parenDepthsOf(fromText)
  // Locate depth-0 JOIN keywords (with their type). A bare FROM (no JOIN) → a single base table.
  const joinRe = /\b(INNER|LEFT|RIGHT|FULL|CROSS)?\s*(?:OUTER\s+)?JOIN\b/gi
  const marks: { idx: number; end: number; type: JoinType }[] = []
  let m: RegExpExecArray | null
  while ((m = joinRe.exec(fromText)) !== null) {
    if ((depths[m.index] ?? 0) !== 0) continue
    const kw = (m[1] ?? 'INNER').toUpperCase()
    if (kw === 'FULL' || kw === 'CROSS') return null   // not a source/target join the builder models
    marks.push({ idx: m.index, end: m.index + m[0].length, type: kw === 'LEFT' ? 'LEFT' : kw === 'RIGHT' ? 'RIGHT' : 'INNER' })
  }
  const starts = [0, ...marks.map((x) => x.end)]
  const stops = [...marks.map((x) => x.idx), fromText.length]
  const out: ParsedTable[] = []
  for (let i = 0; i < starts.length; i++) {
    const seg = fromText.slice(starts[i], stops[i])
    if (i === 0) {
      const base = parseTableSpec(seg)
      if (!base) return null
      out.push(base)
    } else {
      const { before, after } = splitOnClause(seg)
      const tbl = parseTableSpec(before)
      if (!tbl) return null
      out.push({ ...tbl, joinType: marks[i - 1].type, onText: after.trim() })
    }
  }
  return out
}

/** Parse a single ``[schema.]table [AS] [alias]`` (or ``#SCHEMA.X#.table alias``) spec. Returns
 *  ``null`` for anything that isn't a plain table reference (a comma list, a subquery, junk). */
export function parseTableSpec(text: string): ParsedTable | null {
  const t = text.trim()
  if (!t) return null
  const tm = t.match(/^(#SCHEMA(?:\.[A-Za-z0-9_]+)?#\.[A-Za-z_]\w*|[A-Za-z_][\w$.]*)\s*([\s\S]*)$/i)
  if (!tm) return null
  const tok = tm[1]
  let schema: string | null = null
  let name: string
  const sm = tok.match(/^(#SCHEMA(?:\.[A-Za-z0-9_]+)?#)\.([A-Za-z_]\w*)$/i)
  if (sm) { schema = sm[1]; name = sm[2] }
  else { const parts = tok.split('.'); name = parts.pop() ?? tok; schema = parts.length ? parts.join('.') : null }
  let alias = name
  const rest = (tm[2] ?? '').replace(/^AS\s+/i, '').trim()
  if (rest) {
    const am = rest.match(/^([A-Za-z_][\w$]*)$/)
    if (!am) return null   // leftover tokens → not a plain table spec
    alias = am[1]
  }
  return { schema, name, alias }
}

/** Decompose an ON clause into oriented source/target equi-pairs against the built join chain. Falls
 *  back to ``{ conds: [], raw }`` (the original text) when any AND-part isn't a plain ``a.col = b.col``
 *  resolvable against the tables — e.g. ``TRIM(x) IS NULL`` — so a complex condition round-trips intact. */
export function parseOn(onText: string, thisAlias: string, joins: JoinTable[]): { conds: JoinCond[]; raw?: string } {
  const t = (onText ?? '').trim()
  if (!t) return { conds: [{ left: '', right: '' }] }
  const conds: JoinCond[] = []
  for (const part of splitTopLevelKeyword(t, 'AND')) {
    const eq = part.trim().match(/^([A-Za-z_][\w$]*\.[A-Za-z_][\w$]*)\s*=\s*([A-Za-z_][\w$]*\.[A-Za-z_][\w$]*)$/)
    if (!eq) return { conds: [], raw: t }
    const a = resolveColRef(eq[1], joins)
    const b = resolveColRef(eq[2], joins)
    if (!a || !b) return { conds: [], raw: t }
    const al = thisAlias.toLowerCase()
    if (eq[2].split('.')[0].toLowerCase() === al) conds.push({ left: a, right: b })
    else if (eq[1].split('.')[0].toLowerCase() === al) conds.push({ left: b, right: a })
    else return { conds: [], raw: t }   // neither side is this table → can't orient
  }
  return { conds }
}

/** Resolve a qualified ``alias.col`` reference to the canonical ``<alias>.<dbColumnName>`` id (the
 *  value the column dropdowns use), case-insensitively. ``null`` when the alias / column is unknown. */
export function resolveColRef(ref: string, joins: JoinTable[]): string | null {
  const parts = ref.split('.')
  if (parts.length !== 2) return null
  const j = joins.find((x) => x.alias.toLowerCase() === parts[0].toLowerCase())
  if (!j) return null
  const c = j.columns.find((x) => x.name.toLowerCase() === parts[1].toLowerCase())
  return c ? `${j.alias}.${c.name}` : null
}

/** Try to round-trip a multi-table SELECT's column list / WHERE / ORDER BY into the builder widgets.
 *  Returns ``null`` (→ read-only) when any piece isn't representable: an output-column alias, an
 *  expression column, a bare column that's ambiguous across the joined tables, DISTINCT, GROUP BY /
 *  HAVING, an OR'd WHERE, or a value the WHERE editor can't express. */
export function parseMultiSelect(
  sql: string, joins: JoinTable[],
): { cols: Set<string>; wheres: WhereRow[]; orders: OrderRow[] } | null {
  const upper = sql.toUpperCase()
  const tokens = locateTopLevelTokens(sql, upper, ['SELECT', 'FROM', 'WHERE', 'ORDER BY', 'GROUP BY', 'HAVING'])
  const sel = tokens.SELECT, fr = tokens.FROM
  if (sel === -1 || fr === -1 || sel >= fr) return null
  if (tokens['GROUP BY'] !== -1 || tokens.HAVING !== -1) return null   // aggregation — keep the SQL as-is
  // Resolve a column ref to its canonical id — qualified via the alias, bare only when unambiguous.
  const resolve = (ref: string): string | null => {
    if (ref.includes('.')) return resolveColRef(ref, joins)
    const hits = joins.filter((j) => j.columns.some((c) => c.name.toLowerCase() === ref.toLowerCase()))
    if (hits.length !== 1) return null
    const c = hits[0].columns.find((x) => x.name.toLowerCase() === ref.toLowerCase())
    return c ? `${hits[0].alias}.${c.name}` : null
  }
  // SELECT list.
  const colsText = sql.slice(sel + 'SELECT'.length, fr).trim()
  if (/^DISTINCT\b/i.test(colsText)) return null
  const cols = new Set<string>()
  if (colsText === '*') {
    for (const j of joins) for (const c of j.columns) cols.add(`${j.alias}.${c.name}`)
  } else {
    for (const raw of splitTopLevel(colsText, ',')) {
      const mm = raw.trim().match(/^([A-Za-z_][\w$]*)(?:\.([A-Za-z_][\w$]*))?$/)
      if (!mm) return null   // alias (``COL AS X`` / ``COL X``) or expression → not representable
      const id = resolve(mm[2] ? `${mm[1]}.${mm[2]}` : mm[1])
      if (!id) return null
      cols.add(id)
    }
  }
  // WHERE — AND-separated ``col op value`` rows; bail on OR.
  const wheres: WhereRow[] = []
  const wh = tokens.WHERE, ob = tokens['ORDER BY']
  if (wh !== -1) {
    const whereText = sql.slice(wh + 'WHERE'.length, ob !== -1 && ob > wh ? ob : sql.length).trim()
    if (/\bOR\b/i.test(whereText)) return null
    for (const part of splitTopLevelKeyword(whereText, 'AND')) {
      const parsed = parseWhereClauseMulti(part.trim(), resolve)
      if (!parsed) return null
      wheres.push(parsed)
    }
  }
  // ORDER BY — comma-separated ``col [ASC|DESC]``.
  const orders: OrderRow[] = []
  if (ob !== -1) {
    for (const raw of splitTopLevel(sql.slice(ob + 'ORDER BY'.length).trim(), ',')) {
      const mm = raw.trim().match(/^([A-Za-z_][\w$]*(?:\.[A-Za-z_][\w$]*)?)(?:\s+(ASC|DESC))?$/i)
      if (!mm) return null
      const id = resolve(mm[1])
      if (!id) return null
      orders.push({ col: id, dir: mm[2]?.toUpperCase() === 'DESC' ? 'DESC' : 'ASC' })
    }
  }
  return { cols, wheres, orders }
}

/** Multi-table variant of :func:`parseWhereClause` — accepts qualified column refs and resolves each
 *  to its canonical id via *resolve*. ``null`` for shapes the WHERE editor can't represent. */
export function parseWhereClauseMulti(part: string, resolve: (r: string) => string | null): WhereRow | null {
  const COL = '([A-Za-z_][\\w$]*(?:\\.[A-Za-z_][\\w$]*)?)'
  let m = part.match(new RegExp(`^${COL}\\s+IS\\s+(NOT\\s+)?NULL$`, 'i'))
  if (m) { const id = resolve(m[1]); return id ? { col: id, op: m[2] ? 'IS NOT NULL' : 'IS NULL', value: '' } : null }
  m = part.match(new RegExp(`^${COL}\\s+IN\\s*\\((.*)\\)$`, 'i'))
  if (m) {
    const id = resolve(m[1])
    if (!id) return null
    const items = splitTopLevel(m[2], ',').map((s) => stripValueLiteral(s.trim())).filter((s): s is string => s !== null)
    return { col: id, op: 'IN', value: items.join(', ') }
  }
  for (const op of _WHERE_OPS_SORTED) {
    if (op === 'IS NULL' || op === 'IS NOT NULL' || op === 'IN') continue
    const idx = part.search(new RegExp(`(\\s|^)${op.replace(/[<>=]/g, '\\$&')}(\\s|$)`, 'i'))
    if (idx === -1) continue
    const lm = part.slice(0, idx).trim().match(new RegExp(`^${COL}$`))
    if (!lm) continue
    const id = resolve(lm[1])
    if (!id) continue
    const value = stripValueLiteral(part.slice(idx + op.length + 1).trim())
    if (value === null) continue
    return { col: id, op: op as WhereOp, value }
  }
  return null
}

/** First depth-0 ``ON`` keyword → split a JOIN segment into its table spec + condition text. */
export function splitOnClause(text: string): { before: string; after: string } {
  const upper = text.toUpperCase()
  let depth = 0, inS = false, inD = false
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inS) { if (c === "'" && text[i - 1] !== '\\') inS = false; continue }
    if (inD) { if (c === '"' && text[i - 1] !== '\\') inD = false; continue }
    if (c === "'") { inS = true; continue }
    if (c === '"') { inD = true; continue }
    if (c === '(') { depth++; continue }
    if (c === ')') { depth--; continue }
    if (depth !== 0) continue
    if (upper.startsWith('ON', i)
      && (i === 0 || !/[A-Z0-9_]/.test(upper[i - 1]))
      && (i + 2 >= upper.length || !/[A-Z0-9_]/.test(upper[i + 2]))) {
      return { before: text.slice(0, i), after: text.slice(i + 2) }
    }
  }
  return { before: text, after: '' }
}

/** Split *text* on a depth-0, word-boundary keyword (``AND``), outside string literals. */
export function splitTopLevelKeyword(text: string, kw: string): string[] {
  const upper = text.toUpperCase()
  const out: string[] = []
  let depth = 0, inS = false, inD = false, start = 0
  for (let i = 0; i < text.length; i++) {
    const c = text[i]
    if (inS) { if (c === "'" && text[i - 1] !== '\\') inS = false; continue }
    if (inD) { if (c === '"' && text[i - 1] !== '\\') inD = false; continue }
    if (c === "'") { inS = true; continue }
    if (c === '"') { inD = true; continue }
    if (c === '(') { depth++; continue }
    if (c === ')') { depth--; continue }
    if (depth !== 0) continue
    if (upper.startsWith(kw, i)
      && (i === 0 || !/[A-Z0-9_]/.test(upper[i - 1]))
      && (i + kw.length >= upper.length || !/[A-Z0-9_]/.test(upper[i + kw.length]))) {
      out.push(text.slice(start, i)); start = i + kw.length; i += kw.length - 1
    }
  }
  out.push(text.slice(start))
  return out
}

/** Per-character paren depth (string-literal aware) for a fragment — used to skip JOINs in subqueries. */
export function parenDepthsOf(text: string): number[] {
  const out = new Array(text.length).fill(0)
  let depth = 0, inS = false, inD = false
  for (let i = 0; i < text.length; i++) {
    out[i] = depth
    const c = text[i]
    if (inS) { if (c === "'" && text[i - 1] !== '\\') inS = false; continue }
    if (inD) { if (c === '"' && text[i - 1] !== '\\') inD = false; continue }
    if (c === "'") { inS = true; continue }
    if (c === '"') { inD = true; continue }
    if (c === '(') { depth++; continue }
    if (c === ')') { depth--; continue }
  }
  return out
}
