// Nested-tab content kinds — embedded directly inside a parent ScreenDialog tab. Two
// flavours (matching the discriminated-union types in `types/screens.ts`):
//
//   • NestedFormView — v2's port of v1's "FormsDialog inside a FormsDialog". Loads the
//     linked single-record row (parent's PK is bound into the nested read_query via
//     param_binds) and displays the configured fields. Slice 1: **read-only display** —
//     the empty tabs the user complained about ("JD Edwards", "LDAP" on
//     security_applications) are now populated. Slice 2 will add inline editing + the
//     parent-Save → nested-save coordination (a register/dispatch pattern via a shared
//     ref on the parent ScreenDialog).
//
//   • NestedTableView — v2's port of v1's "FormsTable inside a FormsDialog". References
//     another v2 Screen by id (e.g. parent SETTINGS_APPLICATIONS' "Activity Log" tab →
//     standalone settings_activity_log screen), fetches its rows narrowed by the parent
//     PK, and renders a basic DataTable. Read-only for now (the activity_log / audit_trail
//     case is naturally read-only anyway). Drill into a row, or full TableView pipeline
//     re-use, lands in a follow-up.
//
// Both wait for their bound params to resolve before fetching — a not-yet-saved "add"
// parent (APPS_ID still undefined) shows an inert empty state instead of firing a request
// that would return everything in the table.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from '../../api/client'
import { Banner, SpinnerRing } from '../../common'
import { DataTable } from '../../common/DataTable'
import type { Column, QueryResult } from '../../types/connectors'
import type { NestedFormTab, NestedTableTab, ScreenDetail, ScreenField } from '../../types/screens'
import { resolveBindList, type Row, valueFor } from './dialogHelpers'
import { colors, fontSize, fonts, radius } from '../../theme'

const FieldGrid = styled.div<{ $cols: number }>`
  display: grid; grid-template-columns: repeat(${({ $cols }) => $cols}, 1fr); gap: 12px;
`
const Cell = styled.div`
  display: flex; flex-direction: column; gap: 4px; min-width: 0;
`
const CellLabel = styled.div`
  font-size: ${fontSize.micro}; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em;
  color: ${colors.text.muted};
`
const ReadOnlyBox = styled.div`
  display: block; width: 100%; box-sizing: border-box; padding: 6px 10px; min-height: 32px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  white-space: pre-wrap; overflow-wrap: anywhere;
`
const Hint = styled.div`
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 6px 0;
`
const LoadingRow = styled.div`
  display: flex; align-items: center; gap: 8px; color: ${colors.text.muted}; font-size: ${fontSize.sm};
`
const TableWrap = styled.div`
  display: flex; flex-direction: column; min-height: 0; height: 100%;
`

/** Build the query-string for a bound GET — same convention the TableView uses. Empty when
 *  there are no resolved binds (the param form expects narrowing; no narrowing → the URL
 *  carries no params, which we treat as "skip fetch" so a not-yet-saved parent doesn't
 *  trigger a giant SELECT). */
function bindsToQuery(bound: Record<string, string>): string {
  const parts = Object.entries(bound).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
  return parts.length ? `?${parts.join('&')}` : ''
}

/** Resolve `tab.connector` against the parent screen's effective connector (the dialog's
 *  `connector` prop). Matches the migration / runtime convention: blank → parent's. */
function effectiveConnector(tab: { connector?: string | null }, parentConnector: string): string {
  return (tab.connector && tab.connector.trim()) || parentConnector
}

// ── NestedFormView ──────────────────────────────────────────────────────────
// A child-record form embedded inline in this tab. Loads the linked row via the nested
// `read_query` narrowed by `param_binds` resolved against parent form state.

