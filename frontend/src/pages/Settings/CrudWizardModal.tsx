// Reverse-engineer a table from the DB into all four CRUD queries (``_get`` / ``_post`` /
// ``_put`` / ``_delete``). v1 had this — operators routinely build screens against existing
// tables, and writing the four queries by hand (with the ``:NAME`` / ``:NAME_ORIGINAL``
// conventions correctly applied to the ``_put``'s WHERE clause) is busywork. The wizard:
//
//   1. Reads the connector's live pool schema (``GET /api/sql/<c>/_schema``)
//   2. Operator picks a table + ticks which columns are the primary key
//   3. Optionally excludes columns the SELECT shouldn't return (audit columns, blobs, …)
//   4. Live preview of all four generated queries
//   5. Save → appends the four queries to the connector
//
// Each query's name follows the v1 convention so the existing Tables-view grouping picks them
// up: ``<base>_get`` / ``_put`` / ``_post`` / ``_delete``. ``_put`` rebinds key columns to
// ``:NAME_ORIGINAL`` (the same rebind the TableView's inline-edit and the v1 migration's
// ``_rewrite_put_where`` emit), so the dialog's Save knows how to identify the row.
//
// NOT re-exported from common/index.ts — direct import keeps it in the Settings chunk.
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../common/Button'
import { Checkbox } from '../../common/Checkbox'
import { Field, Input } from '../../common/Input'
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from '../../common/Modal'
import { SearchSelect, type SearchSelectOption } from '../../common/SearchSelect'
import { buildCrudSql } from '../../common/sqlBuild'
import { SqlEditor } from '../../common/SqlEditor'
import { SqlConnectorContext } from '../../common/SchemaForm'
import { getPoolSchemaNames, getPoolSchemaTables, findTable, type PoolSchema, type PoolTable } from '../../services/poolSchema'
import { colors, fontSize, fonts, radius } from '../../theme'

