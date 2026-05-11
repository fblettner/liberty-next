// Builds the column definitions for a SELECT result and hands them to the shared
// DataTable (sort / filter / search / group / hide-reorder / resize / export / paginate),
// plus owns the **inline-edit mode** when the query has an `update_query` companion:
// an Edit toggle in the toolbar, a leftmost actions column with a per-row pencil →
// Save / Cancel, and editable cell controls (text / number / date input, a select for
// enums) for the row being edited; Save POSTs the row to the update query and refetches.
//
// Per-column display rules (BOOLEAN / ENUM / LOOKUP — the v2 form of v1's dd_rules) are
// applied per cell via services/cells.ruleCell; a LOOKUP column is split into "<label> (ID)"
// (the raw code, editable) + "<label>" (the resolved label, derived & read-only); lookup
// queries are fetched once via services/lookups.useLookupBatch. Each column also declares
// its filter kind so DataTable's per-column filter row shows the right control.
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import type { ColumnDef, VisibilityState } from '@tanstack/react-table'
import styled from '@emotion/styled'
import { Pencil, Check, X } from 'lucide-react'
import type { Column, QueryResult } from '../../types/connectors'
import { api, ApiError } from '../../api/client'
import { Banner } from '../../common'
import { DataTable } from '../../common/DataTable'
import { genericFilterFn, type FilterKind, type FilterMeta } from '../../common/DataTableFilter'
import { enumMap, ruleCell } from '../../services/cells'
import { lookupKey, useLookupBatch, type LookupSpec } from '../../services/lookups'
import { colors, fontSize, fonts, radius } from '../../theme'
import { CellSpan } from './styled'

type DataRow = Record<string, unknown>
type Align = CSSProperties['textAlign']

function colHeader(c: Column): string {
  return c.label ?? c.name
}
function cellAlign(c: Column): Align {
  return c.align === 'left' || c.align === 'right' || c.align === 'center' ? c.align : undefined
}
function isNumericish(fmt: string, typ: string): boolean {
  return fmt === 'number' || fmt === 'integer' || /int|numeric|decimal|float|double|real/.test(typ)
}
function isDateish(fmt: string, typ: string): boolean {
  return fmt === 'date' || /date|timestamp/.test(typ)
}
function filterKindOf(c: Column): FilterKind {
  if (c.rule?.kind === 'boolean') return 'boolean'
  if (c.rule?.kind === 'enum') return 'enum'
  const fmt = (c.format ?? '').toLowerCase()
  const typ = (c.type ?? '').toLowerCase()
  if (isDateish(fmt, typ)) return 'date'
  if (isNumericish(fmt, typ)) return 'number'
  return 'text'
}
const filterPropsFor = (kind: FilterKind, options?: { value: string; label: string }[]) =>
  kind === 'boolean' || kind === 'enum'
    ? { filterFn: 'equals' as const, meta: { filter: { kind, options } } as FilterMeta }
    : { filterFn: genericFilterFn, meta: { filter: { kind } } as FilterMeta }

// ── inline-edit controls ────────────────────────────────────────────────────
const EditInput = styled.input`
  width: 100%; box-sizing: border-box; background: ${colors.bg.input};
  border: 1px solid ${colors.blue.border}; border-radius: ${radius.sm};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  padding: 2px 6px; outline: none;
  &:focus { box-shadow: 0 0 0 2px ${colors.blue.bg}; }
`
const EditSelect = styled.select`
  width: 100%; box-sizing: border-box; background: ${colors.bg.input};
  border: 1px solid ${colors.blue.border}; border-radius: ${radius.sm};
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  padding: 2px 4px; cursor: pointer;
  option { background: ${colors.bg.dropdown}; color: ${colors.text.secondary}; }
`
const ActionBtns = styled.div`display: inline-flex; gap: 3px;`
const IconBtn = styled.button<{ $tone?: 'primary' }>`
  display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 22px;
  border-radius: ${radius.sm};
  border: 1px solid ${({ $tone }) => ($tone === 'primary' ? colors.blue.border : colors.border)};
  background: ${({ $tone }) => ($tone === 'primary' ? colors.blue.bg : 'transparent')};
  color: ${({ $tone }) => ($tone === 'primary' ? colors.blue.main : colors.text.muted)};
  cursor: pointer; padding: 0;
  &:hover:not(:disabled) { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
  &:disabled { opacity: 0.3; cursor: default; }
`
const EditToggleBtn = styled.button<{ $on: boolean }>`
  display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 10px; border-radius: ${radius.md};
  border: 1px solid ${({ $on }) => ($on ? colors.blue.border : colors.border)};
  background: ${({ $on }) => ($on ? colors.blue.bg : colors.bg.input)};
  color: ${({ $on }) => ($on ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer; white-space: nowrap;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`

// What input to show when editing a column (the resolved lookup column is never editable —
// you edit its "(ID)" sibling). A boolean code is just a text field (we don't know its "false" code).
function editControl(c: Column): 'enum' | 'date' | 'number' | 'text' {
  if (c.rule?.kind === 'enum') return 'enum'
  const fmt = (c.format ?? '').toLowerCase()
  const typ = (c.type ?? '').toLowerCase()
  if (isDateish(fmt, typ)) return 'date'
  if (isNumericish(fmt, typ)) return 'number'
  return 'text'
}

