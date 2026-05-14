// ScreenDialog — the modal form shown when the TableView opens a row for editing or "Add row".
// Built from a Screen's `dialog` (tabs → fields → optional lookup_param_binds). For each
// `ScreenField.name` we look up the matching column on the read result to pick the right widget
// (BOOLEAN / ENUM / LOOKUP / number / date / text) and reuse the dictionary's resolved display
// rule. LOOKUP combos resolve their `lookup_param_binds` at *call time* — a `source` bind reads
// the current form's value of `<source>`, a `value` bind is a literal; both feed the lookup spec's
// `params`, which the lookup service already passes as URL binds and uses as a client-side filter.
//
// Save POSTs to /api/sql/{connector}/{update_query|insert_query} with the row's values (uppercased
// + `:_ORIGINAL` keys for edit, same shape the inline grid editor sends). Cancel discards. Tabs
// flagged `hide_on_add` / `hide_on_edit` drop out in the matching mode. Fields with `hidden=true`
// are skipped; `disabled=true` renders read-only; `required=true` triggers HTML5 validation; a
// `colspan` widens the field across the tab's CSS grid (`cols` wide). When a screen has no dialog
// the TableView falls back to its existing inline grid-edit flow.
import { useCallback, useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Save, X } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { Banner, Button, Field, Input, Modal, ModalBody, ModalFooter, ModalHeader, Overlay, Row, SearchSelect, SpinnerRing } from '../../common'
import type { Column } from '../../types/connectors'
import type { ScreenDetail, ScreenField, ScreenTab } from '../../types/screens'
import { type LookupSpec, lookupKey, lookupOptions, useLookupTables } from '../../services/lookups'
import { colors, fontSize, fonts, radius } from '../../theme'

type Row = Record<string, unknown>
export type DialogMode = 'edit' | 'add'

// Send both the as-is keys and UPPERCASE copies — same trick as the inline grid editor:
// the migrated `_put`/`_post`/`_delete` queries use v1's uppercase column names, while
// Postgres returns the read result's columns lowercased. `text()` only binds what its SQL
// references, so the extras are harmless.
function withUpper(o: Row): Row {
  const out: Row = { ...o }
  for (const [k, v] of Object.entries(o)) out[k.toUpperCase()] = v
  return out
}
function originalKeys(row: Row): Row {
  return Object.fromEntries(Object.entries(row).map(([k, v]) => [`${k}_ORIGINAL`, v]))
}

const TabStrip = styled.div`
  display: flex; gap: 4px; margin-bottom: 12px; border-bottom: 1px solid ${colors.border};
`
const TabBtn = styled.button<{ $active?: boolean }>`
  height: 32px; padding: 0 14px; border: none; border-bottom: 2px solid transparent; background: transparent;
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  border-bottom-color: ${({ $active }) => ($active ? colors.blue.main : 'transparent')};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer;
  font-weight: ${({ $active }) => ($active ? 600 : 400)};
  &:hover { color: ${colors.text.primary}; }
`
const FieldGrid = styled.div<{ $cols: number }>`
  display: grid; grid-template-columns: repeat(${({ $cols }) => $cols}, 1fr); gap: 12px;
`
const CellWrap = styled.div<{ $span: number }>`
  grid-column: span ${({ $span }) => $span}; min-width: 0;
`
const ReadOnlyBox = styled.div`
  display: block; width: 100%; box-sizing: border-box; padding: 6px 10px; min-height: 32px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  white-space: pre-wrap; overflow-wrap: anywhere;
`
const Checkbox = styled.label`
  display: inline-flex; align-items: center; gap: 8px; height: 32px;
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & input { width: 16px; height: 16px; cursor: pointer; }
`

// ── widget choice mirrors ResultTable.editCtrlOf — keep both in sync. ─────────
function isNumericish(fmt: string, typ: string) { return fmt === 'number' || fmt === 'integer' || /int|numeric|decimal|float|double|real/.test(typ) }
function isDateish(fmt: string, typ: string) { return fmt === 'date' || /date|timestamp/.test(typ) }
function isPassword(c: Column | null): boolean { return (c?.format ?? '').toLowerCase() === 'password' }

