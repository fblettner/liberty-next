// Scaffold modal for creating a *sequence* or *lookup* query from a table + columns. The
// operator picks a table (from the connector's live pool introspection), picks a column (key
// for sequences; value + label for lookups) plus an optional WHERE filter, sees a live SQL
// preview, names the query (auto-suggested from the table/column), and saves. The save
// hook receives the generated query + the matching dictionary entry shape so the caller can
// write both into the right places (connectors.toml + dictionary.toml) in one go.
//
// Why a scaffold and not a free-form SQL editor? v1's Liberty UI had this exact reverse-
// engineering for sequences and lookups (the "Get next id from APPS_ID column" of
// SETTINGS_APPLICATIONS-style scaffolding). Operators don't usually want to write
// ``SELECT COALESCE(MAX(<col>), 0) + 1 FROM <table>`` by hand — they want to tick the
// table + column and have the SQL appear. Same for a lookup's
// ``SELECT <value>, <label> FROM <table>``.
//
// NOT re-exported from common/index.ts — direct import, keeps it in the Settings chunk.
import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../common/Button'
import { Field, Input } from '../../common/Input'
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from '../../common/Modal'
import { SearchSelect, type SearchSelectOption } from '../../common/SearchSelect'
import { SqlEditor } from '../../common/SqlEditor'
import { SqlConnectorContext } from '../../common/SchemaForm'
import { getPoolSchema, findTable, type PoolSchema } from '../../services/poolSchema'
import { colors, fontSize, fonts } from '../../theme'