export function ResultTable({
  result, connector, query, updateQuery, onSaved,
}: {
  result: QueryResult
  connector: string
  query: string
  updateQuery?: string | null
  onSaved?: () => void
}) {
  const { t } = useTranslation()

  // ── inline-edit state ──
  const [editMode, setEditMode] = useState(false)
  const [editingRow, setEditingRow] = useState<DataRow | null>(null) // tracked by object reference
  const [saving, setSaving] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const editValuesRef = useRef<DataRow>({}) // in-progress edits for editingRow — uncontrolled inputs write here
  const canEdit = !!updateQuery

  // a refetch (or a query change) ends any in-progress edit
  useEffect(() => {
    setEditingRow(null)
    editValuesRef.current = {}
    setEditError(null)
  }, [result])

  const startEdit = useCallback((row: DataRow) => {
    setEditError(null)
    editValuesRef.current = { ...row }
    setEditingRow(row)
  }, [])
  const cancelEdit = useCallback(() => {
    setEditError(null)
    editValuesRef.current = {}
    setEditingRow(null)
  }, [])
  const commitEdit = useCallback(async () => {
    if (!updateQuery || !editingRow) return
    setSaving(true)
    setEditError(null)
    const merged = editValuesRef.current // a copy of the row, mutated by the inputs
    // Also send UPPERCASE keys: the migrated `_put` queries use v1's uppercase column names,
    // while Postgres returns the read result's columns lowercased.
    const params: Record<string, unknown> = { ...merged }
    for (const [k, v] of Object.entries(merged)) params[k.toUpperCase()] = v
    try {
      await api.post(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(updateQuery)}`, { params })
      setEditingRow(null)
      editValuesRef.current = {}
      onSaved?.()
    } catch (e) {
      setEditError(e instanceof ApiError ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }, [updateQuery, editingRow, connector, onSaved])
  const toggleEditMode = useCallback(() => {
    cancelEdit()
    setEditMode((v) => !v)
  }, [cancelEdit])

  // ── rule helpers (memoized per result) ──
  const enumMaps = useMemo(() => {
    const m = new Map<string, Map<string, string>>()
    for (const c of result.columns) if (c.rule?.kind === 'enum') m.set(c.name, enumMap(c.rule))
    return m
  }, [result.columns])
  const lookupSpecs = useMemo<LookupSpec[]>(
    () =>
      result.columns
        .filter((c) => c.rule?.kind === 'lookup')
        .map((c) => {
          const r = c.rule as Extract<NonNullable<Column['rule']>, { kind: 'lookup' }>
          return { connector: r.connector, query: r.query, value: r.value, label: r.label }
        }),
    [result.columns],
  )
  const lookupMaps = useLookupBatch(lookupSpecs)

  // ── column definitions ──
  const columns = useMemo<ColumnDef<DataRow, unknown>[]>(() => {
    const idSuffix = ` ${t('table.idColumnSuffix')}`
    const span = (text: string, kind: ReturnType<typeof ruleCell>['kind'], align: Align, titleVal?: string) => (
      <CellSpan className={kind === 'plain' ? undefined : kind} style={align ? { textAlign: align } : undefined} title={titleVal}>
        {text}
      </CellSpan>
    )
    const grouped = (info: { cell: { getIsGrouped: () => boolean }; getValue: () => unknown }, align: Align) =>
      info.cell.getIsGrouped() ? span(String(info.getValue() ?? ''), 'plain', align) : null
    // is this row's cell being edited right now?
    const editingHere = (info: { row: { original: unknown } }) => editMode && (info.row.original as DataRow) === editingRow
    const editCell = (c: Column) => (
      <EditCell column={c} initial={editValuesRef.current[c.name]} onChange={(v) => { editValuesRef.current[c.name] = v }} t={t} />
    )

    const out: ColumnDef<DataRow, unknown>[] = []

    if (editMode) {
      out.push({
        id: '__actions',
        header: () => null,
        size: 70,
        minSize: 70,
        enableSorting: false,
        enableHiding: false,
        enableColumnFilter: false,
        enableGrouping: false,
        enableResizing: false,
        meta: { internal: true },
        cell: (info) => {
          const row = info.row.original as DataRow
          if (info.cell.getIsPlaceholder?.() || info.row.getIsGrouped?.()) return null
          if (editingRow === row) {
            return (
              <ActionBtns>
                <IconBtn $tone="primary" disabled={saving} onClick={commitEdit} title={t('common.save')}><Check size={13} /></IconBtn>
                <IconBtn disabled={saving} onClick={cancelEdit} title={t('common.cancel')}><X size={13} /></IconBtn>
              </ActionBtns>
            )
          }
          return (
            <IconBtn disabled={editingRow !== null} onClick={() => startEdit(row)} title={t('common.edit')}><Pencil size={12} /></IconBtn>
          )
        },
      })
    }

    for (const c of result.columns) {
      const align = cellAlign(c)

      if (c.rule?.kind === 'lookup') {
        const r = c.rule
        const map = lookupMaps.get(lookupKey({ connector: r.connector, query: r.query, value: r.value, label: r.label }))
        out.push({
          id: c.name,
          header: colHeader(c) + idSuffix,
          accessorFn: (row) => row[c.name],
          size: c.width ?? undefined,
          ...filterPropsFor('text'),
          cell: (info) => {
            const g = grouped(info, align)
            if (g) return g
            if (editingHere(info)) return editCell(c)
            const { text, isNull } = ruleCell(info.getValue(), { ...c, rule: undefined }, undefined, undefined)
            return span(isNull ? 'null' : text, isNull ? 'null' : 'plain', align)
          },
        })
        out.push({
          id: `${c.name}__lookup`,
          header: colHeader(c),
          accessorFn: (row) => {
            const v = row[c.name]
            return v === null || v === undefined ? '' : (map?.get(String(v)) ?? String(v))
          },
          ...filterPropsFor('text'),
          cell: (info) => {
            const g = grouped(info, align)
            if (g) return g
            // derived from the "(ID)" column — not editable; in edit mode it shows the *edited* code's label
            const raw = editingHere(info)
              ? (editValuesRef.current[c.name] as unknown)
              : (info.row.original as DataRow)[c.name]
            const { text, kind, isNull } = ruleCell(raw, c, undefined, map)
            return span(text, isNull ? 'null' : kind, align, isNull ? undefined : String(raw ?? ''))
          },
        })
        continue
      }

      const kind = filterKindOf(c)
      const enumMapForCol = enumMaps.get(c.name)
      const enumOptions = c.rule?.kind === 'enum' ? c.rule.values.map((v) => ({ value: v.label, label: v.label })) : undefined
      out.push({
        id: c.name,
        header: colHeader(c),
        accessorFn: (row) => {
          const v = row[c.name]
          if (c.rule?.kind === 'boolean') return v === null || v === undefined ? null : v === c.rule.true_value ? 'true' : 'false'
          if (c.rule?.kind === 'enum' && v != null) return enumMapForCol?.get(String(v)) ?? v
          return v
        },
        size: c.width ?? undefined,
        ...filterPropsFor(kind, enumOptions),
        cell: (info) => {
          const g = grouped(info, align)
          if (g) return g
          if (editingHere(info)) return editCell(c)
          const v = (info.row.original as DataRow)[c.name]
          const { text, kind: rk, isNull } = ruleCell(v, c, enumMapForCol, undefined)
          return span(text, isNull ? 'null' : rk, align, rk === 'enum' && !isNull ? String(v ?? '') : undefined)
        },
      })
    }
    return out
    // editValuesRef is a ref — typing into an edit input doesn't churn this memo.
  }, [result.columns, enumMaps, lookupMaps, t, editMode, editingRow, saving, startEdit, cancelEdit, commitEdit])

  const initialVisibility = useMemo<VisibilityState>(() => {
    const v: VisibilityState = {}
    for (const c of result.columns) {
      if (c.hidden) {
        v[c.name] = false
        if (c.rule?.kind === 'lookup') v[`${c.name}__lookup`] = false
      }
    }
    return v
  }, [result.columns])

  return (
    <>
      {editError && <Banner $tone="error">{editError}</Banner>}
      {editMode && editingRow && !editError && <Banner $tone="info">{t('table.editingHint')}</Banner>}
      <DataTable<DataRow>
        columns={columns}
        data={result.rows}
        tableId={`sql:${connector}:${query}`}
        exportFilename={query}
        initialColumnVisibility={initialVisibility}
        toolbar={
          canEdit ? (
            <EditToggleBtn $on={editMode} onClick={toggleEditMode} title={t('table.editTip', { q: updateQuery })}>
              <Pencil size={13} /> {t('table.edit')}
            </EditToggleBtn>
          ) : undefined
        }
      />
    </>
  )
}

function EditCell({ column, initial, onChange, t }: {
  column: Column
  initial: unknown
  onChange: (v: unknown) => void
  t: (key: string) => string
}) {
  const ctrl = editControl(column)
  const init = initial === null || initial === undefined ? '' : String(initial)
  if (ctrl === 'enum' && column.rule?.kind === 'enum') {
    return (
      <EditSelect defaultValue={init} onChange={(e) => onChange(e.target.value === '' ? null : e.target.value)}>
        <option value="">{t('table.nullOption')}</option>
        {column.rule.values.map((v) => <option key={v.value} value={v.value}>{v.label}</option>)}
      </EditSelect>
    )
  }
  const type = ctrl === 'date' ? 'date' : ctrl === 'number' ? 'number' : 'text'
  const dv = ctrl === 'date' && init ? init.slice(0, 10) : init
  return (
    <EditInput
      type={type}
      defaultValue={dv}
      onChange={(e) => {
        const raw = e.target.value
        if (raw === '') onChange(null)
        else if (ctrl === 'number') onChange(Number(raw))
        else onChange(raw)
      }}
    />
  )
}

export default ResultTable
