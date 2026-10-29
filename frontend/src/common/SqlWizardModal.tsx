// A multi-table SELECT builder for the SqlEditor — a button on each editor opens this. The operator
// builds a FROM + optional JOINs (each table picked lazily: schema → name filter → table, then the
// join condition), ticks columns across all the joined tables, adds optional WHERE / ORDER BY rows,
// sees a live preview of the generated SQL, and clicks Insert to replace the editor's contents.
//
// LAZY by design: it loads the pool's SCHEMA LIST first, then one schema's tables only when picked
// (filtered by a name pattern), then one table's columns when selected — never the full pool walk
// (which hangs 10+ s on a multi-schema JDE catalog). Scoped to SELECT only; INSERT/UPDATE/DELETE
// stay hand-written (the CRUD wizard scaffolds those from a single table).
//
// NOT re-exported from `common/index.ts` — direct import only (keeps it in the Settings chunk).
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Banner } from './Banner'
import { Button } from './Button'
import { Checkbox } from './Checkbox'
import { Field, Input, PasswordInput as _pw } from './Input'  // PasswordInput unused — silence the re-export
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from './Modal'
import { SearchSelect, type SearchSelectOption } from './SearchSelect'
import { SqlEditor } from './SqlEditor'
import { buildCrudSql } from './sqlBuild'
import {
  findReferencedTables, getPoolSchemaNames, getPoolSchemaTables,
  type PoolColumn, type PoolTable,
} from '../services/poolSchema'
import { colors, fontSize, fonts, radius } from '../theme'
import {
  WHERE_OPS, JOIN_TYPES, suggestAlias, STMT_TO_CRUD, firstSqlKeyword, isNullary, generateSql, parseExistingSelect, parseTableChain, parseOn, parseMultiSelect, type WhereOp, type WhereRow, type OrderRow, type JoinType, type JoinCond, type JoinTable, type WizardStatementType,
} from '../services/sqlParse'

// Re-exported: callers import these from here (the logic lives in services/sqlParse.ts).
export { JOIN_TYPES, generateSql, suggestAlias } from '../services/sqlParse'
export type { JoinCond, JoinTable, JoinType, WizardStatementType } from '../services/sqlParse'

// PasswordInput is imported only to keep the barrel re-export honest; silence eslint.
void _pw

// Shared modal frame size so SELECT / INSERT / UPDATE / DELETE all open at the same width + a floor
// height — short builders (DELETE) fill to the minimum instead of collapsing; tall ones (SELECT with
// joins) grow and the body scrolls (Modal caps at max-height: 90vh).
const WIZARD_MODAL_STYLE = { width: 'min(900px, 95vw)', minHeight: 'min(600px, 82vh)' } as const