const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.45; margin: 0;`
const ErrorLine = styled.div`color: var(--text-error, #c0392b); font-size: ${fontSize.sm};`
const PreviewWrap = styled.div`min-height: 180px; display: flex; flex-direction: column;`

export type ScaffoldKind = 'sequence' | 'lookup'

export interface ScaffoldResult {
  /** The new query: ``{ name, sql, label? }`` — written into the connector's ``queries`` list. */
  query: { name: string; sql: string; label?: string }
  /** The new dictionary entry — written into ``dictionary.toml`` at the right scope. The
   *  caller picks the scope (shared vs per-connector); the entry itself just carries the
   *  fields. Sequence: ``{ query, description?, dd_id?, connector? }``. Lookup:
   *  ``{ query, value, label, description?, connector? }``. */
  dictEntry: Record<string, unknown>
  /** Stable id for the dictionary entry — the caller uses it as the new dict key. */
  dictId: string
}

export interface ScaffoldQueryModalProps {
  kind: ScaffoldKind
  connector: string
  /** Existing dict ids in the active scope — used to refuse a colliding new id. */
  existingDictIds: ReadonlySet<string>
  /** Existing query names on this connector — used to refuse a colliding new query name. */
  existingQueryNames: ReadonlySet<string>
  onSave: (result: ScaffoldResult) => void
  onCancel: () => void
}

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')

export function ScaffoldQueryModal({
  kind, connector, existingDictIds, existingQueryNames, onSave, onCancel,
}: ScaffoldQueryModalProps) {
  const { t } = useTranslation()

  // Pool introspection — same source as the SQL editor's autocomplete + the SELECT wizard.
  // ``null`` = the pool can't be inspected (404 / 403 / 502 — see services/poolSchema). The
  // dropdowns degrade to "no options"; the operator can still type a table name by hand if
  // we add a free-text fallback later (not yet — for now, scaffolding needs introspection).
  const [schema, setSchema] = useState<PoolSchema | null | undefined>(undefined)
  useEffect(() => {
    let cancelled = false
    getPoolSchema(connector).then((s) => { if (!cancelled) setSchema(s ?? null) })
    return () => { cancelled = true }
  }, [connector])

  // Selection state — drives the preview.
  const [tableName, setTableName] = useState<string>('')
  const [keyCol, setKeyCol] = useState<string>('')      // sequence: the column whose MAX we read
  const [valueCol, setValueCol] = useState<string>('')   // lookup: the value column
  const [labelCol, setLabelCol] = useState<string>('')   // lookup: the label column
  const [where, setWhere] = useState<string>('')          // optional WHERE clause body (sans the keyword)
  const [queryName, setQueryName] = useState<string>('')
  const [dictId, setDictId] = useState<string>('')
  const [description, setDescription] = useState<string>('')
  const [sqlOverride, setSqlOverride] = useState<string | null>(null)  // when the operator edits the preview directly

  const tableOpts: SearchSelectOption[] = useMemo(
    () => (schema?.tables ?? []).map((tbl) => ({
      value: tbl.name,
      label: tbl.schema ? `${tbl.schema}.${tbl.name}` : tbl.name,
    })),
    [schema],
  )
  const table = useMemo(() => schema ? findTable(schema, tableName) : undefined, [schema, tableName])
  const colOpts: SearchSelectOption[] = useMemo(
    () => (table?.columns ?? []).map((c) => ({
      value: c.name,
      label: c.name,
      mono: c.type ?? '',
    })),
    [table],
  )

  // ── default query/dict names — auto-suggested from the selected table/column ──────
  // Only set when the operator hasn't typed something themselves (so an explicit name isn't
  // clobbered by a later table/column change). ``slugify`` keeps names safe for TOML keys.
  const suggestedQuery = useMemo(() => {
    if (!tableName) return ''
    const tbl = slugify(tableName)
    if (kind === 'sequence' && keyCol) return slugify(`get_${keyCol}_from_${tbl}_get`)
    if (kind === 'lookup') return slugify(`get_${tbl}_get`)
    return ''
  }, [kind, tableName, keyCol])
  const suggestedDictId = useMemo(() => {
    if (!tableName) return ''
    const tbl = slugify(tableName)
    if (kind === 'sequence' && keyCol) return slugify(`get_${keyCol}_from_${tbl}`)
    if (kind === 'lookup') return slugify(`get_${tbl}`)
    return ''
  }, [kind, tableName, keyCol])
  useEffect(() => {
    setQueryName((cur) => cur || suggestedQuery)
    setDictId((cur) => cur || suggestedDictId)
  }, [suggestedQuery, suggestedDictId])
  useEffect(() => {
    if (description) return
    if (kind === 'sequence' && keyCol && tableName) setDescription(`Get ${keyCol} from ${tableName}`)
    else if (kind === 'lookup' && tableName) setDescription(`Get ${tableName}`)
  }, [kind, tableName, keyCol])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── generated SQL — preview + the actual saved value. The operator can also click into
  // the editor and tweak it; ``sqlOverride`` then wins over the auto-generated preview.
  const generatedSql = useMemo(() => {
    if (!tableName) return ''
    const fqTable = table?.schema ? `${table.schema}.${tableName}` : tableName
    const whereClause = where.trim() ? `\nWHERE\n  ${where.trim()}` : ''
    if (kind === 'sequence') {
      if (!keyCol) return ''
      return `SELECT COALESCE(MAX(${keyCol}), 0) + 1 AS NEXT_ID\nFROM ${fqTable}${whereClause}`
    }
    // lookup — emit `SELECT value, label FROM table [WHERE ...]`.
    const cols = valueCol === labelCol || !labelCol ? valueCol : `${valueCol}, ${labelCol}`
    if (!valueCol) return ''
    return `SELECT ${cols}\nFROM ${fqTable}${whereClause}\nORDER BY ${labelCol || valueCol}`
  }, [kind, tableName, table, keyCol, valueCol, labelCol, where])
  const effectiveSql = sqlOverride != null ? sqlOverride : generatedSql

  // ── validation — surface errors inline, gate the Save button.
  const error: string | null = (() => {
    if (!tableName) return t('settings.scaffold.errors.tableRequired', 'Pick a table.')
    if (kind === 'sequence' && !keyCol) return t('settings.scaffold.errors.columnRequired', 'Pick a column.')
    if (kind === 'lookup' && !valueCol) return t('settings.scaffold.errors.valueColRequired', 'Pick a value column.')
    if (!queryName.trim()) return t('settings.scaffold.errors.queryNameRequired', 'Name the query.')
    if (!dictId.trim()) return t('settings.scaffold.errors.dictIdRequired', 'Name the dictionary entry.')
    if (existingQueryNames.has(queryName.trim())) {
      return t('settings.scaffold.errors.queryNameExists', 'A query named "{{name}}" already exists.', { name: queryName.trim() })
    }
    if (existingDictIds.has(dictId.trim())) {
      return t('settings.scaffold.errors.dictIdExists', 'A {{kind}} with id "{{id}}" already exists in this scope.', { kind, id: dictId.trim() })
    }
    if (!effectiveSql.trim()) return t('settings.scaffold.errors.sqlEmpty', 'The generated SQL is empty — fill in the fields above.')
    return null
  })()

  // Escape closes — same convention as the rest of the app's modals.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  const handleSave = () => {
    if (error) return
    const name = queryName.trim()
    const id = dictId.trim()
    const desc = description.trim()
    const query: ScaffoldResult['query'] = { name, sql: effectiveSql }
    if (desc) query.label = desc
    const dictEntry: Record<string, unknown> = { query: name }
    if (desc) dictEntry.description = desc
    if (kind === 'sequence' && keyCol) dictEntry.dd_id = keyCol
    if (kind === 'lookup') {
      dictEntry.value = valueCol
      dictEntry.label = labelCol || valueCol
    }
    onSave({ query, dictEntry, dictId: id })
  }

  const titleText = kind === 'sequence'
    ? t('settings.scaffold.sequence.title', 'New sequence')
    : t('settings.scaffold.lookup.title', 'New lookup')

  const modalNode: ReactNode = (
    <Overlay onClick={onCancel}>
      <Modal style={{ width: 'min(720px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          <span>
            {titleText} ·{' '}
            <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontWeight: 400 }}>
              [connectors.{connector}]
            </span>
          </span>
        </ModalHeader>
        <ModalBody>
          {schema === undefined && (
            <Hint>{t('common.loading')}</Hint>
          )}
          {schema === null && (
            <ErrorLine>
              {t('settings.scaffold.errors.noSchema', "Couldn't introspect this connector's pool. Make sure it's reachable (a 502 here usually means the pool URL is wrong) and you have superuser rights.")}
            </ErrorLine>
          )}
          {schema && schema.tables.length === 0 && (
            <Hint>{t('settings.scaffold.errors.noTables', 'No tables in the introspected schema. Check the pool config.')}</Hint>
          )}
          {schema && schema.tables.length > 0 && (
            <>
              <Field label={t('settings.scaffold.table', 'Table')}>
                <SearchSelect
                  value={tableName}
                  onChange={(v) => { setTableName(v); setKeyCol(''); setValueCol(''); setLabelCol(''); setSqlOverride(null); setQueryName(''); setDictId(''); setDescription('') }}
                  options={tableOpts}
                  placeholder={t('common.pick')}
                />
              </Field>
              {kind === 'sequence' && (
                <Field label={t('settings.scaffold.sequence.keyCol', 'Key column (MAX returns the next +1)')}>
                  <SearchSelect
                    value={keyCol}
                    onChange={(v) => { setKeyCol(v); setSqlOverride(null) }}
                    options={colOpts}
                    placeholder={t('common.pick')}
                  />
                </Field>
              )}
              {kind === 'lookup' && (
                <>
                  <Field label={t('settings.scaffold.lookup.valueCol', 'Value column (the code stored on the row)')}>
                    <SearchSelect
                      value={valueCol}
                      onChange={(v) => { setValueCol(v); setSqlOverride(null) }}
                      options={colOpts}
                      placeholder={t('common.pick')}
                    />
                  </Field>
                  <Field label={t('settings.scaffold.lookup.labelCol', 'Label column (the description shown to the user)')}>
                    <SearchSelect
                      value={labelCol}
                      onChange={(v) => { setLabelCol(v); setSqlOverride(null) }}
                      options={colOpts}
                      anyLabel={t('settings.scaffold.lookup.sameAsValue', 'Same as value column')}
                      placeholder={t('common.pick')}
                    />
                  </Field>
                </>
              )}
              <Field label={t('settings.scaffold.where', 'Optional WHERE clause (without the keyword)')}>
                <Input
                  value={where}
                  onChange={(e) => { setWhere(e.target.value); setSqlOverride(null) }}
                  placeholder={t('settings.scaffold.wherePlaceholder', 'e.g. ULUGRP != \'*GROUP\'')}
                />
              </Field>
              <Field label={t('settings.scaffold.queryName', 'Query name')}>
                <Input value={queryName} onChange={(e) => setQueryName(e.target.value)} placeholder={suggestedQuery} />
              </Field>
              <Field label={t('settings.scaffold.dictId', kind === 'sequence' ? 'Sequence id' : 'Lookup id')}>
                <Input value={dictId} onChange={(e) => setDictId(e.target.value)} placeholder={suggestedDictId} />
              </Field>
              <Field label={t('settings.scaffold.description', 'Description')}>
                <Input value={description} onChange={(e) => setDescription(e.target.value)} placeholder={t('settings.scaffold.descriptionPlaceholder', 'Shown in listings + tooltips')} />
              </Field>
              <Field label={t('settings.scaffold.sqlPreview', 'Generated SQL — editable')}>
                <PreviewWrap>
                  <SqlConnectorContext.Provider value={connector}>
                    <SqlEditor value={effectiveSql} onChange={(v) => setSqlOverride(v)} />
                  </SqlConnectorContext.Provider>
                </PreviewWrap>
              </Field>
              {error && <ErrorLine>{error}</ErrorLine>}
            </>
          )}
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" $size="sm" onClick={onCancel}>
            <X size={13} /> {t('common.cancel')}
          </Button>
          <Button $variant="primary" $size="sm" onClick={handleSave} disabled={!!error || !schema}>
            {t('common.save')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
  return createPortal(modalNode, document.body)
}
