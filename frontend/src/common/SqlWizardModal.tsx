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
import {
  getPoolSchemaNames, getPoolSchemaTables,
  type PoolColumn, type PoolTable,
} from '../services/poolSchema'
import { colors, fontSize, fonts, radius } from '../theme'

// PasswordInput is imported only to keep the barrel re-export honest; silence eslint.
void _pw

// One row in the WHERE list. We keep `op` as a literal string so it serializes cleanly into
// generated SQL; `IS NULL` / `IS NOT NULL` have no value.
const WHERE_OPS = ['=', '<>', '<', '>', '<=', '>=', 'LIKE', 'IN', 'IS NULL', 'IS NOT NULL'] as const
type WhereOp = (typeof WHERE_OPS)[number]
interface WhereRow { col: string; op: WhereOp; value: string }

interface OrderRow { col: string; dir: 'ASC' | 'DESC' }

const JOIN_TYPES = ['INNER', 'LEFT', 'RIGHT'] as const
type JoinType = (typeof JOIN_TYPES)[number]
// One equi-join condition: ``<left> = <right>``, each an ``alias.col`` id (left from a preceding
// table, right from this table). A list of these AND's together — JDE keys are often composite.
interface JoinCond { left: string; right: string }
// One table in the FROM/JOIN chain. ``join`` is undefined for the base table (the FROM); the rest
// carry a join type + the join condition. The condition is normally a list of equi-join pairs built
// from the source/target dropdowns; ``raw`` holds a free-text ON instead (set when an existing query
// uses a condition the pair model can't express — ``TRIM(x) IS NULL``, a function predicate, …).
interface JoinTable {
  schema: string | null
  name: string
  alias: string
  columns: PoolColumn[]
  join?: { type: JoinType; conds: JoinCond[]; raw?: string }
}
// A unique, stable alias for a table — the table's own name (uppercased), so a join reads clearly
// (``F0004.DTSY``) instead of a cryptic truncation. A self-join's second copy gets a numeric suffix.
function suggestAlias(name: string, taken: Set<string>): string {
  const base = (name.replace(/[^A-Za-z0-9]/g, '') || 't').toUpperCase()
  let a = base, n = 2
  while (taken.has(a.toUpperCase())) { a = `${base}${n}`; n += 1 }
  return a
}

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
interface PickedTable { schema: string | null; name: string; columns: PoolColumn[] }
// Lazy table picker — schema list → ONE schema's tables (name-filtered) → pick. A real schema needs
// a name filter before listing (no full-catalog dump); a single-/zero-schema pool (SQLite) lists
// directly. Used for the base FROM and each added JOIN.
function TablePicker({ connector, onPick }: { connector: string; onPick: (t: PickedTable) => void }) {
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
            placeholder={t('settings.sqlWizard.pickSchema', 'Schema')} />
        </div>
      )}
      {!noPicker && (
        <Input style={{ flex: '0 0 150px' }} placeholder={t('settings.sqlWizard.filterTable', 'Filter (e.g. F009%)')}
          value={filterRaw} onChange={(e) => setFilterRaw(e.target.value)} />
      )}
      <div style={{ flex: 1, minWidth: 160 }}>
        <SearchSelect value=""
          onChange={(v) => { const tb = (tables ?? []).find((x) => x.name === v); if (tb) onPick({ schema: noPicker ? null : pickedSchema, name: tb.name, columns: tb.columns }) }}
          options={tableOpts}
          placeholder={!noPicker && !filter ? t('settings.sqlWizard.typeFilter', 'Type a filter to list tables') : (tables === undefined ? t('common.loading') : t('settings.sqlWizard.pickTable'))}
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
  onInsert: (sql: string) => void
  onCancel: () => void
}