const Section = styled.div`display: flex; flex-direction: column; gap: 6px;`
const SectionTitle = styled.div`
  display: flex; align-items: center; gap: 8px;
  font-size: ${fontSize.micro}; color: ${colors.text.muted};
  text-transform: uppercase; letter-spacing: 0.05em;
  & .count { text-transform: none; letter-spacing: 0; color: ${colors.text.muted}; font-family: ${fonts.mono}; }
`
// Two-column grid of column rows — each cell holds a full row (include + name + type on the
// left, "key" toggle pinned to the right). Wider min-width (340px) so long names like
// ``xref_description_from`` + a VARCHAR(100) type fit without overlapping the key toggle.
const ColGrid = styled.div`
  display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 4px 16px; max-height: 260px; overflow-y: auto; padding: 8px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
`
// A column row laid out so the *name + type* stretches to fill, the *key toggle* anchors right
// behind a subtle vertical divider. Hovering the row tints the background so the operator can
// scan a long list. The whole row is clickable as a label for the include checkbox; the inner
// ``key`` block stops propagation so ticking it doesn't toggle include.
const ColRow = styled.label`
  display: flex; align-items: center; gap: 8px;
  padding: 5px 8px; border-radius: ${radius.sm}; cursor: pointer;
  & .name { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; flex-shrink: 0; }
  & .type { font-family: ${fonts.mono}; font-size: ${fontSize.micro}; color: ${colors.text.muted}; flex-shrink: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .spacer { flex: 1 1 auto; min-width: 8px; }
  & .keyBox {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 6px; border-left: 1px solid ${colors.border}; flex-shrink: 0;
  }
  & .keyBox .lbl { color: ${colors.orange.main}; font-size: ${fontSize.micro}; text-transform: uppercase; letter-spacing: 0.05em; }
  &:hover { background: var(--hover-subtle, transparent); }
`
// Compact horizontal CRUD opt-out row — each item is a small pill so the operator can scan
// "what's emitted" at a glance.
const EmitRow = styled.div`display: flex; gap: 12px; flex-wrap: wrap;`
const EmitItem = styled.label`
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: ${radius.md};
  border: 1px solid ${colors.border}; background: ${colors.bg.input};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  cursor: pointer;
  & .suffix { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  &.required { opacity: 0.7; cursor: default; }
`
// Each SQL preview gets its own card so the previews don't blur into one tall block.
const PreviewCard = styled.div`
  display: flex; flex-direction: column; gap: 6px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; padding: 10px 12px;
  background: var(--bg-modal, transparent);
`
const PreviewHead = styled.div`
  display: flex; align-items: center; gap: 8px;
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
  & .crud { padding: 1px 6px; border-radius: ${radius.sm}; background: ${colors.blue.bg}; color: ${colors.blue.main}; font-size: ${fontSize.micro}; letter-spacing: 0.05em; }
  & .name { color: ${colors.text.muted}; }
`
const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.45; margin: 0;`
const ErrorLine = styled.div`color: var(--text-error, #c0392b); font-size: ${fontSize.sm};`
const PreviewWrap = styled.div`min-height: 140px; display: flex; flex-direction: column;`

// One CRUD slot in the preview/grid. ``include = false`` skips it (the wizard always emits
// ``_get`` + at least one of the others, but a read-only table can opt out of writes).
type CrudKey = 'get' | 'put' | 'post' | 'delete'
const CRUD_LABELS: Record<CrudKey, string> = {
  get: 'SELECT', put: 'UPDATE', post: 'INSERT', delete: 'DELETE',
}

export interface CrudWizardResult {
  /** The four (or fewer) queries to append to the connector's ``queries`` list. */
  queries: Array<{ name: string; sql: string; writable?: boolean; label?: string }>
  /** The reversed table + its schema — so the caller can chain the dictionary-item scan. */
  table: string
  schema?: string
}

export interface CrudWizardModalProps {
  connector: string
  /** Existing query names in this connector — prevents duplicating an existing table. */
  existingQueryNames: ReadonlySet<string>
  onSave: (result: CrudWizardResult) => void
  onCancel: () => void
}

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')

// CRUD-statement generation lives in the shared builder so the per-slot SQL wizard scaffolds the
// identical statement (see common/sqlBuild.ts).
const buildSql = buildCrudSql

export function CrudWizardModal({
  connector, existingQueryNames, onSave, onCancel,
}: CrudWizardModalProps) {
  const { t } = useTranslation()

  // Two-step lazy introspection — the wizard pulls the schema list first (fast even on Oracle:
  // a single ``ALL_USERS`` query), then pulls tables for the picked schema on demand. Avoids
  // the full multi-schema catalog walk on open (which on JDE's 6-schema pool takes 10+ seconds
  // and the operator would just be waiting on data they don't need yet).
  //
  // ``schemasInfo`` = the schema-name list (+ dialect / pool). ``undefined`` while loading,
  // ``null`` on a 403 / 502 / 404, otherwise the resolved payload. ``schema`` here is the
  // *tables-of-the-picked-schema* fetch result, also lazily-resolved per pick.
  const [schemasInfo, setSchemasInfo] = useState<{ pool: string; dialect: string; schemas: string[] } | null | undefined>(undefined)
  const [schema, setSchema] = useState<PoolSchema | null | undefined>(undefined)
  useEffect(() => {
    let cancelled = false
    getPoolSchemaNames(connector).then((s) => { if (!cancelled) setSchemasInfo(s ?? null) })
    return () => { cancelled = true }
  }, [connector])

  // Schema picker — Oracle pools enumerate every non-system schema (often hundreds of tables
  // total), so we narrow the table list to one schema at a time. PostgreSQL / SQLite usually
  // only have one user schema, in which case the picker hides itself and the table list shows
  // every table directly. Cleared when the operator switches connector.
  const [pickedSchema, setPickedSchema] = useState<string>('')
  // Optional SQL-LIKE-style name filter (``F009%``). Applied server-side BEFORE the per-table
  // column walk — the wizard skips its column fetch entirely for the dropped names, which is
  // the wall-clock-expensive part. A debounce avoids hammering the backend while typing.
  const [nameLikeRaw, setNameLikeRaw] = useState<string>('')
  const [nameLike, setNameLike] = useState<string>('')
  useEffect(() => {
    const handle = setTimeout(() => setNameLike(nameLikeRaw.trim()), 350)
    return () => clearTimeout(handle)
  }, [nameLikeRaw])
  const [tableName, setTableName] = useState<string>('')
  const [baseName, setBaseName] = useState<string>('')
  // Column inclusion: every column ticked goes into SELECT / INSERT lists. Defaults to all
  // columns on first table pick — operator unticks blobs / huge columns they don't want in
  // SELECT, etc. The wizard always emits ``_get`` if at least one column is included.
  const [included, setIncluded] = useState<Set<string>>(new Set())
  // Key columns: drive the UPDATE / DELETE WHERE clauses + the ``_put``'s ``:NAME_ORIGINAL``
  // rebind. Operator picks at least one before _put / _delete can emit.
  const [keys, setKeys] = useState<Set<string>>(new Set())
  // Per-CRUD opt-out — a read-only view doesn't want _put / _post / _delete.
  const [emit, setEmit] = useState<Record<CrudKey, boolean>>({ get: true, put: true, post: true, delete: true })
  // Per-CRUD SQL override — once the operator edits the preview, that wins over the auto-gen.
  const [sqlOverride, setSqlOverride] = useState<Partial<Record<CrudKey, string>>>({})

  // Schema picker options come from the lightweight ``_schemas`` endpoint (already loaded
  // by the time this memo runs; ``undefined`` during the initial fetch means an empty list
  // until the response lands).
  const schemaOpts: SearchSelectOption[] = useMemo(() => {
    if (!schemasInfo) return []
    return [...schemasInfo.schemas].sort().map((s) => ({ value: s, label: s, mono: s }))
  }, [schemasInfo])

  // Default schema on first load — pick the only one when there's a single schema; mark the
  // wizard "no-picker" when the pool has zero schemas (SQLite-style). For multi-schema pools
  // leave empty so the operator must choose (avoids a giant table list dump on Oracle).
  useEffect(() => {
    if (!schemasInfo || pickedSchema) return
    if (schemaOpts.length === 1) setPickedSchema(schemaOpts[0].value)
    else if (schemaOpts.length === 0) setPickedSchema('__nopicker__')
  }, [schemasInfo, schemaOpts, pickedSchema])

  // Fetch tables for the picked schema. ``__nopicker__`` falls back to the unscoped walk
  // (typical SQLite case — single schema, just enumerate everything). Reset to ``undefined``
  // (= loading) when the operator switches schemas OR the debounced name filter changes.
  useEffect(() => {
    if (!pickedSchema) { setSchema(undefined); return }
    // For a real (picker-active) schema, wait for a filter before fetching — a blank filter on a
    // multi-schema Oracle/JDE pool walks hundreds of tables' columns. The operator types a pattern
    // (``%`` for all) like the SQL wizard. The single-schema ``__nopicker__`` case has nothing to
    // narrow, so fetch immediately.
    if (pickedSchema !== '__nopicker__' && !nameLike) { setSchema(null); return }
    let cancelled = false
    setSchema(undefined)
    const arg = pickedSchema === '__nopicker__' ? null : pickedSchema
    getPoolSchemaTables(connector, arg, nameLike || null).then((s) => { if (!cancelled) setSchema(s ?? null) })
    return () => { cancelled = true }
  }, [connector, pickedSchema, nameLike])
  // Clear the typed filter when the schema changes — a pattern that fits SY920's F0%
  // tables makes no sense for CTL920's tables.
  useEffect(() => { setNameLikeRaw(''); setNameLike('') }, [pickedSchema])

  // The tables list — the lazy fetch already returns only the picked schema's tables, so no
  // client-side filter is needed; just project to SearchSelect options.
  const tableOpts: SearchSelectOption[] = useMemo(
    () => (schema?.tables ?? []).map((tbl) => ({
      value: tbl.name,
      // Drop the ``<schema>.`` prefix when the schema picker is active — it's redundant once
      // the operator already filtered. Keep it when there's no picker (SQLite-style with a
      // schema-shaped catalog) so the qualifier still surfaces.
      label: pickedSchema === '__nopicker__' && tbl.schema ? `${tbl.schema}.${tbl.name}` : tbl.name,
    })),
    [schema, pickedSchema],
  )
  // Resolve the picked table from the (already-schema-scoped) tables list. The lazy fetch
  // only returns the picked schema's tables, so a bare ``findTable`` is unambiguous —
  // disambiguation by schema isn't needed here.
  const table: PoolTable | undefined = useMemo(
    () => schema ? findTable(schema, tableName) : undefined,
    [schema, tableName],
  )
  // Pre-fill included = every column when the table changes; pre-fill keys with any column
  // marked ``nullable: false`` on the introspection side (close enough to a PK guess).
  useEffect(() => {
    if (!table) { setIncluded(new Set()); setKeys(new Set()); setBaseName(''); return }
    setIncluded(new Set(table.columns.map((c) => c.name)))
    setKeys(new Set(table.columns.filter((c) => c.nullable === false).map((c) => c.name)))
    setBaseName((cur) => cur || slugify(tableName))
    setSqlOverride({})
  }, [table, tableName])

  // ── generated SQL — preview + the actual saved value ──
  const cruds: CrudKey[] = ['get', 'put', 'post', 'delete']
  const generated: Record<CrudKey, string> = useMemo(() => ({
    get: buildSql({ crud: 'get', schema: table?.schema ?? null, table: table?.name ?? '', selectCols: Array.from(included), keyCols: Array.from(keys) }),
    put: buildSql({ crud: 'put', schema: table?.schema ?? null, table: table?.name ?? '', selectCols: Array.from(included), keyCols: Array.from(keys) }),
    post: buildSql({ crud: 'post', schema: table?.schema ?? null, table: table?.name ?? '', selectCols: Array.from(included), keyCols: Array.from(keys) }),
    delete: buildSql({ crud: 'delete', schema: table?.schema ?? null, table: table?.name ?? '', selectCols: Array.from(included), keyCols: Array.from(keys) }),
  }), [table, included, keys])
  const effective = (c: CrudKey) => sqlOverride[c] != null ? sqlOverride[c]! : generated[c]

  // ── validation ──
  const error: string | null = (() => {
    if (!tableName) return t('settings.crudWizard.errors.tableRequired', 'Pick a table.')
    if (!baseName.trim()) return t('settings.crudWizard.errors.baseRequired', 'Name the table.')
    if (included.size === 0) return t('settings.crudWizard.errors.noCols', 'Include at least one column.')
    if (!emit.get) return t('settings.crudWizard.errors.getRequired', 'The read (_get) query is required.')
    if ((emit.put || emit.delete) && keys.size === 0) {
      return t('settings.crudWizard.errors.keysRequired', 'Pick at least one key column for UPDATE / DELETE — they drive the WHERE clause.')
    }
    // Per-CRUD name collisions
    const toEmit = cruds.filter((c) => emit[c] && effective(c).trim().length > 0)
    for (const c of toEmit) {
      const name = `${slugify(baseName)}_${c}`
      if (existingQueryNames.has(name)) {
        return t('settings.crudWizard.errors.queryNameExists', 'A query named "{{name}}" already exists.', { name })
      }
    }
    return null
  })()

  // Escape closes.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  const handleSave = () => {
    if (error || !table) return
    const baseSlug = slugify(baseName)
    const queries: CrudWizardResult['queries'] = []
    for (const c of cruds) {
      if (!emit[c]) continue
      const sql = effective(c).trim()
      if (!sql) continue
      const q: CrudWizardResult['queries'][number] = { name: `${baseSlug}_${c}`, sql }
      if (c !== 'get') q.writable = true
      queries.push(q)
    }
    if (queries.length === 0) return
    onSave({ queries, table: tableName, schema: pickedSchema && pickedSchema !== '__nopicker__' ? pickedSchema : undefined })
  }

  const toggleIn = (name: string, on: boolean) => {
    setIncluded((prev) => {
      const next = new Set(prev)
      if (on) next.add(name); else { next.delete(name); /* unticking also drops the key flag */ }
      return next
    })
    if (!on) setKeys((prev) => {
      if (!prev.has(name)) return prev
      const next = new Set(prev); next.delete(name); return next
    })
    setSqlOverride({})
  }
  const toggleKey = (name: string, on: boolean) => {
    setKeys((prev) => {
      const next = new Set(prev)
      if (on) next.add(name); else next.delete(name)
      return next
    })
    setSqlOverride({})
  }

  // No backdrop-click-to-close — outside clicks must not discard wizard progress (Cancel / Escape).
  const modalNode: ReactNode = (
    <Overlay>
      <Modal style={{ width: 'min(820px, 95vw)', height: 'min(720px, 90vh)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          <span>
            {t('settings.crudWizard.title', 'Generate table from DB')} ·{' '}
            <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontWeight: 400 }}>
              [connectors.{connector}]
            </span>
          </span>
        </ModalHeader>
        <ModalBody>
          {/* Loading / error states for the two-step lazy fetch. ``schemasInfo`` is the first
              step (schema names); ``schema`` is the second (tables of the picked schema). The
              user sees "Loading schemas…" briefly on open, then the schema picker. After they
              pick, a "Loading tables…" message replaces the table picker until that fetch
              resolves. Most pools resolve in <100ms; Oracle with many schemas / tables can
              take a couple of seconds per schema — much less than the 10+s full walk. */}
          {schemasInfo === undefined && <Hint>{t('settings.crudWizard.loadingSchemas', 'Loading schemas…')}</Hint>}
          {schemasInfo === null && (
            <ErrorLine>
              {t('settings.scaffold.errors.noSchema', "Couldn't introspect this connector's pool. Make sure it's reachable and you have superuser rights.")}
            </ErrorLine>
          )}
          {schemasInfo && (
            <>
              {/* Schema picker — hidden when the pool has 0 or 1 user schemas (typical for
                  PostgreSQL / SQLite). Mandatory first step on Oracle so the table list
                  doesn't dump every owner's tables at once (hundreds of entries → laggy
                  SearchSelect). Switching the schema clears the table selection and triggers
                  a fresh tables fetch. */}
              {schemaOpts.length > 1 && (
                <Field label={t('settings.crudWizard.schema', 'Schema')}>
                  <SearchSelect
                    value={pickedSchema && pickedSchema !== '__nopicker__' ? pickedSchema : ''}
                    onChange={(v) => { setPickedSchema(v); setTableName('') }}
                    options={schemaOpts}
                    placeholder={t('common.pick')}
                  />
                </Field>
              )}
              {/* Name filter — debounced free-text input, required before tables load on a
                  multi-schema pool. ``F009%`` style patterns are applied server-side BEFORE the
                  per-table column walk (the slow step), so a 2000-table SY920 narrowed to a F009%
                  prefix returns under a second. Type ``%`` to list every table in the schema. */}
              {pickedSchema && pickedSchema !== '__nopicker__' && (
                <Field label={t('settings.crudWizard.nameFilter', 'Table name filter')}>
                  <Input
                    value={nameLikeRaw}
                    onChange={(e) => { setNameLikeRaw(e.target.value); setTableName('') }}
                    placeholder={t('settings.crudWizard.nameFilterPlaceholder', 'e.g. F009%, USR_%, % for all')}
                  />
                </Field>
              )}
              <Field label={t('settings.crudWizard.table', 'Table')}>
                <SearchSelect
                  value={tableName}
                  onChange={(v) => setTableName(v)}
                  options={tableOpts}
                  placeholder={!pickedSchema && schemaOpts.length > 1
                    ? t('settings.crudWizard.pickSchemaFirst', 'Pick a schema first')
                    : pickedSchema && pickedSchema !== '__nopicker__' && !nameLike
                      ? t('settings.crudWizard.typeFilter', 'Type a filter to list tables')
                      : schema === undefined && pickedSchema
                        ? t('settings.crudWizard.loadingTables', 'Loading tables…')
                        : t('common.pick')}
                  disabled={!pickedSchema && schemaOpts.length > 1}
                  loading={schema === undefined && !!pickedSchema}
                />
              </Field>
              {schema === null && (
                <ErrorLine>
                  {t('settings.scaffold.errors.noSchema', "Couldn't introspect this connector's pool. Make sure it's reachable and you have superuser rights.")}
                </ErrorLine>
              )}
              {schema && schema.tables.length === 0 && pickedSchema && (
                <Hint>{t('settings.scaffold.errors.noTables', 'No tables in the introspected schema. Check the pool config.')}</Hint>
              )}
              {table && (
                <>
                  <Field label={t('settings.crudWizard.baseName', 'Base name (queries become <base>_get / _put / _post / _delete)')}>
                    <Input value={baseName} onChange={(e) => setBaseName(e.target.value)} placeholder={slugify(tableName)} />
                  </Field>
                  <Section>
                    <SectionTitle>
                      {t('settings.crudWizard.colsTitle', 'Columns')}
                      <span className="count">
                        ({included.size} / {table.columns.length} included · {keys.size} key)
                      </span>
                    </SectionTitle>
                    <Hint>{t('settings.crudWizard.colsHint', 'Tick which columns to include in SELECT / INSERT. Tick the small "key" checkbox for the row\'s primary key columns — they drive the UPDATE / DELETE WHERE clause.')}</Hint>
                    <ColGrid>
                      {table.columns.map((c) => {
                        const inIn = included.has(c.name)
                        const isKey = keys.has(c.name)
                        return (
                          <ColRow key={c.name}>
                            <Checkbox checked={inIn} onChange={(v) => toggleIn(c.name, v)} />
                            <span className="name">{c.name}</span>
                            {c.type && <span className="type">{c.type}</span>}
                            <span className="spacer" />
                            <span className="keyBox" onClick={(e) => e.preventDefault()}>
                              <Checkbox checked={isKey} onChange={(v) => toggleKey(c.name, v)} disabled={!inIn} />
                              <span className="lbl">{t('settings.crudWizard.key', 'key')}</span>
                            </span>
                          </ColRow>
                        )
                      })}
                    </ColGrid>
                  </Section>
                  <Section>
                    <SectionTitle>{t('settings.crudWizard.emitTitle', 'Which queries to emit')}</SectionTitle>
                    <EmitRow>
                      {cruds.map((c) => (
                        <EmitItem key={c} className={c === 'get' ? 'required' : ''}>
                          <Checkbox checked={emit[c]} onChange={(v) => setEmit((p) => ({ ...p, [c]: v }))} disabled={c === 'get'} />
                          <span>{CRUD_LABELS[c]}</span>
                          <span className="suffix">(_{c})</span>
                        </EmitItem>
                      ))}
                    </EmitRow>
                  </Section>
                  {cruds.filter((c) => emit[c] && effective(c).trim().length > 0).map((c) => (
                    <PreviewCard key={c}>
                      <PreviewHead>
                        <span className="crud">{CRUD_LABELS[c]}</span>
                        <span className="name">{slugify(baseName)}_{c}</span>
                      </PreviewHead>
                      <PreviewWrap>
                        <SqlConnectorContext.Provider value={connector}>
                          <SqlEditor value={effective(c)} onChange={(v) => setSqlOverride((p) => ({ ...p, [c]: v }))} />
                        </SqlConnectorContext.Provider>
                      </PreviewWrap>
                    </PreviewCard>
                  ))}
                  {error && <ErrorLine>{error}</ErrorLine>}
                </>
              )}
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" $size="sm" onClick={onCancel}>
            <X size={13} /> {t('common.cancel')}
          </Button>
          <Button $variant="primary" $size="sm" onClick={handleSave} disabled={!!error || !schema || !table}>
            {t('common.save')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
  return createPortal(modalNode, document.body)
}
