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
  onInsert: (sql: string) => void
  onCancel: () => void
}

export function SqlWizardModal({ schema, initialTable, onInsert, onCancel }: SqlWizardModalProps) {
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
  // Default: every column selected (operators almost always want everything; unticking is easier
  // than ticking 30 columns).
  const [cols, setCols] = useState<Set<string>>(new Set(table?.columns.map((c) => c.name) ?? []))
  useEffect(() => { setCols(new Set(table?.columns.map((c) => c.name) ?? [])) }, [table])
  const [wheres, setWheres] = useState<WhereRow[]>([])
  const [orders, setOrders] = useState<OrderRow[]>([])

  const colOpts: SearchSelectOption[] = useMemo(
    () => (table?.columns ?? []).map((c) => ({ value: c.name, label: c.name })),
    [table],
  )
  const generated = useMemo(() => generateSql(table, cols, wheres, orders), [table, cols, wheres, orders])
  const canInsert = !!table && cols.size > 0

  return (
    <Overlay onClick={onCancel}>
      <Modal style={{ width: 'min(900px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('settings.sqlWizard.title')}</ModalHeader>
        <ModalBody>
          <Field label={t('settings.sqlWizard.table')}>
            <SearchSelect value={tableName} onChange={setTableName} options={tableOpts}
              placeholder={t('settings.sqlWizard.pickTable')} />
          </Field>
          {table && (
            <Section>
              <RowBar>
                <SectionTitle style={{ flex: 1 }}>{t('settings.sqlWizard.columns', { n: cols.size, total: table.columns.length })}</SectionTitle>
                <MiniBtn type="button" onClick={() => setCols(new Set(table.columns.map((c) => c.name)))}>{t('common.selectAll')}</MiniBtn>
                <MiniBtn type="button" onClick={() => setCols(new Set())}>{t('common.selectNone')}</MiniBtn>
              </RowBar>
              <ColGrid>
                {table.columns.map((c) => {
                  const on = cols.has(c.name)
                  return (
                    <ColCheck key={c.name}>
                      <Checkbox checked={on} onChange={() => {
                        const next = new Set(cols); if (on) next.delete(c.name); else next.add(c.name); setCols(next)
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
                    <SearchSelect value={w.col} onChange={(v) => setWheres(wheres.map((x, j) => j === i ? { ...x, col: v } : x))} options={colOpts} placeholder={t('settings.sqlWizard.column')} />
                  </div>
                  <div style={{ flex: '0 0 130px' }}>
                    <SearchSelect value={w.op}
                      onChange={(v) => setWheres(wheres.map((x, j) => j === i ? { ...x, op: (v as WhereOp) } : x))}
                      options={WHERE_OPS.map((o) => ({ value: o, label: o }))} />
                  </div>
                  <Input style={{ flex: '1 1 200px' }} placeholder={isNullary(w.op) ? '' : t('settings.sqlWizard.valueHint')}
                    value={w.value} onChange={(e) => setWheres(wheres.map((x, j) => j === i ? { ...x, value: e.target.value } : x))}
                    disabled={isNullary(w.op)} />
                  <SmallX type="button" title={t('common.remove')} onClick={() => setWheres(wheres.filter((_, j) => j !== i))}><X size={13} /></SmallX>
                </RowBar>
              ))}
              <MiniBtn type="button" onClick={() => setWheres([...wheres, { col: colOpts[0]?.value ?? '', op: '=', value: '' }])}><Plus size={12} /> {t('settings.sqlWizard.addWhere')}</MiniBtn>
            </Section>
          )}
          {table && (
            <Section>
              <SectionTitle>{t('settings.sqlWizard.orderBy')}</SectionTitle>
              {orders.map((o, i) => (
                <RowBar key={i}>
                  <div style={{ flex: '1 1 200px' }}>
                    <SearchSelect value={o.col} onChange={(v) => setOrders(orders.map((x, j) => j === i ? { ...x, col: v } : x))} options={colOpts} placeholder={t('settings.sqlWizard.column')} />
                  </div>
                  <div style={{ flex: '0 0 110px' }}>
                    <SearchSelect value={o.dir} onChange={(v) => setOrders(orders.map((x, j) => j === i ? { ...x, dir: (v as 'ASC' | 'DESC') } : x))}
                      options={[{ value: 'ASC', label: 'ASC' }, { value: 'DESC', label: 'DESC' }]} />
                  </div>
                  <SmallX type="button" title={t('common.remove')} onClick={() => setOrders(orders.filter((_, j) => j !== i))}><X size={13} /></SmallX>
                </RowBar>
              ))}
              <MiniBtn type="button" onClick={() => setOrders([...orders, { col: colOpts[0]?.value ?? '', dir: 'ASC' }])}><Plus size={12} /> {t('settings.sqlWizard.addOrder')}</MiniBtn>
            </Section>
          )}
          <Section>
            <SectionTitle>{t('settings.sqlWizard.preview')}</SectionTitle>
            <SqlEditor value={generated} onChange={() => undefined} rows={6} readOnly />
          </Section>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel}>{t('common.cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={() => onInsert(generated)} disabled={!canInsert} autoFocus>
            {t('settings.sqlWizard.insert')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
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
  const selected = table.columns.filter((c) => cols.has(c.name)).map((c) => c.name)
  const colList = selected.length === table.columns.length ? '*' : selected.join(',\n    ')
  let sql = `SELECT\n    ${colList === '*' ? '*' : colList}\nFROM\n    ${target}`
  const wf = wheres.map(whereFragment).filter((x): x is string => x !== null)
  if (wf.length) sql += `\nWHERE\n    ${wf.join('\n    AND ')}`
  if (orders.length) {
    const ob = orders.filter((o) => o.col).map((o) => `${o.col} ${o.dir}`)
    if (ob.length) sql += `\nORDER BY\n    ${ob.join(', ')}`
  }
  return sql
}