const Section = styled.div`
  display: flex; flex-direction: column; gap: 6px;
  font-size: ${fontSize.sm}; color: ${colors.text.secondary};
`
const SectionTitle = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em;`
const OnLabel = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px;`
const OnText = styled.div`font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; margin-top: 4px; white-space: pre-wrap; word-break: break-word;`
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
// A column row in the mutation builder: include checkbox (or bare name) on the left, an optional
// "key" toggle on the right (UPDATE/DELETE WHERE).
const MutCol = styled.div`
  display: flex; align-items: center; gap: 8px; justify-content: space-between;
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & .type { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  & .key { display: inline-flex; align-items: center; gap: 4px; color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
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

// What a TablePicker pick yields: the chosen table's ``name`` + ``columns`` plus the schema
// EXPRESSION to write in the SQL — a portable ``#SCHEMA.<KEY>#`` token when the pool defines a
// schema map, else the real schema name, or null for a single-/zero-schema pool.
export interface PickedTable { schema: string | null; name: string; columns: PoolColumn[] }
// Lazy table picker — schema list → ONE schema's tables (name-filtered) → pick. A real schema needs
// a name filter before listing (no full-catalog dump); a single-/zero-schema pool (SQLite) lists
// directly. Used for the base FROM and each added JOIN, and reused by the dictionary query generator.
export function TablePicker({ connector, onPick }: { connector: string; onPick: (t: PickedTable) => void }) {
  const { t } = useTranslation()
  const [schemasInfo, setSchemasInfo] = useState<{ schemas: string[]; map: Record<string, string> } | null | undefined>(undefined)
  const [pickedSchema, setPickedSchema] = useState('')   // the schema EXPRESSION (a #SCHEMA.X# token or a real name)
  const [filterRaw, setFilterRaw] = useState('')
  const [filter, setFilter] = useState('')
  const [tables, setTables] = useState<PoolTable[] | null | undefined>(undefined)
  useEffect(() => {
    let c = false
    getPoolSchemaNames(connector).then((s) => { if (!c) setSchemasInfo(s ? { schemas: s.schemas, map: s.schema_map ?? {} } : null) })
    return () => { c = true }
  }, [connector])
  // Prefer the portable tokens (``#SCHEMA.CTL#`` · PS920CTL) when the pool maps them, so a picked
  // table writes the token (env-portable); else the raw schema names.
  const schemaOpts = useMemo<SearchSelectOption[]>(() => {
    const map = schemasInfo?.map ?? {}
    const keys = Object.keys(map)
    if (keys.length) return keys.sort().map((k) => ({ value: `#SCHEMA.${k}#`, label: `${k} · ${map[k]}`, mono: `#SCHEMA.${k}#` }))
    return (schemasInfo?.schemas ?? []).slice().sort().map((s) => ({ value: s, label: s, mono: s }))
  }, [schemasInfo])
  useEffect(() => {
    if (!schemasInfo || pickedSchema) return
    if (schemaOpts.length === 1) setPickedSchema(schemaOpts[0].value)
    else if (schemaOpts.length === 0) setPickedSchema('__nopicker__')
  }, [schemasInfo, schemaOpts, pickedSchema])
  useEffect(() => { const h = setTimeout(() => setFilter(filterRaw.trim()), 350); return () => clearTimeout(h) }, [filterRaw])
  useEffect(() => { setFilterRaw(''); setFilter('') }, [pickedSchema])
  // Fetch only when a filter is set (real schema) — or unconditionally for the no-picker (small) pool.
  // ``pickedSchema`` may be a ``#SCHEMA.X#`` token; the backend resolves it to the real owner.
  const noPicker = pickedSchema === '__nopicker__'
  const shouldFetch = noPicker || (!!pickedSchema && !!filter)
  useEffect(() => {
    if (!shouldFetch) { setTables(undefined); return }
    let c = false; setTables(undefined)
    getPoolSchemaTables(connector, noPicker ? null : pickedSchema, filter || null)
      .then((s) => { if (!c) setTables(s?.tables ?? null) })
    return () => { c = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- shouldFetch folds in pickedSchema + filter
  }, [connector, shouldFetch, pickedSchema, filter])
  const tableOpts = useMemo<SearchSelectOption[]>(
    () => (tables ?? []).map((tb) => ({ value: tb.name, label: tb.schema ? `${tb.schema}.${tb.name}` : tb.name, mono: tb.name })),
    [tables],
  )
  return (
    <RowBar style={{ flexWrap: 'wrap' }}>
      {schemaOpts.length > 1 && (
        <div style={{ flex: '0 0 180px' }}>
          <SearchSelect value={noPicker ? '' : pickedSchema} onChange={setPickedSchema} options={schemaOpts}
            placeholder={t('settings.sqlWizard.pickSchema')} />
        </div>
      )}
      {!noPicker && (
        <Input style={{ flex: '0 0 150px' }} placeholder={t('settings.sqlWizard.filterTable')}
          value={filterRaw} onChange={(e) => setFilterRaw(e.target.value)} />
      )}
      <div style={{ flex: 1, minWidth: 160 }}>
        <SearchSelect value=""
          onChange={(v) => { const tb = (tables ?? []).find((x) => x.name === v); if (tb) onPick({ schema: noPicker ? null : pickedSchema, name: tb.name, columns: tb.columns }) }}
          options={tableOpts}
          placeholder={!noPicker && !filter ? t('settings.sqlWizard.typeFilter') : (tables === undefined ? t('common.loading') : t('settings.sqlWizard.pickTable'))}
          loading={shouldFetch && tables === undefined} />
      </div>
    </RowBar>
  )
}


export interface SqlWizardModalProps {
  /** The connector — the wizard lazily introspects its pool (schema list → tables → columns). */
  connector: string
  /** Existing SQL in the editor — the wizard lazily loads each referenced table's columns and tries
   *  to parse the simple single-table SELECT shape so opening on an existing query pre-fills its
   *  widgets. A multi-table / complex query shows untouched until the operator changes a widget. */
  initialSql?: string
  /** The slot's expected statement kind (from the CRUD tab). Omitted → inferred from the SQL's
   *  leading keyword, defaulting to SELECT. */
  statementType?: WizardStatementType
  onInsert: (sql: string) => void
  onCancel: () => void
}

