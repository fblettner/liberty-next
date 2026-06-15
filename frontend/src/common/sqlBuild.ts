// The canonical CRUD-statement generator — shared by the "Generate table from DB" wizard
// (CrudWizardModal) and the per-slot SQL wizard (SqlWizardModal), so a slot you didn't generate at
// import time scaffolds the SAME statement. Pure string assembly: pick a table + the columns (and
// the key columns that drive UPDATE/DELETE WHERE) and out comes the SELECT / INSERT / UPDATE / DELETE.
// ``schema`` is the schema EXPRESSION (a portable ``#SCHEMA.X#`` token or a real owner) — preserved
// verbatim. Returns '' when the inputs can't make a meaningful statement (e.g. UPDATE with no key).

export type CrudStatementKind = 'get' | 'put' | 'post' | 'delete'

export function buildCrudSql(opts: {
  crud: CrudStatementKind
  schema: string | null
  table: string
  selectCols: string[]   // columns visible to SELECT / INSERT (the "include" set)
  keyCols: string[]      // columns identifying a row (drive UPDATE / DELETE WHERE)
}): string {
  const fqTable = opts.schema ? `${opts.schema}.${opts.table}` : opts.table
  if (!opts.table) return ''
  // Upper-case column identifiers + their bind names — same convention as the SELECT wizard
  // (generateSql): DB introspection folds case per dialect (lower on Postgres, upper on Oracle), so
  // a generated statement reads uniformly. Unquoted, so Postgres folds it back to lowercase anyway.
  const sel = opts.selectCols.map((c) => c.toUpperCase())
  const keys = opts.keyCols.map((c) => c.toUpperCase())
  if (opts.crud === 'get') {
    if (sel.length === 0) return ''
    return `SELECT\n  ${sel.join(',\n  ')}\nFROM ${fqTable}`
  }
  if (opts.crud === 'post') {
    if (sel.length === 0) return ''
    const placeholders = sel.map((c) => `:${c}`).join(',\n  ')
    return `INSERT INTO ${fqTable} (\n  ${sel.join(',\n  ')}\n) VALUES (\n  ${placeholders}\n)`
  }
  if (opts.crud === 'put') {
    if (keys.length === 0) return ''
    const nonKey = sel.filter((c) => !keys.includes(c))
    if (nonKey.length === 0) return ''
    const sets = nonKey.map((c) => `${c} = :${c}`).join(',\n  ')
    const where = keys.map((c) => `${c} = :${c}_ORIGINAL`).join('\n  AND ')
    return `UPDATE ${fqTable}\nSET\n  ${sets}\nWHERE\n  ${where}`
  }
  // delete
  if (keys.length === 0) return ''
  const where = keys.map((c) => `${c} = :${c}`).join('\n  AND ')
  return `DELETE FROM ${fqTable}\nWHERE\n  ${where}`
}
