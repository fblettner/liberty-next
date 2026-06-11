// Generate a lookup / sequence query straight from the dictionary record's params + a picked table.
// A LookupDef names its value / label / key columns; a SequenceDef names its MAX column (dd_id) +
// WHERE params — but those are OUTPUT ALIASES (the dd-side field codes), not the table's real column
// names (JDE prefixes every physical column, e.g. the DTAI alias lives in column FRDTAI). So the
// operator picks the table with the SAME lazy picker the SQL wizard uses, then maps each alias to a
// real column (auto-matched, prefix-aware). The SQL selects ``<column> <alias>`` so the result set
// carries the dd-side names the runtime expects. Editable preview (+ the Build-a-SELECT wizard).
//
// This modal does NOT write anything — it returns ``{ name, sql }``. The caller (DictionaryBuilder)
// hands that to EditQueryModal in create mode so the existing connectors write/reload path is reused.
//
// NOT re-exported from common/index.ts — direct import keeps it in the Settings chunk.
import { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Button, Field, Input, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, SearchSelect, type SearchSelectOption } from '../../common'
import { SqlConnectorContext } from '../../common/SchemaForm'
import { SqlEditor } from '../../common/SqlEditor'
import { TablePicker, type PickedTable } from '../../common/SqlWizardModal'
import { colors, fontSize, fonts } from '../../theme'

const Hint = styled.p`font-size: ${fontSize.sm}; color: ${colors.text.muted}; line-height: 1.45; margin: 0;`
const ErrorLine = styled.div`color: var(--text-error, #c0392b); font-size: ${fontSize.sm};`
const MapRow = styled.div`display: flex; gap: 8px; align-items: center; & + & { margin-top: 6px; }`
const MapLabel = styled.span`flex: 0 0 150px; font-size: ${fontSize.sm}; color: ${colors.text.secondary}; font-family: ${fonts.mono};`

export type GenerateKind = 'lookups' | 'sequences'

export interface GenerateQueryModalProps {
  kind: GenerateKind
  connector: string
  /** The LookupDef / SequenceDef record being edited — drives which params the SQL selects/filters. */
  record: Record<string, unknown>
  /** Existing query names on the connector — used to refuse a colliding new name. */
  existingQueryNames: ReadonlySet<string>
  onConfirm: (r: { name: string; sql: string }) => void
  onCancel: () => void
}

type Role = 'value' | 'label' | 'key' | 'max' | 'param'
interface Param { alias: string; role: Role }

const slugify = (s: string) => s.toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '')
const asStr = (v: unknown) => (typeof v === 'string' ? v.trim() : '')
const asList = (v: unknown) => (Array.isArray(v) ? v.map((x) => String(x).trim()).filter(Boolean) : [])

/** Best-effort default column for a dd alias against the table's columns: exact name, else a JDE-style
 *  prefixed column (``FR`` + ``DTAI`` → ``FRDTAI``: same length-2 prefix, ends with the alias), else any
 *  suffix / substring match. Empty when nothing looks right — the operator picks it. */
function autoMatch(alias: string, cols: { name: string }[]): string {
  const a = alias.toLowerCase()
  if (!a) return ''
  const lower = cols.map((c) => ({ name: c.name, l: c.name.toLowerCase() }))
  return (
    lower.find((c) => c.l === a)?.name
    ?? lower.find((c) => c.l.length === a.length + 2 && c.l.endsWith(a))?.name
    ?? lower.find((c) => c.l.endsWith(a))?.name
    ?? lower.find((c) => c.l.includes(a))?.name
    ?? ''
  )
}

