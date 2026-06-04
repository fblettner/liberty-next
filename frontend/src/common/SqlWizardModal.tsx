// A modest SELECT-builder for the SqlEditor — a button on each editor opens this. The operator
// picks a table, ticks columns, adds optional WHERE rows + ORDER BY rows, sees a live preview
// of the generated SQL, and clicks Insert to replace the editor's contents.
//
// Deliberately scoped to SELECT only. INSERT/UPDATE/DELETE wizards balloon in complexity (column
// mapping, parameter binding, the `_put` `:_ORIGINAL` rewrite, the upsert split…) and most
// operators are tweaking SQL they already have, not writing from scratch. The editor's Monaco
// autocomplete covers the day-to-day case; this wizard is for starting a query from zero.
//
// NOT re-exported from `common/index.ts` — direct import only (keeps it in the Settings chunk).
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from './Button'
import { Checkbox } from './Checkbox'
import { Field, Input, PasswordInput as _pw } from './Input'  // PasswordInput unused — silence the re-export
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from './Modal'
import { SearchSelect, type SearchSelectOption } from './SearchSelect'
import { SqlEditor } from './SqlEditor'
import type { PoolSchema, PoolTable } from '../services/poolSchema'
import { colors, fontSize, fonts, radius } from '../theme'

// PasswordInput is imported only to keep the barrel re-export honest; silence eslint.
void _pw

// One row in the WHERE list. We keep `op` as a literal string so it serializes cleanly into
// generated SQL; `IS NULL` / `IS NOT NULL` have no value.
const WHERE_OPS = ['=', '<>', '<', '>', '<=', '>=', 'LIKE', 'IN', 'IS NULL', 'IS NOT NULL'] as const
type WhereOp = (typeof WHERE_OPS)[number]
interface WhereRow { col: string; op: WhereOp; value: string }

interface OrderRow { col: string; dir: 'ASC' | 'DESC' }

const Section = styled.div`
  display: flex; flex-direction: column; gap: 6px;
  font-size: ${fontSize.sm}; color: ${colors.text.secondary};
`
const SectionTitle = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em;`
const ColGrid = styled.div`
  display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 4px 12px; max-height: 200px; overflow-y: auto; padding: 6px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