// Resolve the field's `lookup_param_binds` against the current form state. `value` binds are
// literals; `source` binds read the live value of another field on the same form (column name,
// case-insensitive). Empty / missing values are dropped so the lookup falls back to "no narrowing"
// rather than filtering away everything. Reserved built-ins (`#LOGIN_USER#`/`#SYSDATE#`/…) wait
// for slice 4 — for now those `source` binds are skipped with a noop.
function resolveBinds(field: ScreenField, formValues: Row): Record<string, string> {
  const out: Record<string, string> = {}
  for (const b of field.lookup_param_binds ?? []) {
    if (b.value != null && b.value !== '') { out[b.param] = String(b.value); continue }
    if (b.source && !b.source.startsWith('#')) {
      // case-insensitive on the source name (read columns are lowercased by Postgres)
      const key = Object.keys(formValues).find((k) => k.toLowerCase() === b.source!.toLowerCase())
      const v = key != null ? formValues[key] : undefined
      if (v != null && String(v) !== '') out[b.param] = String(v)
    }
  }
  return out
}

// One field row — a switch over the column's resolved rule (when present) + format/type to pick
// the right widget. `disabled` from the field config locks the widget; `required` forwards HTML5
// validation. Values are kept in the parent's `formValues` map by column name (the read result's
// case — which is what the row already uses).
function FieldRow({
  field, column, formValues, onChange, disabled,
}: {
  field: ScreenField
  column: Column | null
  formValues: Row
  onChange: (name: string, v: unknown) => void
  disabled: boolean
}) {
  const { t } = useTranslation()
  const value = formValues[field.name]
  const textValue = value === null || value === undefined ? '' : String(value)
  const required = !!field.required
  const label = field.label ?? column?.label ?? field.name

  // For a LOOKUP field we need a live lookup spec — the *static* params from the rule plus the
  // dynamic ones from the field's lookup_param_binds (resolved against the current form values).
  const lookupSpec: LookupSpec | null = useMemo(() => {
    if (!column?.rule || column.rule.kind !== 'lookup') return null
    const dyn = resolveBinds(field, formValues)
    return {
      connector: column.rule.connector,
      query: column.rule.query,
      value: column.rule.value,
      label: column.rule.label,
      params: { ...(column.rule.params ?? {}), ...dyn },
    }
  }, [column, field, formValues])
  // Mount a single-spec hook unconditionally (React requires stable hook order). The spec list is
  // either [lookupSpec] (lookup field) or [] (everything else); the lookup service caches per spec
  // key so re-renders that don't change the key don't refetch.
  const lookupMaps = useLookupTables(lookupSpec ? [lookupSpec] : [])
  const lookupData = lookupSpec ? lookupMaps.get(lookupKey(lookupSpec)) : undefined

  let widget: React.ReactNode
  if (disabled) {
    // Read-only echo — keep the value visible (and copyable). Boolean shows its raw code, same as
    // every other type; no special widget here so the user sees exactly what's in the column.
    widget = <ReadOnlyBox aria-readonly>{textValue || <span style={{ color: colors.text.muted }}>—</span>}</ReadOnlyBox>
  } else if (column?.rule?.kind === 'boolean') {
    const trueV = column.rule.true_value
    const checked = textValue === trueV
    widget = (
      <Checkbox>
        <input
          type="checkbox" checked={checked}
          onChange={(e) => onChange(field.name, e.target.checked ? trueV : null)}
        />
        <span>{checked ? trueV : t('common.no')}</span>
      </Checkbox>
    )
  } else if (column?.rule?.kind === 'enum') {
    const options = column.rule.values.map((v) => ({ value: v.value, label: v.label, mono: v.value }))
    widget = (
      <SearchSelect
        value={textValue}
        onChange={(v) => onChange(field.name, v === '' ? null : v)}
        options={options}
        anyLabel={required ? undefined : t('common.none')}
        placeholder={t('common.pick')}
      />
    )
  } else if (column?.rule?.kind === 'lookup' && lookupSpec) {
    const opts = lookupData ? lookupOptions(lookupData).map((o) => ({ value: o.value, label: o.label, mono: o.value })) : []
    widget = (
      <SearchSelect
        value={textValue}
        onChange={(v) => onChange(field.name, v === '' ? null : v)}
        options={opts}
        anyLabel={required ? undefined : t('common.none')}
        loading={!lookupData}
        placeholder={t('common.pick')}
      />
    )
  } else if (isPassword(column)) {
    // Never round-trip the stored value: it's an ``ENC:`` blob and showing it in the dialog
    // leaks ciphertext (and, when it's a plain hash, lets a casual observer copy it). Render
    // a masked input with a placeholder that hints "leave blank to keep". Seeding code skips
    // password fields, so `textValue` here is always empty when the dialog opens — the user
    // types a new value if they want to change the password, or leaves it blank to keep the
    // current one. (The submit path only includes a password field when the user typed
    // something — see ScreenDialog.submit.)
    widget = (
      <Input
        type="password" autoComplete="new-password" required={required}
        value={textValue}
        placeholder={t('dialog.passwordPlaceholder')}
        onChange={(e) => onChange(field.name, e.target.value === '' ? null : e.target.value)}
      />
    )
  } else {
    const fmt = (column?.format ?? '').toLowerCase()
    const typ = (column?.type ?? '').toLowerCase()
    const type = isDateish(fmt, typ) ? 'date' : isNumericish(fmt, typ) ? 'number' : 'text'
    const dv = type === 'date' && textValue ? textValue.slice(0, 10) : textValue
    widget = (
      <Input
        type={type} required={required} value={dv}
        onChange={(e) => {
          const raw = e.target.value
          if (raw === '') onChange(field.name, null)
          else if (type === 'number') onChange(field.name, Number(raw))
          else onChange(field.name, raw)
        }}
      />
    )
  }

  return (
    <CellWrap $span={Math.max(1, field.colspan ?? 1)}>
      <Field label={`${label}${required ? ' *' : ''}`}>{widget}</Field>
    </CellWrap>
  )
}