export function GenerateQueryModal({ kind, connector, record, existingQueryNames, onConfirm, onCancel }: GenerateQueryModalProps) {
  const { t } = useTranslation()
  const isSequence = kind === 'sequences'

  // The dd aliases this record declares — each maps to a real table column below. The list fields are
  // memoised on ``record`` so they keep a stable reference across renders (``asList`` builds a fresh
  // array each call — an unstable dep would re-run the auto-map effect every render and wipe the
  // operator's column picks).
  const valueAlias = asStr(record.value)
  const labelAlias = asStr(record.label) || valueAlias
  const ddId = asStr(record.dd_id)
  const keyAliases = useMemo(() => asList(record.key_columns), [record])
  const seqParams = useMemo(() => asList(record.params), [record])

  // The ordered, de-duplicated param list driving both the mapping rows and the SQL.
  const params = useMemo<Param[]>(() => {
    const out: Param[] = isSequence
      ? [{ alias: ddId, role: 'max' }, ...seqParams.map((p): Param => ({ alias: p, role: 'param' }))]
      : [{ alias: valueAlias, role: 'value' }, { alias: labelAlias, role: 'label' }, ...keyAliases.map((k): Param => ({ alias: k, role: 'key' }))]
    const seen = new Set<string>()
    return out.filter((p) => p.alias && !seen.has(p.alias) && seen.add(p.alias))
  }, [isSequence, ddId, seqParams, valueAlias, labelAlias, keyAliases])

  const [picked, setPicked] = useState<PickedTable | null>(null)
  const [colMap, setColMap] = useState<Record<string, string>>({})   // alias -> table column name
  const [name, setName] = useState('')
  const [sqlOverride, setSqlOverride] = useState<string | null>(null)

  // Auto-map every alias to a column when the table is picked (prefix-aware). Resets the override.
  useEffect(() => {
    if (!picked) { setColMap({}); return }
    const next: Record<string, string> = {}
    for (const p of params) next[p.alias] = autoMatch(p.alias, picked.columns)
    setColMap(next)
    setSqlOverride(null)
  }, [picked, params])

  const setCol = (alias: string, col: string) => { setColMap((m) => ({ ...m, [alias]: col })); setSqlOverride(null) }

  // Auto-suggest a query name from the table (+ MAX column for sequences). Fills while untyped.
  const suggested = useMemo(() => {
    if (!picked) return ''
    const tbl = slugify(picked.name)
    return isSequence && ddId ? slugify(`get_${ddId}_from_${tbl}_get`) : slugify(`get_${tbl}_get`)
  }, [picked, isSequence, ddId])
  useEffect(() => { setName((cur) => cur || suggested) }, [suggested])

  // The generated SQL. ids upper-cased by convention; the schema EXPRESSION (a portable #SCHEMA.X#
  // token when the picker offered one) is preserved so the query stays env-portable. Each alias emits
  // ``<column> <alias>`` (aliasing the physical column to the dd field code); an unmapped alias falls
  // back to itself so the SQL is still a valid draft.
  const colOrAlias = (alias: string) => (colMap[alias] || alias).toUpperCase()
  const selectItem = (alias: string) => {
    const col = (colMap[alias] || '').toUpperCase()
    const a = alias.toUpperCase()
    return col && col !== a ? `${col} ${a}` : a
  }
  const generatedSql = useMemo(() => {
    if (!picked) return ''
    const fq = picked.schema ? `${picked.schema}.${picked.name}` : picked.name
    if (isSequence) {
      if (!ddId) return ''
      let sql = `SELECT COALESCE(MAX(${colOrAlias(ddId)}), 0) + 1 AS SEQ_VALUE\nFROM\n    ${fq}`
      if (seqParams.length) sql += `\nWHERE\n    ${seqParams.map((p) => `${colOrAlias(p)} = :${p}`).join('\n    AND ')}`
      return sql
    }
    if (!valueAlias) return ''
    const sel = Array.from(new Set([valueAlias, labelAlias])).map(selectItem)
    let sql = `SELECT\n    ${sel.join(',\n    ')}\nFROM\n    ${fq}`
    if (keyAliases.length) sql += `\nWHERE\n    ${keyAliases.map((k) => `${colOrAlias(k)} = :${k}`).join('\n    AND ')}`
    sql += `\nORDER BY\n    ${labelAlias.toUpperCase()}`
    return sql
    // colMap is read through the closures above — list it so the SQL regenerates on each mapping change.
  }, [picked, isSequence, ddId, seqParams, valueAlias, labelAlias, keyAliases, colMap])
  const effectiveSql = sqlOverride != null ? sqlOverride : generatedSql

  const missingField = isSequence
    ? (!ddId ? t('settings.generateQuery.needDdId', 'Set this sequence’s column (dd_id) first.') : null)
    : (!valueAlias ? t('settings.generateQuery.needValue', 'Set this lookup’s value column first.') : null)

  const error: string | null = (() => {
    if (missingField) return missingField
    if (!picked) return t('settings.generateQuery.pickTable', 'Pick a table to read from.')
    const n = name.trim()
    if (!n) return t('settings.generateQuery.nameRequired', 'Name the query.')
    if (existingQueryNames.has(n)) return t('settings.generateQuery.nameExists', 'A query named "{{name}}" already exists.', { name: n })
    if (!effectiveSql.trim()) return t('settings.generateQuery.sqlEmpty', 'The generated SQL is empty.')
    return null
  })()

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  const confirm = () => { if (!error) onConfirm({ name: name.trim(), sql: effectiveSql }) }

  const colOpts: SearchSelectOption[] = useMemo(
    () => (picked?.columns ?? []).map((c) => ({ value: c.name, label: c.name.toUpperCase(), mono: c.type ?? '' })),
    [picked],
  )
  const roleLabel = (r: Role) => t(`settings.generateQuery.role.${r}`, r)

  return createPortal(
    <Overlay style={{ zIndex: 950 }}>
      <Modal style={{ width: 'min(760px, 95vw)' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          <span>
            {isSequence
              ? t('settings.generateQuery.sequenceTitle', 'Generate sequence query')
              : t('settings.generateQuery.lookupTitle', 'Generate lookup query')} ·{' '}
            <span style={{ fontFamily: fonts.mono, color: colors.text.muted, fontWeight: 400 }}>[connectors.{connector}]</span>
          </span>
        </ModalHeader>
        <ModalBody>
          <Field label={t('settings.generateQuery.table', 'Table to read from')}>
            <TablePicker connector={connector} onPick={(p) => { setPicked(p); setName('') }} />
            {picked && (
              <Hint style={{ marginTop: 4 }}>{picked.schema ? `${picked.schema}.` : ''}{picked.name}</Hint>
            )}
          </Field>
          {/* One row per dd param — map the alias (the dd field code) to a real table column. */}
          <Field label={t('settings.generateQuery.mapColumns', 'Map each parameter to a column')}>
            {missingField ? (
              <Hint>{missingField}</Hint>
            ) : !picked ? (
              <Hint>{t('settings.generateQuery.pickTableFirst', 'Pick a table to map its columns.')}</Hint>
            ) : (
              params.map((p) => (
                <MapRow key={p.alias}>
                  <MapLabel>{roleLabel(p.role)}: {p.alias.toUpperCase()}</MapLabel>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <SearchSelect value={colMap[p.alias] ?? ''} onChange={(v) => setCol(p.alias, v)}
                      options={colOpts} placeholder={t('settings.generateQuery.column', 'Column…')} />
                  </div>
                </MapRow>
              ))
            )}
          </Field>
          <Field label={t('settings.generateQuery.queryName', 'Query name')}>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={suggested} />
          </Field>
          <Field label={t('settings.generateQuery.sqlPreview', 'Generated SQL — editable')}>
            <SqlConnectorContext.Provider value={connector}>
              <SqlEditor value={effectiveSql} rows={10} onChange={(v) => setSqlOverride(v)} connector={connector} />
            </SqlConnectorContext.Provider>
          </Field>
          {error && picked && <ErrorLine>{error}</ErrorLine>}
        </ModalBody>
        <ModalFooter>
          <Button $variant="ghost" $size="sm" onClick={onCancel}>{t('common.cancel')}</Button>
          <Button $variant="primary" $size="sm" onClick={confirm} disabled={!!error}>
            {t('settings.generateQuery.use', 'Use this query')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>,
    document.body,
  )
}