/** Dispatcher: pick the SELECT builder or the mutation (INSERT/UPDATE/DELETE) builder by expected
 *  kind, and lock to read-only when the existing SQL's leading keyword doesn't match. */
export function SqlWizardModal(props: SqlWizardModalProps) {
  const kw = firstSqlKeyword(props.initialSql ?? '')
  const expected: WizardStatementType = props.statementType ?? (kw && kw !== 'OTHER' ? kw : 'SELECT')
  const forceReadOnly = kw != null && kw !== expected   // mismatch, or a CTE/MERGE/… the builder won't edit
  if (expected === 'SELECT') return <SelectWizard {...props} forceReadOnly={forceReadOnly} />
  return <MutationWizard {...props} crud={STMT_TO_CRUD[expected]} expected={expected} forceReadOnly={forceReadOnly} />
}

function SelectWizard({ connector, initialSql, onInsert, onCancel, forceReadOnly }: SqlWizardModalProps & { forceReadOnly?: boolean }) {
  const { t } = useTranslation()
  // The FROM/JOIN chain — joins[0] is the base table, joins[1+] carry a join type + ON. Column ids
  // are ``<alias>.<col>`` so columns from different joined tables never collide.
  const [joins, setJoins] = useState<JoinTable[]>([])
  const [cols, setCols] = useState<Set<string>>(new Set())
  const [wheres, setWheres] = useState<WhereRow[]>([])
  const [orders, setOrders] = useState<OrderRow[]>([])
  const [adding, setAdding] = useState(false)
  // ``readOnly`` — the opened query parsed into a join chain, but its SELECT list / WHERE / ORDER BY
  // use features the builder can't represent (output-column aliases, expression columns, …). We show
  // the joins for reference + a banner, keep the original SQL in the preview, and don't let the
  // operator rebuild (which would silently drop the aliases/expressions). Edit those in SQL.
  // ``forceReadOnly`` (the SQL doesn't lead with SELECT — a write statement or a CTE/MERGE) seeds the
  // read-only state, so the builder shows the query for reference without rebuilding it as a SELECT.
  const [readOnly, setReadOnly] = useState(!!forceReadOnly)
  // ``userTouched`` — until the operator changes a widget, the preview shows the ORIGINAL SQL verbatim
  // (so opening on a complex/joined query the builder can't round-trip doesn't silently rewrite it).
  const [userTouched, setUserTouched] = useState(false)
  const touch = () => { if (!userTouched) setUserTouched(true) }

  // Lazy mount seed — parse the existing query's FROM/JOIN chain and load each referenced table's
  // columns. A single-table query pre-fills the column / WHERE / ORDER BY widgets via
  // ``parseExistingSelect``. A multi-table query rebuilds the full join chain (table + type + ON, the
  // ON decomposed into source/target pairs or kept raw) and, when its SELECT/WHERE/ORDER are
  // representable, pre-fills those too; when they're not (output aliases, expression columns) it marks
  // the wizard read-only so the original SQL is preserved verbatim instead of being silently rewritten.
  useEffect(() => {
    if (forceReadOnly) return   // not a SELECT — keep the original SQL, don't parse it as one
    let cancelled = false
    const sql = initialSql ?? ''
    const chain = parseTableChain(sql)
    if (!chain || chain.length === 0) return   // empty / unparseable FROM → operator picks the base below
    void Promise.all(chain.map((c) => getPoolSchemaTables(connector, c.schema, c.name))).then((parts) => {
      if (cancelled) return
      // Resolve each parsed table to its introspected columns; abort to the base-pick UI if any miss.
      const taken = new Set<string>()
      const built: JoinTable[] = []
      for (let i = 0; i < chain.length; i++) {
        const c = chain[i]
        const tb = (parts[i]?.tables ?? []).find((x) => x.name.toLowerCase() === c.name.toLowerCase())
        if (!tb) return
        // Honour the alias the query wrote (its ON refs use it); fall back to the table name. Keep the
        // schema EXPRESSION (a portable ``#SCHEMA.X#`` token) rather than the resolved owner.
        let alias = c.alias || suggestAlias(tb.name, taken)
        if (taken.has(alias.toLowerCase())) alias = suggestAlias(tb.name, taken)
        taken.add(alias.toLowerCase())
        built.push({ schema: c.schema ?? tb.schema ?? null, name: tb.name, alias, columns: tb.columns })
      }
      // Join conditions: decompose each ON into oriented equi-pairs, or keep it raw.
      for (let i = 1; i < built.length; i++) {
        built[i].join = { type: chain[i].joinType ?? 'INNER', ...parseOn(chain[i].onText ?? '', built[i].alias, built) }
      }
      setJoins(built)

      if (built.length === 1) {
        // Single table — reuse the well-tested simple-shape parser.
        const tb = built[0]
        const parsed = parseExistingSelect(sql, { name: tb.name, schema: tb.schema ?? undefined, kind: 'table', columns: tb.columns })
        if (parsed) {
          setCols(new Set([...parsed.cols].map((c) => `${tb.alias}.${c}`)))
          setWheres(parsed.wheres.map((w) => ({ ...w, col: `${tb.alias}.${w.col}` })))
          setOrders(parsed.orders.map((o) => ({ ...o, col: `${tb.alias}.${o.col}` })))
        } else {
          setCols(new Set(tb.columns.map((c) => `${tb.alias}.${c.name}`)))
        }
        return
      }
      // Multi-table — try to round-trip the SELECT/WHERE/ORDER; otherwise show read-only + banner.
      const parsed = parseMultiSelect(sql, built)
      if (parsed) {
        setCols(parsed.cols); setWheres(parsed.wheres); setOrders(parsed.orders)
      } else {
        setReadOnly(true)
      }
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only seed
  }, [])

  const hasBase = joins.length > 0 && !!joins[0].name
  const single = joins.length === 1
  const pickBase = (tb: PickedTable) => {
    touch()
    const alias = suggestAlias(tb.name, new Set())
    setJoins([{ schema: tb.schema, name: tb.name, alias, columns: tb.columns }])
    setCols(new Set(tb.columns.map((c) => `${alias}.${c.name}`)))
    setWheres([]); setOrders([])
  }
  const addJoin = (tb: PickedTable) => {
    touch(); setAdding(false)
    const taken = new Set(joins.map((j) => j.alias.toUpperCase()))
    const alias = suggestAlias(tb.name, taken)
    setJoins((js) => [...js, { schema: tb.schema, name: tb.name, alias, columns: tb.columns, join: { type: 'INNER', conds: [{ left: '', right: '' }] } }])
  }
  const removeJoin = (idx: number) => {
    touch()
    const j = joins[idx]
    setJoins((js) => js.filter((_, i) => i !== idx))
    setCols((s) => new Set([...s].filter((id) => !id.startsWith(`${j.alias}.`))))
    setWheres((ws) => ws.filter((w) => !w.col.startsWith(`${j.alias}.`)))
    setOrders((os) => os.filter((o) => !o.col.startsWith(`${j.alias}.`)))
  }
  const setJoinType = (idx: number, type: JoinType) => {
    touch()
    setJoins((js) => js.map((j, i) => i === idx
      ? { ...j, join: { type, conds: j.join?.conds ?? [{ left: '', right: '' }] } } : j))
  }
  const setJoinConds = (idx: number, conds: JoinCond[]) => {
    touch()
    setJoins((js) => js.map((j, i) => i === idx
      ? { ...j, join: { type: j.join?.type ?? 'INNER', conds } } : j))
  }
  const setJoinRaw = (idx: number, raw: string) => {
    touch()
    setJoins((js) => js.map((j, i) => i === idx
      ? { ...j, join: { type: j.join?.type ?? 'INNER', conds: j.join?.conds ?? [], raw } } : j))
  }
  // Toggle a join's ON between the source/target pair builder and a free-text expression. Switching to
  // text seeds the box with the pairs built so far; switching back to the builder drops the raw text.
  const toggleJoinRaw = (idx: number) => {
    touch()
    setJoins((js) => js.map((j, i) => {
      if (i !== idx) return j
      const type = j.join?.type ?? 'INNER'
      const conds = j.join?.conds ?? [{ left: '', right: '' }]
      if (j.join?.raw !== undefined) return { ...j, join: { type, conds: conds.length ? conds : [{ left: '', right: '' }] } }
      const seed = conds.filter((c) => c.left && c.right).map((c) => `${c.left.toUpperCase()} = ${c.right.toUpperCase()}`).join(' AND ')
      return { ...j, join: { type, conds, raw: seed } }
    }))
  }

  const onColsChange = (next: Set<string>) => { touch(); setCols(next) }
  const onWheresChange = (next: WhereRow[]) => { touch(); setWheres(next) }
  const onOrdersChange = (next: OrderRow[]) => { touch(); setOrders(next) }
  // All selectable column ids across the joined tables (for the WHERE / ORDER pickers). Bare label
  // for a single table; alias-qualified when there's a join (so duplicate column names disambiguate).
  const allColOpts = useMemo<SearchSelectOption[]>(
    () => joins.flatMap((j) => j.columns.map((c) => { const disp = single ? c.name.toUpperCase() : `${j.alias}.${c.name.toUpperCase()}`; return { value: `${j.alias}.${c.name}`, label: disp, mono: disp } })),
    [joins, single],
  )
  // Alias-qualified column options for a subset of tables — feeds the join ON source/target pickers
  // (source = every preceding table, target = the table being joined).
  const qualOpts = (list: JoinTable[]): SearchSelectOption[] =>
    list.flatMap((j) => j.columns.map((c) => { const disp = `${j.alias}.${c.name.toUpperCase()}`; return { value: `${j.alias}.${c.name}`, label: disp, mono: disp } }))

  const generated = useMemo(() => generateSql(joins, cols, wheres, orders), [joins, cols, wheres, orders])
  // In read-only mode (and until the operator touches a widget) the preview is the ORIGINAL SQL verbatim.
  const previewSql = readOnly || (!userTouched && (initialSql ?? '').trim()) ? (initialSql as string) : generated
  // A join needs an ON before we'll let it through (a bare cross join is almost never intended).
  // A join's ON is satisfied by a raw expression OR at least one complete source/target pair.
  const joinHasOn = (j: JoinTable) => (j.join?.raw ?? '').trim() !== '' || (j.join?.conds ?? []).some((c) => c.left && c.right)
  const canInsert = !readOnly && hasBase && cols.size > 0 && (single || joins.slice(1).every(joinHasOn))

  // Portal the overlay to ``document.body`` so it escapes the parent Modal's ``backdrop-filter``;
  // z-index 1000 sits above the editor modal (900) but below the global confirm/prompt overlay (2000).
  return createPortal(
    <Overlay style={{ zIndex: 1000 }}>
      <Modal style={WIZARD_MODAL_STYLE} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('settings.sqlWizard.title')}</ModalHeader>
        <ModalBody>
          {/* ── tables: base FROM + JOINs (each lazily picked) ── */}
          {readOnly && !hasBase ? null : !hasBase ? (
            <Field label={t('settings.sqlWizard.table')}>
              <TablePicker connector={connector} onPick={pickBase} />
            </Field>
          ) : (
            <Section>
              <SectionTitle>{t('settings.sqlWizard.tables')}</SectionTitle>
              {joins.map((j, i) => (
                <div key={`${j.alias}_${i}`}>
                  <RowBar>
                    {i > 0 && !readOnly && (
                      <div style={{ flex: '0 0 120px' }}>
                        <SearchSelect value={j.join?.type ?? 'INNER'} onChange={(v) => setJoinType(i, v as JoinType)}
                          options={JOIN_TYPES.map((x) => ({ value: x, label: `${x} JOIN` }))} />
                      </div>
                    )}
                    {i > 0 && readOnly && (
                      <span style={{ color: colors.text.muted, fontSize: fontSize.sm, flex: '0 0 120px' }}>{j.join?.type ?? 'INNER'} JOIN</span>
                    )}
                    <strong style={{ fontFamily: fonts.mono, fontSize: fontSize.sm, flex: 1 }}>
                      {(j.schema ? `${j.schema}.` : '') + j.name}<span style={{ color: colors.text.muted, marginLeft: 6 }}>{j.alias}</span>
                    </strong>
                    {i > 0 && !readOnly && <SmallX type="button" title={t('common.remove')} onClick={() => removeJoin(i)}><X size={13} /></SmallX>}
                  </RowBar>
                  {i > 0 && (() => {
                    const conds = j.join?.conds ?? [{ left: '', right: '' }]
                    const isRaw = j.join?.raw !== undefined
                    const leftOpts = qualOpts(joins.slice(0, i))
                    const rightOpts = qualOpts([j])
                    const setCond = (k: number, patch: Partial<JoinCond>) =>
                      setJoinConds(i, conds.map((c, ci) => ci === k ? { ...c, ...patch } : c))
                    return (
                      <div style={{ marginTop: 4 }}>
                        <RowBar>
                          <OnLabel style={{ flex: 1, marginBottom: 0 }}>{t('settings.sqlWizard.joinOn')}</OnLabel>
                          {!readOnly && (
                            <MiniBtn type="button" onClick={() => toggleJoinRaw(i)}>
                              {isRaw ? t('settings.sqlWizard.joinUseBuilder') : t('settings.sqlWizard.joinEditText')}
                            </MiniBtn>
                          )}
                        </RowBar>
                        {readOnly ? (
                          <OnText>{j.join?.raw?.trim() || conds.filter((c) => c.left && c.right).map((c) => `${c.left.toUpperCase()} = ${c.right.toUpperCase()}`).join(' AND ')}</OnText>
                        ) : isRaw ? (
                          <Input style={{ marginTop: 4, fontFamily: fonts.mono }}
                            value={j.join?.raw ?? ''} onChange={(e) => setJoinRaw(i, e.target.value)}
                            placeholder={t('settings.sqlWizard.joinOnExpr')} />
                        ) : (<>
                          {conds.map((c, k) => (
                            <RowBar key={k} style={{ marginTop: 4 }}>
                              <div style={{ flex: 1 }}>
                                <SearchSelect value={c.left} onChange={(v) => setCond(k, { left: v })}
                                  options={leftOpts} placeholder={t('settings.sqlWizard.joinSource')} />
                              </div>
                              <span style={{ color: colors.text.muted }}>=</span>
                              <div style={{ flex: 1 }}>
                                <SearchSelect value={c.right} onChange={(v) => setCond(k, { right: v })}
                                  options={rightOpts} placeholder={t('settings.sqlWizard.joinTarget')} />
                              </div>
                              {conds.length > 1 && (
                                <SmallX type="button" title={t('common.remove')}
                                  onClick={() => setJoinConds(i, conds.filter((_, ci) => ci !== k))}><X size={13} /></SmallX>
                              )}
                            </RowBar>
                          ))}
                          <MiniBtn type="button" onClick={() => setJoinConds(i, [...conds, { left: '', right: '' }])}>
                            <Plus size={12} /> {t('settings.sqlWizard.addJoinCond')}
                          </MiniBtn>
                        </>)}
                      </div>
                    )
                  })()}
                </div>
              ))}
              {!readOnly && (adding
                ? <TablePicker connector={connector} onPick={addJoin} />
                : <MiniBtn type="button" onClick={() => setAdding(true)}><Plus size={12} /> {t('settings.sqlWizard.addJoin')}</MiniBtn>)}
            </Section>
          )}
          {readOnly && (
            <Banner $tone="warning" style={{ marginBottom: 12 }}>
              {forceReadOnly
                ? t('settings.sqlWizard.notSelectNote')
                : t('settings.sqlWizard.readOnlyNote')}
            </Banner>
          )}
          {/* ── columns, grouped per table ── */}
          {!readOnly && hasBase && joins.map((j, ji) => (
            <Section key={`cols_${j.alias}_${ji}`}>
              <RowBar>
                <SectionTitle style={{ flex: 1 }}>
                  {(single ? t('settings.sqlWizard.columnsTitle') : j.alias)} — {j.columns.filter((c) => cols.has(`${j.alias}.${c.name}`)).length}/{j.columns.length}
                </SectionTitle>
                <MiniBtn type="button" onClick={() => onColsChange(new Set([...cols, ...j.columns.map((c) => `${j.alias}.${c.name}`)]))}>{t('common.selectAll')}</MiniBtn>
                <MiniBtn type="button" onClick={() => onColsChange(new Set([...cols].filter((id) => !id.startsWith(`${j.alias}.`))))}>{t('common.selectNone')}</MiniBtn>
              </RowBar>
              <ColGrid>
                {j.columns.map((c) => {
                  const id = `${j.alias}.${c.name}`; const on = cols.has(id)
                  return (
                    <ColCheck key={id}>
                      <Checkbox checked={on} onChange={() => { const next = new Set(cols); if (on) next.delete(id); else next.add(id); onColsChange(next) }}
                        label={<>{c.name.toUpperCase()}{c.type && <span className="type"> · {c.type}</span>}</>} />
                    </ColCheck>
                  )
                })}
              </ColGrid>
            </Section>
          ))}
          {/* ── WHERE ── */}
          {!readOnly && hasBase && (
            <Section>
              <SectionTitle>{t('settings.sqlWizard.where')}</SectionTitle>
              {wheres.map((w, i) => (
                <RowBar key={i}>
                  <div style={{ flex: '1 1 200px' }}>
                    <SearchSelect value={w.col} onChange={(v) => onWheresChange(wheres.map((x, j) => j === i ? { ...x, col: v } : x))} options={allColOpts} placeholder={t('settings.sqlWizard.column')} />
                  </div>
                  <div style={{ flex: '0 0 130px' }}>
                    <SearchSelect value={w.op} onChange={(v) => onWheresChange(wheres.map((x, j) => j === i ? { ...x, op: (v as WhereOp) } : x))} options={WHERE_OPS.map((o) => ({ value: o, label: o }))} />
                  </div>
                  <Input style={{ flex: '1 1 200px' }} placeholder={isNullary(w.op) ? '' : t('settings.sqlWizard.valueHint')}
                    value={w.value} onChange={(e) => onWheresChange(wheres.map((x, j) => j === i ? { ...x, value: e.target.value } : x))} disabled={isNullary(w.op)} />
                  <SmallX type="button" title={t('common.remove')} onClick={() => onWheresChange(wheres.filter((_, j) => j !== i))}><X size={13} /></SmallX>
                </RowBar>
              ))}
              <MiniBtn type="button" onClick={() => onWheresChange([...wheres, { col: allColOpts[0]?.value ?? '', op: '=', value: '' }])}><Plus size={12} /> {t('settings.sqlWizard.addWhere')}</MiniBtn>
            </Section>
          )}
          {/* ── ORDER BY ── */}
          {!readOnly && hasBase && (
            <Section>
              <SectionTitle>{t('settings.sqlWizard.orderBy')}</SectionTitle>
              {orders.map((o, i) => (
                <RowBar key={i}>
                  <div style={{ flex: '1 1 200px' }}>
                    <SearchSelect value={o.col} onChange={(v) => onOrdersChange(orders.map((x, j) => j === i ? { ...x, col: v } : x))} options={allColOpts} placeholder={t('settings.sqlWizard.column')} />
                  </div>
                  <div style={{ flex: '0 0 110px' }}>
                    <SearchSelect value={o.dir} onChange={(v) => onOrdersChange(orders.map((x, j) => j === i ? { ...x, dir: (v as 'ASC' | 'DESC') } : x))}
                      options={[{ value: 'ASC', label: 'ASC' }, { value: 'DESC', label: 'DESC' }]} />
                  </div>
                  <SmallX type="button" title={t('common.remove')} onClick={() => onOrdersChange(orders.filter((_, j) => j !== i))}><X size={13} /></SmallX>
                </RowBar>
              ))}
              <MiniBtn type="button" onClick={() => onOrdersChange([...orders, { col: allColOpts[0]?.value ?? '', dir: 'ASC' }])}><Plus size={12} /> {t('settings.sqlWizard.addOrder')}</MiniBtn>
            </Section>
          )}
          <Section>
            <SectionTitle>{t('settings.sqlWizard.preview')}</SectionTitle>
            <SqlEditor value={previewSql} onChange={() => undefined} rows={6} readOnly />
          </Section>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel}>{t('common.cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={() => onInsert(previewSql)} disabled={!canInsert} autoFocus>
            {t('settings.sqlWizard.insert')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}

// ─── INSERT / UPDATE / DELETE builder ───────────────────────────────────────────────────────────
// A single-table column picker that reuses the shared CRUD generator (common/sqlBuild.ts) — the
// SAME statement import-from-DB emits — so a slot you didn't generate at import can be scaffolded
// here. INSERT picks the columns to write; UPDATE picks SET columns + key columns (WHERE); DELETE
// picks key columns. ``#SCHEMA.X#`` table tokens from the picker are preserved verbatim.
function MutationWizard({ connector, initialSql, crud, expected, forceReadOnly, onInsert, onCancel }:
  SqlWizardModalProps & { crud: 'post' | 'put' | 'delete'; expected: WizardStatementType; forceReadOnly?: boolean }) {
  const { t } = useTranslation()
  const [base, setBase] = useState<{ schema: string | null; name: string; columns: PoolColumn[] } | null>(null)
  const [included, setIncluded] = useState<Set<string>>(new Set())
  const [keys, setKeys] = useState<Set<string>>(new Set())
  const [userTouched, setUserTouched] = useState(false)
  const readOnly = !!forceReadOnly
  const touch = () => setUserTouched(true)

  const seedTable = (tb: { schema: string | null; name: string; columns: PoolColumn[] }) => {
    setBase(tb)
    setIncluded(new Set(tb.columns.map((c) => c.name)))
    // Guess keys from NOT NULL columns (same heuristic as the table-scaffold wizard).
    setKeys(new Set(tb.columns.filter((c) => c.nullable === false).map((c) => c.name)))
  }
  // Seed the base table from the existing statement (INSERT INTO / UPDATE / DELETE FROM <table>).
  useEffect(() => {
    if (forceReadOnly) return
    const r0 = findReferencedTables(initialSql ?? '')[0]
    if (!r0) return
    let cancelled = false
    void getPoolSchemaTables(connector, r0.schema ?? null, r0.name).then((part) => {
      if (cancelled) return
      const tb = (part?.tables ?? []).find((x) => x.name.toLowerCase() === r0.name.toLowerCase())
      if (tb) seedTable({ schema: r0.schema ?? tb.schema ?? null, name: tb.name, columns: tb.columns })
    })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- mount-only seed
  }, [])

  const pick = (p: PickedTable) => { touch(); seedTable({ schema: p.schema, name: p.name, columns: p.columns }) }
  const needsInclude = crud === 'post' || crud === 'put'
  const needsKey = crud === 'put' || crud === 'delete'
  const toggleInc = (name: string) => { touch(); setIncluded((s) => { const n = new Set(s); if (n.has(name)) n.delete(name); else n.add(name); return n }) }
  const toggleKey = (name: string) => { touch(); setKeys((s) => { const n = new Set(s); if (n.has(name)) n.delete(name); else n.add(name); return n }) }

  const generated = base ? buildCrudSql({ crud, schema: base.schema, table: base.name, selectCols: [...included], keyCols: [...keys] }) : ''
  const previewSql = readOnly || (!userTouched && (initialSql ?? '').trim()) ? (initialSql ?? '') : generated
  const canInsert = !readOnly && !!generated.trim()

  return createPortal(
    <Overlay style={{ zIndex: 1000 }}>
      <Modal style={WIZARD_MODAL_STYLE} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t(`settings.sqlWizard.title_${crud}`, `Build an ${expected}`)}</ModalHeader>
        <ModalBody>
          {readOnly ? (
            <Banner $tone="warning" style={{ marginBottom: 12 }}>
              {t('settings.sqlWizard.notMatchNote', { kw: expected })}
            </Banner>
          ) : !base ? (
            <Field label={t('settings.sqlWizard.table')}>
              <TablePicker connector={connector} onPick={pick} />
            </Field>
          ) : (
            <>
              <Section>
                <RowBar>
                  <SectionTitle style={{ flex: 1 }}>
                    {t('settings.sqlWizard.tables')} — <span style={{ fontFamily: fonts.mono, textTransform: 'none' }}>{(base.schema ? `${base.schema}.` : '') + base.name}</span>
                  </SectionTitle>
                  <MiniBtn type="button" onClick={() => { setBase(null); touch() }}>{t('settings.sqlWizard.changeTable')}</MiniBtn>
                </RowBar>
              </Section>
              <Section>
                <SectionTitle>
                  {crud === 'post' ? t('settings.sqlWizard.insertCols')
                    : crud === 'put' ? t('settings.sqlWizard.updateCols')
                      : t('settings.sqlWizard.deleteCols')}
                </SectionTitle>
                <ColGrid>
                  {base.columns.map((c) => (
                    <MutCol key={c.name}>
                      {needsInclude ? (
                        <Checkbox checked={included.has(c.name)} onChange={() => toggleInc(c.name)}
                          label={<>{c.name.toUpperCase()}{c.type && <span className="type"> · {c.type}</span>}</>} />
                      ) : (
                        <span>{c.name.toUpperCase()}{c.type && <span className="type"> · {c.type}</span>}</span>
                      )}
                      {needsKey && (
                        <span className="key">
                          <Checkbox checked={keys.has(c.name)} onChange={() => toggleKey(c.name)} disabled={needsInclude && !included.has(c.name)} />
                          {t('settings.sqlWizard.key')}
                        </span>
                      )}
                    </MutCol>
                  ))}
                </ColGrid>
              </Section>
            </>
          )}
          <Section>
            <SectionTitle>{t('settings.sqlWizard.preview')}</SectionTitle>
            <SqlEditor value={previewSql} onChange={() => undefined} rows={6} readOnly />
          </Section>
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel}>{t('common.cancel')}</Button>
          <Button $size="sm" $variant="primary" onClick={() => onInsert(previewSql)} disabled={!canInsert} autoFocus>
            {t('settings.sqlWizard.insert')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}