export function NestedFormView({
  tab, parentFormValues, parentConnector,
}: {
  tab: NestedFormTab
  /** Live parent form state — `source` binds read from here at fetch time. */
  parentFormValues: Row
  /** The parent screen's effective connector — used when `tab.connector` is blank. */
  parentConnector: string
}) {
  const { t } = useTranslation()
  const connector = effectiveConnector(tab, parentConnector)
  // Resolve bound params against the live parent state. JSON-stringified key drives the
  // fetch effect — re-running the read query when the parent's PK / context changes.
  const bound = useMemo(() => resolveBindList(tab.param_binds, parentFormValues), [tab.param_binds, parentFormValues])
  const boundKey = useMemo(() => JSON.stringify(bound), [bound])
  // Skip the fetch entirely when no binds resolved (a not-yet-saved parent in "add" mode):
  // firing the read_query with no params would return every row in the underlying table.
  const hasBinds = Object.keys(bound).length > 0

  const [result, setResult] = useState<QueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!hasBinds) {
      setResult(null); setError(null); return
    }
    setLoading(true); setError(null)
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(tab.read_query)}${bindsToQuery(bound)}`)
      .then((r) => setResult(r))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boundKey is the stable serialization of bound
  }, [connector, tab.read_query, boundKey, hasBinds])

  const row: Row | null = result?.rows?.[0] ?? null
  const colByName = useMemo(() => {
    const m = new Map<string, Column>()
    for (const c of result?.columns ?? []) m.set(c.name.toLowerCase(), c)
    return m
  }, [result])

  const cols = Math.max(1, tab.cols ?? 2)
  if (!hasBinds) return <Banner $tone="info">{t('dialog.nested.pendingBinds')}</Banner>
  if (loading && !result) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  if (error) return <Banner $tone="error">{error}</Banner>
  if (!row) return <Hint>{t('dialog.nested.noRecord')}</Hint>

  return (
    <FieldGrid $cols={cols}>
      {tab.fields
        .filter((f) => !f.hidden)
        .map((f) => (
          <NestedFieldCell key={f.name} field={f} column={colByName.get(f.name.toLowerCase()) ?? null} row={row} cols={cols} />
        ))}
    </FieldGrid>
  )
}

function NestedFieldCell({ field, column, row, cols }: {
  field: ScreenField; column: Column | null; row: Row; cols: number
}) {
  const label = field.label ?? column?.label ?? field.name
  const raw = valueFor(field.name, row)
  // Password fields never display their stored value (it's a hash / ENC: ciphertext).
  // Same rule as the parent ScreenDialog's edit mode.
  const isPwd = (column?.format ?? '').toLowerCase() === 'password'
  const display = isPwd ? '••••••••' : raw == null ? '—' : String(raw)
  const span = Math.min(cols, Math.max(1, field.colspan ?? 1))
  return (
    <Cell style={{ gridColumn: `span ${span}` }}>
      <CellLabel>{label}</CellLabel>
      <ReadOnlyBox>{display}</ReadOnlyBox>
    </Cell>
  )
}

// ── NestedTableView ─────────────────────────────────────────────────────────
// A related-rows table embedded inline. References another v2 Screen by id; loads its
// read query narrowed by `param_binds` resolved against parent form state.

export function NestedTableView({
  tab, parentFormValues, parentConnector, app,
}: {
  tab: NestedTableTab
  parentFormValues: Row
  parentConnector: string
  /** The parent screen's app — used to look up the nested screen's detail catalog entry
   *  (GET /api/screens/{app}/{id}). The nested screen lives under the same app since
   *  screens.toml's [screens.<app>.<id>] keys by app. */
  app: string
}) {
  const { t } = useTranslation()
  const connector = effectiveConnector(tab, parentConnector)
  const bound = useMemo(() => resolveBindList(tab.param_binds, parentFormValues), [tab.param_binds, parentFormValues])
  const boundKey = useMemo(() => JSON.stringify(bound), [bound])
  const hasBinds = Object.keys(bound).length > 0

  const [nestedScreen, setNestedScreen] = useState<ScreenDetail | null>(null)
  const [result, setResult] = useState<QueryResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Fetch the nested screen's detail once — we need its read_query name.
  useEffect(() => {
    setError(null)
    api.get<ScreenDetail>(`/api/screens/${encodeURIComponent(app)}/${encodeURIComponent(tab.screen)}`)
      .then((s) => setNestedScreen(s))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [app, tab.screen])

  useEffect(() => {
    if (!nestedScreen || !hasBinds) { setResult(null); return }
    setLoading(true); setError(null)
    const q = nestedScreen.read_query
    api.get<QueryResult>(`/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(q)}${bindsToQuery(bound)}`)
      .then((r) => setResult(r))
      .catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
      .finally(() => setLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps -- boundKey serializes bound
  }, [nestedScreen, connector, boundKey, hasBinds])

  // Minimal column defs — one TanStack column per result column, displaying the raw value.
  // No rules (BOOLEAN / ENUM / LOOKUP), no filters, no batch edit — slice 2 territory.
  const columns = useMemo(() => {
    const cols = result?.columns ?? []
    return cols
      .filter((c) => !c.hidden)
      .map((c) => ({
        id: c.name,
        accessorFn: (r: Row) => r[c.name] ?? r[c.name.toLowerCase()],
        header: c.label ?? c.name,
        cell: ({ getValue }: { getValue: () => unknown }) => {
          const v = getValue()
          return v == null ? '' : String(v)
        },
      }))
  }, [result])

  if (!hasBinds) return <Banner $tone="info">{t('dialog.nested.pendingBinds')}</Banner>
  if (error) return <Banner $tone="error">{error}</Banner>
  if (loading && !result) return <LoadingRow><SpinnerRing size={14} thickness={2} /> {t('common.loading')}</LoadingRow>
  if (!result) return null
  return (
    <TableWrap>
      <DataTable
        tableId={`nested-${app}-${tab.screen}`}
        data={(result.rows as Row[]) ?? []}
        columns={columns}
      />
    </TableWrap>
  )
}