export function SqlWizardModal({ connector, initialSql, onInsert, onCancel }: SqlWizardModalProps) {
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
  const [readOnly, setReadOnly] = useState(false)
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
      <Modal style={{ width: 'min(960px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{t('settings.sqlWizard.title')}</ModalHeader>
        <ModalBody>
          {/* ── tables: base FROM + JOINs (each lazily picked) ── */}
          {!hasBase ? (
            <Field label={t('settings.sqlWizard.table')}>
              <TablePicker connector={connector} onPick={pickBase} />
            </Field>
          ) : (
            <Section>
              <SectionTitle>{t('settings.sqlWizard.tables', 'Tables')}</SectionTitle>
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
                          <OnLabel style={{ flex: 1, marginBottom: 0 }}>{t('settings.sqlWizard.joinOn', 'ON')}</OnLabel>
                          {!readOnly && (
                            <MiniBtn type="button" onClick={() => toggleJoinRaw(i)}>
                              {isRaw ? t('settings.sqlWizard.joinUseBuilder', 'Use builder') : t('settings.sqlWizard.joinEditText', 'Edit as text')}
                            </MiniBtn>
                          )}
                        </RowBar>
                        {readOnly ? (
                          <OnText>{j.join?.raw?.trim() || conds.filter((c) => c.left && c.right).map((c) => `${c.left.toUpperCase()} = ${c.right.toUpperCase()}`).join(' AND ')}</OnText>
                        ) : isRaw ? (
                          <Input style={{ marginTop: 4, fontFamily: fonts.mono }}
                            value={j.join?.raw ?? ''} onChange={(e) => setJoinRaw(i, e.target.value)}
                            placeholder={t('settings.sqlWizard.joinOnExpr', 'e.g. a.KEY = b.KEY AND TRIM(b.X) IS NULL')} />
                        ) : (<>
                          {conds.map((c, k) => (
                            <RowBar key={k} style={{ marginTop: 4 }}>
                              <div style={{ flex: 1 }}>
                                <SearchSelect value={c.left} onChange={(v) => setCond(k, { left: v })}
                                  options={leftOpts} placeholder={t('settings.sqlWizard.joinSource', 'Source column')} />
                              </div>
                              <span style={{ color: colors.text.muted }}>=</span>
                              <div style={{ flex: 1 }}>
                                <SearchSelect value={c.right} onChange={(v) => setCond(k, { right: v })}
                                  options={rightOpts} placeholder={t('settings.sqlWizard.joinTarget', 'Target column')} />
                              </div>
                              {conds.length > 1 && (
                                <SmallX type="button" title={t('common.remove')}
                                  onClick={() => setJoinConds(i, conds.filter((_, ci) => ci !== k))}><X size={13} /></SmallX>
                              )}
                            </RowBar>
                          ))}
                          <MiniBtn type="button" onClick={() => setJoinConds(i, [...conds, { left: '', right: '' }])}>
                            <Plus size={12} /> {t('settings.sqlWizard.addJoinCond', 'Add condition')}
                          </MiniBtn>
                        </>)}
                      </div>
                    )
                  })()}
                </div>
              ))}
              {!readOnly && (adding
                ? <TablePicker connector={connector} onPick={addJoin} />
                : <MiniBtn type="button" onClick={() => setAdding(true)}><Plus size={12} /> {t('settings.sqlWizard.addJoin', 'Add a joined table')}</MiniBtn>)}
            </Section>
          )}
          {readOnly && (
            <Banner $tone="warning" style={{ marginBottom: 12 }}>
              {t('settings.sqlWizard.readOnlyNote', "This query's SELECT list uses column aliases or expressions the builder can't edit. The joins are shown above for reference — edit the query directly in SQL.")}
            </Banner>
          )}
          {/* ── columns, grouped per table ── */}
          {!readOnly && hasBase && joins.map((j, ji) => (
            <Section key={`cols_${j.alias}_${ji}`}>
              <RowBar>
                <SectionTitle style={{ flex: 1 }}>
                  {(single ? t('settings.sqlWizard.columnsTitle', 'Columns') : j.alias)} — {j.columns.filter((c) => cols.has(`${j.alias}.${c.name}`)).length}/{j.columns.length}
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

// ─── multi-table FROM/JOIN round-trip (for re-opening a saved join query) ───────────────────────

interface ParsedTable { schema: string | null; name: string; alias: string; joinType?: JoinType; onText?: string }

/** Parse a SELECT's FROM/JOIN chain into an ordered table list (base first). Each entry keeps the
 *  schema EXPRESSION (a portable ``#SCHEMA.X#`` token preserved verbatim), the table name, the alias
 *  the query used (or the table name when none — JDE writes ON refs against the bare name), and for
 *  joins the type + the raw ON text. Returns ``null`` when the FROM isn't a plain table chain (comma
 *  joins, CROSS / FULL JOIN, a subquery in FROM, set ops) — the caller falls back to base-pick. */
function parseTableChain(sql: string): ParsedTable[] | null {
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
function parseTableSpec(text: string): ParsedTable | null {
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
function parseOn(onText: string, thisAlias: string, joins: JoinTable[]): { conds: JoinCond[]; raw?: string } {
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
function resolveColRef(ref: string, joins: JoinTable[]): string | null {
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
function parseMultiSelect(
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
function parseWhereClauseMulti(part: string, resolve: (r: string) => string | null): WhereRow | null {
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
function splitOnClause(text: string): { before: string; after: string } {
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
function splitTopLevelKeyword(text: string, kw: string): string[] {
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
function parenDepthsOf(text: string): number[] {
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