export function ScreenDialog({
  open, mode, screen, columns, row, connector, onClose, onSaved,
}: {
  open: boolean
  mode: DialogMode
  /** the full screen definition (dialog + update/insert query refs) */
  screen: ScreenDetail
  /** the read result's columns — used to resolve display rules / widget kinds per field. */
  columns: Column[]
  /** the row being edited (mode='edit'). For 'add' an empty / default-filled row. */
  row: Row
  /** the screen's effective connector — same one the screen's read query lives on. */
  connector: string
  onClose: () => void
  /** Called after a successful save; the TableView reruns its SELECT to refresh the grid. */
  onSaved: () => void
}) {
  const { t } = useTranslation()
  const dlg = screen.dialog
  // Filter tabs by mode (v1's tab_disable_add/_edit → hide_on_add/_edit). An empty list after the
  // filter shouldn't happen in practice but we surface it as a banner instead of crashing.
  const tabs = useMemo<ScreenTab[]>(() => {
    if (!dlg) return []
    return dlg.tabs.filter((tab) => (mode === 'add' ? !tab.hide_on_add : !tab.hide_on_edit))
  }, [dlg, mode])

  const [tabIdx, setTabIdx] = useState(0)
  const [formValues, setFormValues] = useState<Row>({})
  const [savedRow, setSavedRow] = useState<Row>({})  // the *original* values keyed by field name (for `:<FIELD>_ORIGINAL`)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Case-insensitive lookup: the DB result rows have lowercase keys (Postgres folds unquoted
  // identifiers); a ScreenField.name from the migration is uppercase (col_target). Match by
  // lower-casing on both sides so we read the right cell when seeding the form.
  const valueFor = useCallback((field: string, src: Row): unknown => {
    if (field in src) return src[field]
    const lk = field.toLowerCase()
    if (lk in src) return src[lk]
    for (const k of Object.keys(src)) if (k.toLowerCase() === lk) return src[k]
    return undefined
  }, [])

  // Resolve each ScreenField.name to its read-result column (case-insensitive), once per change.
  // Hoisted above the seeding effect so we can skip password fields when seeding.
  const colByName = useMemo(() => {
    const m = new Map<string, Column>()
    for (const c of columns) m.set(c.name.toLowerCase(), c)
    return m
  }, [columns])

  // Re-seed when the dialog (re-)opens or the underlying row changes. The form state is keyed by
  // ScreenField.name (whatever case the screen uses) so on save we can post `{NAME: v}` with the
  // case the migrated update_query expects. `add` mode also applies field defaults. Password
  // fields are NEVER seeded with the stored value — that's a hash / ENC: ciphertext, and putting
  // it in the form means it (a) shows up in the masked input as long-look-alike dots, (b) gets
  // posted back on save, overwriting whatever the user might have set elsewhere. The seeded
  // `savedRow` still carries the original for non-password `:<COL>_ORIGINAL` binds.
  useEffect(() => {
    if (!open || !dlg) return
    const seeded: Row = {}
    const original: Row = {}
    for (const tab of dlg.tabs) {
      for (const f of tab.fields ?? []) {
        const col = colByName.get(f.name.toLowerCase()) ?? null
        const v = valueFor(f.name, row)
        if (v !== undefined) original[f.name] = v
        if (isPassword(col)) continue   // seeded blank — user types a new password to change it
        if (v !== undefined) seeded[f.name] = v
        else if (mode === 'add' && f.default != null && f.default !== '') seeded[f.name] = f.default
      }
    }
    setFormValues(seeded)
    setSavedRow(original)
    setTabIdx(0)
    setError(null)
  }, [open, mode, row, dlg, valueFor, colByName])

  const onFieldChange = useCallback((name: string, v: unknown) => {
    setFormValues((p) => ({ ...p, [name]: v }))
  }, [])

  const submit = useCallback(async () => {
    const targetQuery = mode === 'edit' ? screen.update_query : screen.insert_query
    if (!targetQuery) {
      setError(t(mode === 'edit' ? 'table.editNoUpdate' : 'table.editNoInsert'))
      return
    }
    // Collect every value that's on a visible field on a non-hidden tab. Password fields with
    // empty values are dropped — the user didn't change the password, so we must not overwrite
    // the stored hash / ENC: ciphertext with NULL or "". On 'edit', the absence of the password
    // key in `sent` means `savedRow`'s original (already stored, untouched) wins; on 'add', the
    // INSERT runs without a password (the DB column may be nullable or get a default).
    const sent: Row = {}
    for (const tab of tabs) {
      for (const f of tab.fields ?? []) {
        if (f.hidden) continue
        if (!(f.name in formValues)) continue
        const v = formValues[f.name]
        const col = colByName.get(f.name.toLowerCase()) ?? null
        if (isPassword(col) && (v == null || v === '')) continue
        sent[f.name] = v
      }
    }
    setSaving(true); setError(null)
    try {
      // For password fields we never had the original ciphertext in the form (seeded blank), so
      // dropping them from `savedRow` keeps the update-query's SET clause from binding `:PASSWORD`
      // to undefined — text() omits unmentioned params, but a key with the wrong value would still
      // overwrite. The migrated _put queries reference :PASSWORD only in their SET clause; not
      // sending it means the column keeps its current DB value. (When the user *did* type a new
      // password, `sent` carries it and overrides.)
      const baseSaved: Row = mode === 'edit'
        ? Object.fromEntries(Object.entries(savedRow).filter(([k]) => {
            const c = colByName.get(k.toLowerCase()) ?? null
            return !isPassword(c)
          }))
        : {}
      const params = mode === 'edit'
        ? { ...baseSaved, ...sent, ...originalKeys(baseSaved) }
        : sent
      await api.post(
        `/api/sql/${encodeURIComponent(connector)}/${encodeURIComponent(targetQuery)}`,
        { params: withUpper(params) },
      )
      setSaving(false)
      onSaved()
      onClose()
    } catch (e) {
      setSaving(false)
      setError(e instanceof ApiError ? e.message : String(e))
    }
  }, [mode, screen, tabs, formValues, savedRow, connector, onClose, onSaved, t, colByName])

  if (!open || !dlg) return null
  const currentTab = tabs[Math.min(tabIdx, tabs.length - 1)] ?? null
  const title = dlg.title || screen.label || screen.id
  const gridCols = Math.max(1, currentTab?.cols ?? 2)
  const visibleFields = (currentTab?.fields ?? []).filter((f) => !f.hidden)

  return (
    <Overlay onClick={onClose}>
      <Modal style={{ width: 720, maxWidth: '95vw' }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>
          {mode === 'edit' ? t('dialog.editTitle', { title }) : t('dialog.addTitle', { title })}
        </ModalHeader>
        <ModalBody>
          {tabs.length === 0 ? (
            <Banner $tone="info">{t('dialog.noVisibleTabs')}</Banner>
          ) : (
            <>
              {tabs.length > 1 && (
                <TabStrip role="tablist">
                  {tabs.map((tab, i) => (
                    <TabBtn key={tab.id} type="button" role="tab" aria-selected={i === tabIdx} $active={i === tabIdx} onClick={() => setTabIdx(i)}>
                      {tab.label || tab.id}
                    </TabBtn>
                  ))}
                </TabStrip>
              )}
              {currentTab && (
                <FieldGrid $cols={gridCols}>
                  {visibleFields.map((f) => (
                    <FieldRow
                      key={f.name}
                      field={f}
                      column={colByName.get(f.name.toLowerCase()) ?? null}
                      formValues={formValues}
                      onChange={onFieldChange}
                      disabled={!!f.disabled}
                    />
                  ))}
                  {visibleFields.length === 0 && (
                    <CellWrap $span={gridCols}>
                      <Banner $tone="info">{t('dialog.noVisibleFields')}</Banner>
                    </CellWrap>
                  )}
                </FieldGrid>
              )}
            </>
          )}
          {error && <Banner $tone="error">{error}</Banner>}
        </ModalBody>
        <ModalFooter>
          <Row gap={8}>
            <Button $size="sm" $variant="ghost" onClick={onClose} disabled={saving}>
              <X size={13} /> {t('common.cancel')}
            </Button>
            <Button $size="sm" $variant="primary" onClick={submit} disabled={saving || tabs.length === 0}>
              {saving ? <SpinnerRing size={13} thickness={2} /> : <Save size={13} />} {t('common.save')}
            </Button>
          </Row>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
