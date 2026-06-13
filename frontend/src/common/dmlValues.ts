// Parse an Oracle LogMiner-style DML statement (INSERT / UPDATE / DELETE redo) into field-level
// BEFORE / AFTER values, in flight on the client. A faithful port of nomasx1's Python
// `_parse_sql_to_values` (plugins/nomasx1/audit_trail.py) — keep the two in sync. Generic SQL,
// not JDE-specific. Used to expand an audit statement row to "what changed" without a separate
// values table (so that table can be purged and this still works from the stored statement).

/** Undo Oracle's '' single-quote escaping. */
function unescapeSqlStr(v: string): string {
  return v.replace(/''/g, "'")
}

/** Normalise one INSERT VALUES token: 'string' → string (unescaped), NULL → '', a number /
 *  function expression (TO_DATE(…), 5, …) → verbatim. */
function unquoteSql(token: string): string {
  const t = token.trim()
  if (t.toUpperCase() === 'NULL') return ''
  if (t.length >= 2 && t[0] === "'" && t[t.length - 1] === "'") return unescapeSqlStr(t.slice(1, -1))
  return t
}

/** Split on top-level commas only — commas inside single-quoted strings ('' escapes) or balanced
 *  parens are left intact (so TO_DATE('a','b') / 'Smith, John' aren't shredded). */
function splitTopLevel(s: string): string[] {
  const out: string[] = []
  let buf = ''
  let depth = 0
  let inStr = false
  for (let i = 0; i < s.length; i++) {
    const ch = s[i]
    if (inStr) {
      buf += ch
      if (ch === "'") {
        if (s[i + 1] === "'") { buf += s[i + 1]; i++; continue }
        inStr = false
      }
      continue
    }
    if (ch === "'") { inStr = true; buf += ch; continue }
    if (ch === '(') { depth++; buf += ch; continue }
    if (ch === ')') { depth--; buf += ch; continue }
    if (ch === ',' && depth === 0) { out.push(buf); buf = ''; continue }
    buf += ch
  }
  if (buf) out.push(buf)
  return out.map((t) => t.trim())
}

/** Content between the '(' at openIdx and its matching ')' (respecting strings + nesting), plus
 *  the index just past the close. */
function parenGroup(s: string, openIdx: number): [string, number] {
  let depth = 0
  let inStr = false
  const start = openIdx + 1
  for (let i = openIdx; i < s.length; i++) {
    const ch = s[i]
    if (inStr) {
      if (ch === "'") { if (s[i + 1] === "'") { i++; continue } inStr = false }
      continue
    }
    if (ch === "'") inStr = true
    else if (ch === '(') depth++
    else if (ch === ')') { depth--; if (depth === 0) return [s.slice(start, i), i + 1] }
  }
  return [s.slice(start), s.length]
}

/** `INSERT INTO "S"."T"(cols) VALUES (vals)` → [[col, value], …] positionally. */
function parseInsertPairs(sql: string): [string, string][] {
  const open1 = sql.indexOf('(')
  if (open1 === -1) return []
  const [colSrc, after] = parenGroup(sql, open1)
  const vm = sql.slice(after).search(/\bVALUES\b/i)
  if (vm === -1) return []
  const open2 = sql.indexOf('(', after + vm)
  if (open2 === -1) return []
  const [valSrc] = parenGroup(sql, open2)
  const cols = splitTopLevel(colSrc).map((c) => c.trim().replace(/^"|"$/g, ''))
  const vals = splitTopLevel(valSrc)
  const out: [string, string][] = []
  for (let i = 0; i < Math.min(cols.length, vals.length); i++) out.push([cols[i], unquoteSql(vals[i])])
  return out
}

/** Split an UPDATE redo into [SET clause, WHERE clause] on the first TOP-LEVEL ` WHERE `. */
function splitSetWhere(sql: string): [string, string] | null {
  let depth = 0
  let inStr = false
  for (let i = 0; i < sql.length; i++) {
    const ch = sql[i]
    if (inStr) {
      if (ch === "'") { if (sql[i + 1] === "'") { i++; continue } inStr = false }
      continue
    }
    if (ch === "'") { inStr = true; continue }
    if (ch === '(') { depth++; continue }
    if (ch === ')') { depth--; continue }
    if (depth === 0 && sql.slice(i, i + 7).toUpperCase() === ' WHERE ') return [sql.slice(0, i), sql.slice(i + 7)]
  }
  return null
}

const FIELD_VALUE = /"(\w+)"\s*=\s*'((?:[^']|'')*)'/g

function fieldValuePairs(clause: string): [string, string][] {
  const out: [string, string][] = []
  for (const m of clause.matchAll(FIELD_VALUE)) out.push([m[1], unescapeSqlStr(m[2])])
  return out
}

export interface ValueRow { name: string; before: string | null; after: string | null }

/** Parse *sql* (a DML redo) into per-column BEFORE/AFTER rows, in first-seen column order.
 *  *operation* (INSERT/UPDATE/DELETE) is used when given, else inferred from the statement. */
export function parseDmlValues(sql: string, operation?: string | null): ValueRow[] {
  if (!sql) return []
  const op = (operation || sql.trim().split(/\s+/, 1)[0] || '').toUpperCase()
  const order: string[] = []
  const rows = new Map<string, ValueRow>()
  const set = (name: string, side: 'before' | 'after', value: string) => {
    let r = rows.get(name)
    if (!r) { r = { name, before: null, after: null }; rows.set(name, r); order.push(name) }
    r[side] = value
  }
  if (op === 'INSERT') {
    for (const [name, value] of parseInsertPairs(sql)) set(name, 'after', value)
  } else if (op === 'DELETE') {
    for (const [name, value] of fieldValuePairs(sql)) set(name, 'before', value)
  } else if (op === 'UPDATE') {
    const parts = splitSetWhere(sql)
    if (parts) {
      for (const [name, value] of fieldValuePairs(parts[0])) set(name, 'after', value)
      for (const [name, value] of fieldValuePairs(parts[1])) set(name, 'before', value)
    }
  }
  return order.map((n) => rows.get(n)!)
}