`
const ColCheck = styled.div`
  display: inline-flex; align-items: center; gap: 6px; font-family: ${fonts.mono};
  font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & .type { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
`
const RowBar = styled.div`display: flex; gap: 6px; align-items: center;`
const SmallX = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px;
  border-radius: ${radius.sm}; border: 1px solid ${colors.border}; background: transparent;
  color: ${colors.text.muted}; cursor: pointer; flex-shrink: 0;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; }
`
const MiniBtn = styled.button`
  display: inline-flex; align-items: center; gap: 4px; height: 28px; padding: 0 10px; border-radius: ${radius.sm};
  border: 1px dashed ${colors.border}; background: transparent; color: ${colors.text.muted};
  font-size: ${fontSize.micro}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`

export interface SqlWizardModalProps {
  schema: PoolSchema
  initialTable?: string
  /** Existing SQL in the editor — the wizard tries to parse the simple single-table SELECT
   *  shape and pre-fills its column/WHERE/ORDER BY widgets so opening on a non-trivial query
   *  doesn't silently lose work. Unparseable SQL falls back to "all columns selected, no
   *  WHERE, no ORDER BY" so the wizard remains useful as a starting-from-scratch tool. */
  initialSql?: string
  onInsert: (sql: string) => void
  onCancel: () => void
}

export function SqlWizardModal({ schema, initialTable, initialSql, onInsert, onCancel }: SqlWizardModalProps) {
  const { t } = useTranslation()
  const tableOpts: SearchSelectOption[] = useMemo(
    () => schema.tables.map((t) => ({
      value: t.name,
      label: t.schema ? `${t.schema}.${t.name}` : t.name,
      // SearchSelect's renderRight is more code than we need; description lands inline.
    })),
    [schema],
  )
  const [tableName, setTableName] = useState<string>(() => initialTable ?? schema.tables[0]?.name ?? '')
  const table: PoolTable | undefined = useMemo(
    () => schema.tables.find((t) => t.name === tableName) ?? schema.tables.find((t) => t.name.toLowerCase() === tableName.toLowerCase()),
    [schema, tableName],
  )
  // Best-effort parse of *initialSql* — extract column list, WHEREs, ORDER BYs from a simple
  // single-table SELECT. Falls back to "all columns selected" if the parse fails (complex
  // joins, subqueries, set ops) so the wizard remains usable as a starting-from-scratch tool.
  // Runs once on mount (the initial-state body is only evaluated then); switching tables
  // afterwards via the dropdown picks the all-columns default for the new table.
  const initialParse = useMemo(() => parseExistingSelect(initialSql ?? '', table), [/* mount-only */])  // eslint-disable-line react-hooks/exhaustive-deps
  const [cols, setCols] = useState<Set<string>>(() => initialParse?.cols ?? new Set(table?.columns.map((c) => c.name) ?? []))
  const [wheres, setWheres] = useState<WhereRow[]>(() => initialParse?.wheres ?? [])
  const [orders, setOrders] = useState<OrderRow[]>(() => initialParse?.orders ?? [])
  // When the operator picks a *different* table via the dropdown, reset to that table's
  // all-columns default. ``initialParse`` only applies to the table the wizard opened with,
  // not to subsequent picks (the parsed columns wouldn't match the new table's schema).
  const initialTableRef = useMemo(() => tableName, [/* mount-only */])  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (tableName !== initialTableRef) {
      setCols(new Set(table?.columns.map((c) => c.name) ?? []))
      setWheres([])
      setOrders([])
    }
  }, [table, tableName, initialTableRef])

  const colOpts: SearchSelectOption[] = useMemo(
    () => (table?.columns ?? []).map((c) => ({ value: c.name, label: c.name })),
    [table],
  )
  // ``userTouched`` flips to true as soon as the operator changes a widget. Until then the
  // preview shows the *original* SQL verbatim (when ``initialSql`` is non-empty) so opening
  // the wizard on an existing query — especially a complex one the parser can't fully round-
  // trip (CASE expressions / subselects / JOINs) — doesn't silently overwrite a working
  // query with a regenerated default. The Insert button always sends what's in the preview:
  // either ``initialSql`` (untouched) or the regenerated ``generated`` (touched). The user
  // explicitly asked for this: "simply copy the current query when opening the wizard".
  const [userTouched, setUserTouched] = useState(false)
  const touch = () => { if (!userTouched) setUserTouched(true) }
  // Wrap each setter so its first call marks the wizard as touched. Local helpers keep the
  // JSX free of repetition.
  const onColsChange = (next: Set<string>) => { touch(); setCols(next) }
  const onWheresChange = (next: WhereRow[]) => { touch(); setWheres(next) }
  const onOrdersChange = (next: OrderRow[]) => { touch(); setOrders(next) }
  const onTableChange = (next: string) => { touch(); setTableName(next) }
  const generated = useMemo(() => generateSql(table, cols, wheres, orders), [table, cols, wheres, orders])
  // Preview = original SQL when untouched + non-empty + the parse recognised it, OR generated
  // otherwise. For unparseable complex SQL (JOINs / expressions), we still show the original
  // until the operator touches a widget, so just opening the wizard to *inspect* doesn't risk
  // anything. Brand-new (empty) queries always show the generated ``SELECT * FROM <table>``.
  const previewSql = !userTouched && (initialSql ?? '').trim() ? (initialSql as string) : generated
  // Insert: the preview is the source of truth — whatever the operator sees, that's what
  // lands in the editor. If they didn't touch anything, the original is kept (no-op insert);
  // if they did, the regenerated SQL replaces it.
  const insertSql = previewSql
  const canInsert = !!table && cols.size > 0

  // Portal the overlay to ``document.body`` so it escapes any ancestor with
  // ``backdrop-filter`` (the parent ConnectorsBuilder / RawEditor Modals have it). A
  // ``position: fixed`` element inside such an ancestor positions relative to that
  // ancestor instead of the viewport — the wizard would appear offset inside the
  // editor's frame instead of centered on the screen.
  // No backdrop-click-to-close — outside clicks must not discard wizard input (Cancel / Escape).
  return createPortal(
    <Overlay>
      <Modal style={{ width: 'min(900px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('settings.sqlWizard.title')}</ModalHeader>
        <ModalBody>
          <Field label={t('settings.sqlWizard.table')}>
            <SearchSelect value={tableName} onChange={onTableChange} options={tableOpts}
              placeholder={t('settings.sqlWizard.pickTable')} />
          </Field>
          {table && (
            <Section>
              <RowBar>
                <SectionTitle style={{ flex: 1 }}>{t('settings.sqlWizard.columns', { n: cols.size, total: table.columns.length })}</SectionTitle>
                <MiniBtn type="button" onClick={() => onColsChange(new Set(table.columns.map((c) => c.name)))}>{t('common.selectAll')}</MiniBtn>
                <MiniBtn type="button" onClick={() => onColsChange(new Set())}>{t('common.selectNone')}</MiniBtn>
              </RowBar>
              <ColGrid>
                {table.columns.map((c) => {
                  const on = cols.has(c.name)
                  return (
                    <ColCheck key={c.name}>
                      <Checkbox checked={on} onChange={() => {
                        const next = new Set(cols); if (on) next.delete(c.name); else next.add(c.name); onColsChange(next)
                      }} label={<>{c.name}{c.type && <span className="type"> · {c.type}</span>}</>} />
                    </ColCheck>
                  )
                })}
              </ColGrid>
            </Section>
          )}
          {table && (
            <Section>
              <SectionTitle>{t('settings.sqlWizard.where')}</SectionTitle>
              {wheres.map((w, i) => (
                <RowBar key={i}>
                  <div style={{ flex: '1 1 200px' }}>
                    <SearchSelect value={w.col} onChange={(v) => onWheresChange(wheres.map((x, j) => j === i ? { ...x, col: v } : x))} options={colOpts} placeholder={t('settings.sqlWizard.column')} />
                  </div>
                  <div style={{ flex: '0 0 130px' }}>
                    <SearchSelect value={w.op}
                      onChange={(v) => onWheresChange(wheres.map((x, j) => j === i ? { ...x, op: (v as WhereOp) } : x))}
                      options={WHERE_OPS.map((o) => ({ value: o, label: o }))} />
                  </div>
                  <Input style={{ flex: '1 1 200px' }} placeholder={isNullary(w.op) ? '' : t('settings.sqlWizard.valueHint')}
                    value={w.value} onChange={(e) => onWheresChange(wheres.map((x, j) => j === i ? { ...x, value: e.target.value } : x))}
                    disabled={isNullary(w.op)} />
                  <SmallX type="button" title={t('common.remove')} onClick={() => onWheresChange(wheres.filter((_, j) => j !== i))}><X size={13} /></SmallX>
                </RowBar>
              ))}
              <MiniBtn type="button" onClick={() => onWheresChange([...wheres, { col: colOpts[0]?.value ?? '', op: '=', value: '' }])}><Plus size={12} /> {t('settings.sqlWizard.addWhere')}</MiniBtn>
            </Section>
          )}
          {table && (
            <Section>
              <SectionTitle>{t('settings.sqlWizard.orderBy')}</SectionTitle>
              {orders.map((o, i) => (
                <RowBar key={i}>
                  <div style={{ flex: '1 1 200px' }}>
                    <SearchSelect value={o.col} onChange={(v) => onOrdersChange(orders.map((x, j) => j === i ? { ...x, col: v } : x))} options={colOpts} placeholder={t('settings.sqlWizard.column')} />
                  </div>
                  <div style={{ flex: '0 0 110px' }}>
                    <SearchSelect value={o.dir} onChange={(v) => onOrdersChange(orders.map((x, j) => j === i ? { ...x, dir: (v as 'ASC' | 'DESC') } : x))}
                      options={[{ value: 'ASC', label: 'ASC' }, { value: 'DESC', label: 'DESC' }]} />
                  </div>
                  <SmallX type="button" title={t('common.remove')} onClick={() => onOrdersChange(orders.filter((_, j) => j !== i))}><X size={13} /></SmallX>
                </RowBar>
              ))}
              <MiniBtn type="button" onClick={() => onOrdersChange([...orders, { col: colOpts[0]?.value ?? '', dir: 'ASC' }])}><Plus size={12} /> {t('settings.sqlWizard.addOrder')}</MiniBtn>
            </Section>
          )}
          <Section>
            <SectionTitle>{t('settings.sqlWizard.preview')}</SectionTitle>
            <SqlEditor value={previewSql} onChange={() => undefined} rows={6} readOnly />
          </Section>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel}>{t('common.cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={() => onInsert(insertSql)} disabled={!canInsert} autoFocus>
            {t('settings.sqlWizard.insert')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}

function isNullary(op: WhereOp): boolean {
  return op === 'IS NULL' || op === 'IS NOT NULL'
}

/** SQL fragment for one WHERE row. Empty value on a non-nullary op → drop it (the row hasn't been
 *  filled in yet; the operator can leave it half-built without breaking the preview). Values are
 *  quoted as strings; numbers can be typed unquoted on a value the operator owns (we don't try
 *  to infer types). `IN` accepts a comma-separated list. */
function whereFragment(w: WhereRow): string | null {
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

function quoteIfNonNumeric(v: string): string {
  if (/^-?\d+(\.\d+)?$/.test(v)) return v               // bare number
  if (/^:[A-Za-z_]\w*$/.test(v)) return v               // a `:bind` reference — keep as-is (operator's intent)
  return `'${v.replace(/'/g, "''")}'`                  // SQL-quoted, single-quote-escaped
}

function generateSql(
  table: PoolTable | undefined, cols: Set<string>, wheres: WhereRow[], orders: OrderRow[],
): string {
  if (!table || cols.size === 0) return ''
  const target = table.schema ? `${table.schema}.${table.name}` : table.name
  // Always emit explicit column names — even "all columns selected" reads cleaner with the
  // list than ``*``. The user explicitly asked for this: a wizard that rebuilds the query
  // with ``*`` loses the column ordering + names that were already there. Tabletype info is
  // also lost (e.g. a stored ``SELECT id, name FROM t`` becomes ``SELECT * FROM t`` if all
  // columns happen to be selected, even though the operator may want to drop a column next.)
  const selected = table.columns.filter((c) => cols.has(c.name)).map((c) => c.name)
  const colList = selected.join(',\n    ')
  let sql = `SELECT\n    ${colList}\nFROM\n    ${target}`
  const wf = wheres.map(whereFragment).filter((x): x is string => x !== null)
  if (wf.length) sql += `\nWHERE\n    ${wf.join('\n    AND ')}`
  if (orders.length) {
    const ob = orders.filter((o) => o.col).map((o) => `${o.col} ${o.dir}`)
    if (ob.length) sql += `\nORDER BY\n    ${ob.join(', ')}`
  }
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
function parseExistingSelect(
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
function locateTopLevelTokens(sql: string, upper: string, keywords: string[]): Record<string, number> {
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
function splitTopLevel(text: string, sep: string): string[] {
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

const _WHERE_OPS_SORTED = [...WHERE_OPS].sort((a, b) => b.length - a.length)  // longest first so `<=` beats `<`

/** Parse a single ``col op value`` (or ``col IS [NOT] NULL``, or ``col IN (...)``) clause
 *  against *table*'s schema. Returns ``null`` for shapes the wizard's WHERE editor can't
 *  faithfully represent. */
function parseWhereClause(part: string, table: PoolTable): WhereRow | null {
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
function stripValueLiteral(s: string): string | null {
  const t = s.trim()
  if (!t) return null
  if (t.startsWith("'") && t.endsWith("'")) {
    return t.slice(1, -1).replace(/''/g, "'")
  }
  if (/^-?\d+(\.\d+)?$/.test(t)) return t
  if (/^:[A-Za-z_]\w*$/.test(t)) return t
  return null
}
