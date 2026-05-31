// nomaflow step-pipeline editor (NOMAFLOW-UI.md §4) — the ETL-building surface.
// An ordered list of step cards: collapse/expand, up/down reorder, duplicate, delete;
// a per-type "+ Add" toolbar at the bottom.
//
// Increment 4 ships the hand-written sql_copy + sql_query forms (they need live
// connector/query dropdowns). python / ldap_sync / http get a name field + a
// raw-values fallback until increment 5 brings their SchemaForm.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, ArrowUp, ArrowDown, Trash2, Copy, Plus } from 'lucide-react'
import { Input, Select, Button, Checkbox, Tag, StringListEditor, SearchSelect, type SearchSelectOption } from '../../common'
import { api } from '../../api/client'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { ConnectorMeta } from '../../types/connectors'
import type { StepConfig, StepType } from './types'

// ── styled ───────────────────────────────────────────────────────────────────────
const List = styled.div`display: flex; flex-direction: column; gap: 8px;`
const Card = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;`
const Head = styled.div`
  display: flex; align-items: center; gap: 8px; padding: 8px 10px;
  background: ${colors.bg.input}; cursor: pointer;
`
const HeadName = styled.span`font-family: ${fonts.mono}; color: ${colors.text.primary};`
const HeadSummary = styled.span`
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.muted};
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1;
`
const IconBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: transparent;
  color: ${colors.text.muted}; cursor: pointer; flex-shrink: 0;
  &:hover:not(:disabled) { color: ${colors.text.primary}; background: ${colors.bg.card}; }
  &:disabled { opacity: 0.35; cursor: default; }
`
const Body = styled.div`padding: 12px; display: flex; flex-direction: column; gap: 12px;`
const Grid = styled.div`display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px;`
const Field = styled.label<{ $span?: number }>`
  display: flex; flex-direction: column; gap: 4px;
  grid-column: ${({ $span }) => ($span ? `span ${$span}` : 'auto')};
`
const FieldLabel = styled.span`font-size: ${fontSize.sm}; color: ${colors.text.secondary};`
const SubHead = styled.div`
  font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};
  text-transform: uppercase; letter-spacing: 0.04em;
`
const AddBar = styled.div`display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 4px;`
const Note = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted};`
// Grid (not flex) so the key + value columns stay equal width regardless of the value
// widget's natural size — a Select for a short connector name was shrinking to its
// content width and pushing all the space onto the key cell. ``minmax(0, 1fr)`` lets
// long content overflow with text-ellipsis instead of blowing the column wider than 1fr.
const KvRow = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 6px; align-items: center;
  & > *:not(button) { min-width: 0; width: 100%; }
`
const TextArea = styled.textarea`
  width: 100%; min-height: 72px; padding: 8px 10px; resize: vertical;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input}; color: ${colors.text.primary};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm}; outline: none;
  &:focus { border-color: ${colors.blue.main}; }
`

const STEP_TONE: Record<StepType, 'blue' | 'green' | 'orange' | 'purple' | 'neutral' | 'yellow'> = {
  sql_copy: 'blue', sql_query: 'green', python: 'purple', ldap_sync: 'orange', http: 'neutral', call_job: 'yellow',
}
const STEP_TYPES: StepType[] = ['sql_copy', 'sql_query', 'python', 'ldap_sync', 'http', 'call_job']

/** A fresh blank step of *type* — the "+ Add" buttons append one of these. */
function blankStep(type: StepType): StepConfig {
  switch (type) {
    case 'sql_copy':
      return { type, name: '', mode: 'overwrite', type_coercion: 'jde', source: {}, target: {} }
    case 'sql_query':
      return { type, name: '', connector: '', query: '' }
    case 'python':
      return { type, name: '', callable: '' }
    case 'ldap_sync':
      return { type, name: '', server: '', bind_dn: '', search_base: '', target_connector: '', target_query: '' }
    case 'http':
      return { type, name: '', url: '', method: 'GET' }
    case 'call_job':
      return { type, name: '', target_job_id: '' }
  }
}

function stepSummary(s: StepConfig): string {
  const f = s as Record<string, unknown>
  const ep = (e: unknown) => {
    const o = (e ?? {}) as Record<string, unknown>
    return [o.connector, o.schema, o.table].filter(Boolean).join('.')
  }
  switch (s.type) {
    case 'sql_copy': return `${ep(f.source)} → ${ep(f.target)}`
    case 'sql_query': return `${f.connector ?? '?'}.${f.query ?? '?'}`
    case 'python': return String(f.callable ?? '')
    case 'ldap_sync': return String(f.server ?? '')
    case 'http': return `${f.method ?? 'GET'} ${f.url ?? ''}`
    case 'call_job': return `→ ${f.target_job_id ?? '?'}`
    default: return ''
  }
}

// ── predefined key catalog for job-level ``params`` ────────────────────────────────
// The python steps consume well-known kwargs (apps_id / source_connector / target_connector /
// target_schema / …) — a typo here breaks the run with a generic "missing argument"
// downstream. The schema below tells the KeyValueEditor which keys to OFFER from a
// dropdown when the operator clicks "+ Add param", and how to render the VALUE cell
// for each (connector picker, schema picker dependent on a sibling key, plain text).
//
// "Custom" stays available — the catalog is suggestion, not enforcement.
export type ParamWidget =
  | { type: 'text'; placeholder?: string }
  | { type: 'connector' }                                  // dropdown of all SQL connectors
  | { type: 'connector_schema'; dependsOn: string }        // dropdown of schemas for sibling key's connector
  | { type: 'select'; options: string[] }

export interface ParamKeySpec {
  key: string
  label: string
  description?: string
  widget: ParamWidget
  /** Native type the value is serialized as. Without this, the editor stringifies
   *  every value on save and a callable annotated ``int`` receives ``"1"`` — the
   *  regression that motivated this field. ``'string'`` (the default) preserves
   *  the historical behaviour. Server-side ``_build_kwargs`` coerces too, so this
   *  is the cosmetic / disk-shape line of defence. */
  type?: 'string' | 'int' | 'float' | 'bool'
}

export const JOB_PARAM_CATALOG: ParamKeySpec[] = [
  { key: 'apps_id', label: 'apps_id',
    description: 'Target application id (security-* / database-* / out-* jobs read this).',
    // Numeric: the python steps annotate ``apps_id: int`` and asyncpg refuses a string
    // bind value. ``type: 'int'`` makes the editor save an int (not "1") so the disk
    // shape matches the callable contract. Server-side _build_kwargs coerces too as
    // a backstop for legacy TOML.
    type: 'int',
    widget: { type: 'text', placeholder: 'e.g. 1' } },
  { key: 'source_connector', label: 'source_connector',
    description: 'SQL connector the python step reads from.',
    widget: { type: 'connector' } },
  { key: 'source_schema', label: 'source_schema',
    description: 'Schema on the source connector.',
    widget: { type: 'connector_schema', dependsOn: 'source_connector' } },
  { key: 'target_connector', label: 'target_connector',
    description: 'SQL connector the python step writes to.',
    widget: { type: 'connector' } },
  { key: 'target_schema', label: 'target_schema',
    description: 'Schema on the target connector — populated lazily once you pick the connector.',
    widget: { type: 'connector_schema', dependsOn: 'target_connector' } },
]

// ── small key/value editor — used by sql_query step params + job-level params ──────
//
// ``schema`` is optional. When provided (the job-level Shared params editor passes
// JOB_PARAM_CATALOG), the "+ Add param" button opens a picker of predefined keys with
// hints; each predefined value cell renders the right widget (connector dropdown,
// schema dropdown that follows a sibling, …). When omitted (step-level sql_query
// params), the editor stays the original flat key/value pair input.
export function KeyValueEditor({ value, onChange, schema, sqlConnectors }: {
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
  /** Predefined param catalog. When set, "+ Add param" opens a picker; values render
   *  with the matching widget; custom keys stay possible via the "Custom key" option. */
  schema?: ParamKeySpec[]
  /** Required when ``schema`` carries connector / connector_schema widgets. */
  sqlConnectors?: ConnectorMeta[]
}) {
  const { t } = useTranslation()
  const entries = Object.entries(value ?? {})
  // Value is ``unknown`` (not string) so typed params (int / bool / float) keep their
  // native type all the way through to onChange. Without this, every edit round-tripped
  // through String(...) and the next save serialized 1 as "1" — exactly the regression
  // that broke apps_id.
  const setEntry = (i: number, k: string, v: unknown) => {
    const next = entries.map(([ek, ev], idx) => (idx === i ? [k, v] : [ek, ev])) as [string, unknown][]
    onChange(Object.fromEntries(next))
  }
  const removeEntry = (i: number) => onChange(Object.fromEntries(entries.filter((_, idx) => idx !== i)))
  const addCustom = () => onChange({ ...value, '': '' })
  const specFor = (key: string): ParamKeySpec | undefined => schema?.find((s) => s.key === key)
  const addPredefined = (key: string) => {
    if (key in (value ?? {})) return                     // already present — no-op
    // Initial value matches the spec's declared type so the first save preserves it
    // (e.g. apps_id added with no edit lands on disk as null, not "").
    const spec = specFor(key)
    const init: unknown = spec?.type === 'bool' ? false : spec?.type ? null : ''
    onChange({ ...value, [key]: init })
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      {entries.map(([k, v], i) => (
        <KvRow key={i}>
          <Input placeholder="key" value={k}
            onChange={(e) => setEntry(i, e.target.value, v)}
            disabled={!!specFor(k)} />
          <ParamValueCell
            spec={specFor(k)}
            value={v}
            onChange={(nv) => setEntry(i, k, nv)}
            siblings={value ?? {}}
            sqlConnectors={sqlConnectors ?? []}
          />
          <IconBtn type="button" onClick={() => removeEntry(i)}><Trash2 size={13} /></IconBtn>
        </KvRow>
      ))}
      {schema && schema.length > 0 ? (
        <ParamAddPicker
          schema={schema}
          existing={new Set(Object.keys(value ?? {}))}
          onPickPredefined={addPredefined}
          onPickCustom={addCustom}
        />
      ) : (
        <Button type="button" $variant="ghost" $size="sm" onClick={addCustom} style={{ alignSelf: 'flex-start' }}>
          <Plus size={13} /> {t('nomaflow.steps.addParam')}
        </Button>
      )}
    </div>
  )
}

// Renders the right value widget for a predefined param. Plain text fallback when the key
// isn't in the catalog (operator typed a custom key). The value flows in/out as ``unknown``
// so typed params (int / float / bool) keep their native type through the round trip —
// HTML inputs still need a string for display, so we coerce at the boundary here, not
// in the editor's state.
function ParamValueCell({
  spec, value, onChange, siblings, sqlConnectors,
}: {
  spec: ParamKeySpec | undefined
  value: unknown
  onChange: (v: unknown) => void
  siblings: Record<string, unknown>
  sqlConnectors: ConnectorMeta[]
}) {
  // Typed cells handle their own input renderer; only string-shaped paths use this.
  const strValue = value == null ? '' : String(value)

  // Typed inputs — declared on the spec, render the natural widget and emit native types.
  // ``apps_id: int`` shows as a number input; an empty field saves null so the operator
  // can't accidentally land "" on disk (asyncpg would then crash with an even worse error).
  if (spec?.type === 'int' || spec?.type === 'float') {
    const isInt = spec.type === 'int'
    return (
      <Input
        type="number"
        step={isInt ? '1' : 'any'}
        placeholder={spec.widget.type === 'text' ? spec.widget.placeholder ?? 'value' : 'value'}
        value={strValue}
        onChange={(e) => {
          const raw = e.target.value
          if (raw === '') { onChange(null); return }
          const n = isInt ? parseInt(raw, 10) : parseFloat(raw)
          onChange(Number.isFinite(n) ? n : raw)         // fall back to raw so user sees what they typed
        }}
      />
    )
  }
  if (spec?.type === 'bool') {
    return (
      <Checkbox checked={!!value} onChange={(c) => onChange(c)} label="" />
    )
  }

  // Untyped (legacy / custom keys): the string-shaped path.
  if (!spec) {
    return <Input placeholder="value" value={strValue} onChange={(e) => onChange(e.target.value)} />
  }
  const widget = spec.widget
  if (widget.type === 'connector') {
    return (
      <Select value={strValue} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
      </Select>
    )
  }
  if (widget.type === 'connector_schema') {
    const connector = String(siblings[widget.dependsOn] ?? '')
    return <SchemaSelect connector={connector || null} value={strValue} onChange={(v) => onChange(v)} />
  }
  if (widget.type === 'select') {
    return (
      <Select value={strValue} onChange={(e) => onChange(e.target.value)}>
        <option value="">—</option>
        {widget.options.map((opt) => <option key={opt} value={opt}>{opt}</option>)}
      </Select>
    )
  }
  return <Input placeholder={widget.placeholder ?? 'value'} value={strValue} onChange={(e) => onChange(e.target.value)} />
}

// Lazy schema dropdown — fetches schemas for the picked connector once, caches via
// poolSchema's session cache so picking the same connector twice is free. Falls back to
// a plain text input if the fetch returns no schemas (SQLite, missing perms, etc.).
function SchemaSelect({ connector, value, onChange }: {
  connector: string | null; value: string; onChange: (v: string) => void
}) {
  const [schemas, setSchemas] = useState<string[] | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!connector) { setSchemas(null); return }
    let cancelled = false
    setLoading(true)
    import('../../services/poolSchema').then(({ getPoolSchemaNames }) =>
      getPoolSchemaNames(connector).then((r) => {
        if (cancelled) return
        setSchemas(r?.schemas ?? [])
        setLoading(false)
      }),
    )
    return () => { cancelled = true }
  }, [connector])
  if (!connector) {
    return <Input placeholder="pick connector first" value={value} disabled />
  }
  if (loading) {
    return <Input placeholder="loading schemas…" value={value} disabled />
  }
  if (!schemas || schemas.length === 0) {
    // Schema picker has nothing to offer (SQLite / permissions / connector down) —
    // fall through to plain text so the operator can still type a value.
    return <Input placeholder="schema (free text — none discovered)" value={value} onChange={(e) => onChange(e.target.value)} />
  }
  return (
    <Select value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">—</option>
      {schemas.map((s) => <option key={s} value={s}>{s}</option>)}
    </Select>
  )
}

// "+ Add param" button that opens a popover with the catalog of predefined keys
// (greyed when already present) + a "Custom key" fallback. Picking a predefined key
// inserts a row whose value cell renders the right widget; "Custom" inserts a blank
// row matching the original behaviour.
//
// The panel is portaled to document.body with ``position: fixed`` so it escapes the
// JobEditor's ``<Card>`` stacking context (the Steps section below was painting OVER
// the popover otherwise — same problem the TagsField hit + solved the same way).
function ParamAddPicker({
  schema, existing, onPickPredefined, onPickCustom,
}: {
  schema: ParamKeySpec[]
  existing: Set<string>
  onPickPredefined: (key: string) => void
  onPickCustom: () => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const btnRef = useRef<HTMLButtonElement>(null)
  const [coords, setCoords] = useState<{ top: number; left: number; minWidth: number } | null>(null)
  useEffect(() => {
    if (!open) { setCoords(null); return }
    const compute = () => {
      const r = btnRef.current?.getBoundingClientRect()
      if (!r) return
      setCoords({ top: r.bottom + 4, left: r.left, minWidth: Math.max(r.width, 360) })
    }
    compute()
    window.addEventListener('resize', compute)
    // capture:true catches scroll on every ancestor (the JobEditor form is inside a
    // scrolling container) — bubbling scroll skips non-Window scrollers.
    window.addEventListener('scroll', compute, true)
    return () => {
      window.removeEventListener('resize', compute)
      window.removeEventListener('scroll', compute, true)
    }
  }, [open])
  return (
    <div style={{ alignSelf: 'flex-start' }}>
      <Button ref={btnRef as never} type="button" $variant="ghost" $size="sm" onClick={() => setOpen((v) => !v)}>
        <Plus size={13} /> {t('nomaflow.steps.addParam')}
      </Button>
      {open && coords && createPortal(
        <>
          {/* Backdrop swallows outside clicks to close the popover. Sits above the
              app shell but below the panel itself so clicks on the panel still work. */}
          <div style={{ position: 'fixed', inset: 0, zIndex: 9998 }} onClick={() => setOpen(false)} />
          <PickerPanel
            style={{ top: coords.top, left: coords.left, minWidth: coords.minWidth }}
            onClick={(e) => e.stopPropagation()}
          >
            {schema.map((s) => {
              const used = existing.has(s.key)
              return (
                <PickerRow key={s.key} type="button" $disabled={used} disabled={used}
                  onClick={() => { if (!used) { onPickPredefined(s.key); setOpen(false) } }}>
                  <span className="key">{s.label}{used ? ` · ${t('nomaflow.steps.alreadyAdded', '(already added)')}` : ''}</span>
                  {s.description && <span className="desc">{s.description}</span>}
                </PickerRow>
              )
            })}
            <PickerDivider />
            <PickerRow type="button" onClick={() => { onPickCustom(); setOpen(false) }}>
              <span className="key">{t('nomaflow.steps.addCustomParam', 'Custom key…')}</span>
              <span className="desc">{t('nomaflow.steps.addCustomParamDesc', 'Free-text key not in the catalog above.')}</span>
            </PickerRow>
          </PickerPanel>
        </>,
        document.body,
      )}
    </div>
  )
}

const PickerPanel = styled.div`
  position: fixed; z-index: 9999;
  max-height: 360px; overflow-y: auto;
  background: ${colors.bg.dropdown ?? colors.bg.input};
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.32);
  padding: 4px 0;
`
const PickerRow = styled.button<{ $disabled?: boolean }>`
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  width: 100%; padding: 8px 12px; border: 0; background: transparent;
  cursor: ${({ $disabled }) => ($disabled ? 'default' : 'pointer')};
  text-align: left; color: ${colors.text.primary};
  ${({ $disabled }) => ($disabled ? 'opacity: 0.5;' : '')}
  & .key { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; }
  & .desc { color: ${colors.text.muted}; font-size: ${fontSize.micro}; }
  &:hover:not(:disabled) { background: var(--hover-subtle); }
`
const PickerDivider = styled.div`height: 1px; background: ${colors.border}; margin: 4px 0;`

// ── per-type forms ─────────────────────────────────────────────────────────────────
interface FormProps {
  step: StepConfig
  patch: (p: Partial<StepConfig>) => void
  sqlConnectors: ConnectorMeta[]
}

function SqlCopyForm({ step, patch, sqlConnectors }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const src = (f.source ?? {}) as Record<string, unknown>
  const tgt = (f.target ?? {}) as Record<string, unknown>
  const setEndpoint = (key: 'source' | 'target', part: Record<string, unknown>) =>
    patch({ [key]: { ...(f[key] as object ?? {}), ...part } } as Partial<StepConfig>)
  return (
    <>
      <SubHead>{t('nomaflow.steps.source')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.connector')}</FieldLabel>
          <Select value={String(src.connector ?? '')} onChange={(e) => setEndpoint('source', { connector: e.target.value })}>
            <option value="">—</option>
            {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </Select>
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.schema')}</FieldLabel>
          <Input value={String(src.schema ?? '')} onChange={(e) => setEndpoint('source', { schema: e.target.value })} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.table')}</FieldLabel>
          <Input value={String(src.table ?? '')} onChange={(e) => setEndpoint('source', { table: e.target.value })} />
        </Field>
      </Grid>
      <SubHead>{t('nomaflow.steps.target')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.connector')}</FieldLabel>
          <Select value={String(tgt.connector ?? '')} onChange={(e) => setEndpoint('target', { connector: e.target.value })}>
            <option value="">—</option>
            {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </Select>
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.schema')}</FieldLabel>
          <Input value={String(tgt.schema ?? '')} onChange={(e) => setEndpoint('target', { schema: e.target.value })} />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.table')}</FieldLabel>
          <Input value={String(tgt.table ?? '')} onChange={(e) => setEndpoint('target', { table: e.target.value })} />
        </Field>
      </Grid>
      <SubHead>{t('nomaflow.steps.options')}</SubHead>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.mode')}</FieldLabel>
          <Select value={String(f.mode ?? 'overwrite')} onChange={(e) => patch({ mode: e.target.value } as Partial<StepConfig>)}>
            <option value="overwrite">overwrite</option>
            <option value="append">append</option>
            <option value="upsert">upsert</option>
          </Select>
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.typeCoercion')}</FieldLabel>
          <Select value={String(f.type_coercion ?? 'jde')} onChange={(e) => patch({ type_coercion: e.target.value } as Partial<StepConfig>)}>
            <option value="jde">jde</option>
            <option value="none">none</option>
          </Select>
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.decimalMode')}</FieldLabel>
          <Select value={String(f.decimal_mode ?? 'truncate')} onChange={(e) => patch({ decimal_mode: e.target.value } as Partial<StepConfig>)}>
            <option value="truncate">truncate</option>
            <option value="preserve">preserve</option>
          </Select>
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.batchSize')}</FieldLabel>
          <Input
            type="number" min={1}
            value={String(f.batch_size ?? 10000)}
            onChange={(e) => patch({ batch_size: Number(e.target.value) || 10000 } as Partial<StepConfig>)}
          />
        </Field>
        {/* Per-step, per-table list of column names that get full strip() (both ends) instead
            of the default rstrip when the source pool's trim_strings is on. Use for JDE-style
            right-justified codes left-padded with spaces (F0005 = DRKY/DRMCU; F0101 = ABKY).
            Matching is case-insensitive against the introspected column name.

            NB: we pass StringListEditor's emit straight through (no filter). An earlier version
            stripped empty strings here, which silently removed the blank row "Add" just created
            — the operator saw the button do nothing. Empty rows survive in state; the empty
            strings get dropped at save time below (where we strip the field entirely when the
            list is empty) and never reach the runner. */}
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.stripBothColumns', 'Full-strip columns (left + right padding)')}</FieldLabel>
          <StringListEditor
            value={Array.isArray(f.strip_both_columns) ? (f.strip_both_columns as string[]) : []}
            onChange={(v) => patch({ strip_both_columns: v } as Partial<StepConfig>)}
          />
        </Field>
      </Grid>
    </>
  )
}

function SqlQueryForm({ step, patch, sqlConnectors }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const connName = String(f.connector ?? '')
  const conn = sqlConnectors.find((c) => c.name === connName)
  const queries = conn && conn.type === 'sql' ? conn.queries : []
  return (
    <Grid>
      <Field>
        <FieldLabel>{t('nomaflow.steps.connector')}</FieldLabel>
        <Select value={connName} onChange={(e) => patch({ connector: e.target.value } as Partial<StepConfig>)}>
          <option value="">—</option>
          {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
        </Select>
      </Field>
      <Field $span={2}>
        <FieldLabel>{t('nomaflow.steps.query')}</FieldLabel>
        {queries.length > 0 ? (
          <Select value={String(f.query ?? '')} onChange={(e) => patch({ query: e.target.value } as Partial<StepConfig>)}>
            <option value="">—</option>
            {queries.map((q) => <option key={q.name} value={q.name}>{q.name}</option>)}
          </Select>
        ) : (
          <Input
            value={String(f.query ?? '')}
            onChange={(e) => patch({ query: e.target.value } as Partial<StepConfig>)}
            placeholder={t('nomaflow.steps.queryFreeText')}
          />
        )}
      </Field>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.params')}</FieldLabel>
        <KeyValueEditor
          value={(f.params ?? {}) as Record<string, unknown>}
          onChange={(v) => patch({ params: v } as Partial<StepConfig>)}
        />
      </Field>
    </Grid>
  )
}

// Module-level cache for the discovered callable catalog. Multiple python
// steps in the same job (and across job edits in the same session) reuse
// the same in-memory list — avoids hammering the backend on every step
// expansion. ``null`` = not yet fetched; ``[]`` = fetched + empty (no
// plugins installed). The promise field lets concurrent callers await the
// same in-flight request instead of firing N parallel ones.
type CallableDef = { callable: string; module: string; name: string; is_async: boolean; docstring: string | null }
let _callablesCache: CallableDef[] | null = null
let _callablesPromise: Promise<CallableDef[]> | null = null
function loadCallables(): Promise<CallableDef[]> {
  if (_callablesCache) return Promise.resolve(_callablesCache)
  if (_callablesPromise) return _callablesPromise
  _callablesPromise = api.get<{ callables: CallableDef[] }>('/admin/jobs/callables')
    .then((r) => { _callablesCache = r.callables; return r.callables })
    .finally(() => { _callablesPromise = null })
  return _callablesPromise
}

function PythonForm({ step, patch }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  // Callable catalog — fetched once at mount, kept in module-level cache for
  // sibling steps. allowCustom stays on so an operator can type a callable
  // outside the dropdown (e.g. an internal helper that doesn't follow the
  // ``j_*`` convention; the validator at run time is the real gate).
  const [catalog, setCatalog] = useState<CallableDef[]>(_callablesCache ?? [])
  useEffect(() => {
    if (_callablesCache) return  // already fetched
    let cancelled = false
    loadCallables().then((list) => { if (!cancelled) setCatalog(list) }).catch(() => { /* silent */ })
    return () => { cancelled = true }
  }, [])
  const options = useMemo<SearchSelectOption[]>(
    () => catalog.map((c) => ({
      value: c.callable,
      // Mono = the actual callable string the operator stores.
      // Label = the docstring's first line (helps disambiguate two j_* with
      // similar names). Falls back to the callable when no docstring exists.
      mono: c.callable,
      label: c.docstring || c.callable,
    })),
    [catalog],
  )
  return (
    <Grid>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.callable')}</FieldLabel>
        <SearchSelect
          value={String(f.callable ?? '')}
          onChange={(v) => patch({ callable: v } as Partial<StepConfig>)}
          options={options}
          allowCustom
          placeholder={t('nomaflow.steps.callablePlaceholder', 'Pick a callable, or type a module:function…')}
          loading={catalog.length === 0 && _callablesPromise !== null}
        />
      </Field>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.opKwargs')}</FieldLabel>
        <KeyValueEditor
          value={(f.op_kwargs ?? {}) as Record<string, unknown>}
          onChange={(v) => patch({ op_kwargs: v } as Partial<StepConfig>)}
        />
      </Field>
    </Grid>
  )
}

function LdapSyncForm({ step, patch, sqlConnectors }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const text = (key: string, label: string, span?: number, placeholder?: string) => (
    <Field $span={span}>
      <FieldLabel>{label}</FieldLabel>
      <Input
        value={String(f[key] ?? '')}
        onChange={(e) => patch({ [key]: e.target.value } as Partial<StepConfig>)}
        placeholder={placeholder}
      />
    </Field>
  )
  return (
    <>
      <Grid>
        {text('server', t('nomaflow.steps.server'), 3, 'ldaps://ad.example.com')}
        {text('bind_dn', t('nomaflow.steps.bindDn'), 3)}
        {text('bind_password', t('nomaflow.steps.bindPassword'), 3)}
        {text('search_base', t('nomaflow.steps.searchBase'), 3)}
        {text('search_filter', t('nomaflow.steps.searchFilter'), 3)}
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.attributes')}</FieldLabel>
          <Input
            value={(Array.isArray(f.attributes) ? f.attributes : []).join(', ')}
            onChange={(e) => patch({
              attributes: e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
            } as Partial<StepConfig>)}
            placeholder="sAMAccountName, mail, displayName"
          />
        </Field>
        <Field>
          <FieldLabel>{t('nomaflow.steps.targetConnector')}</FieldLabel>
          <Select
            value={String(f.target_connector ?? '')}
            onChange={(e) => patch({ target_connector: e.target.value } as Partial<StepConfig>)}
          >
            <option value="">—</option>
            {sqlConnectors.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
          </Select>
        </Field>
        {text('target_query', t('nomaflow.steps.targetQuery'), 2)}
      </Grid>
      <SubHead>{t('nomaflow.steps.mapping')}</SubHead>
      <KeyValueEditor
        value={(f.mapping ?? {}) as Record<string, unknown>}
        onChange={(v) => patch({ mapping: v } as Partial<StepConfig>)}
      />
    </>
  )
}

function HttpForm({ step, patch }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  return (
    <>
      <Grid>
        <Field>
          <FieldLabel>{t('nomaflow.steps.method')}</FieldLabel>
          <Select value={String(f.method ?? 'GET')} onChange={(e) => patch({ method: e.target.value } as Partial<StepConfig>)}>
            {['GET', 'POST', 'PUT', 'DELETE', 'PATCH'].map((m) => <option key={m} value={m}>{m}</option>)}
          </Select>
        </Field>
        <Field $span={2}>
          <FieldLabel>{t('nomaflow.steps.url')}</FieldLabel>
          <Input
            value={String(f.url ?? '')}
            onChange={(e) => patch({ url: e.target.value } as Partial<StepConfig>)}
            placeholder="https://api.example.com/hook"
          />
        </Field>
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.headers')}</FieldLabel>
          <KeyValueEditor
            value={(f.headers ?? {}) as Record<string, unknown>}
            onChange={(v) => patch({ headers: v } as Partial<StepConfig>)}
          />
        </Field>
        <Field $span={3}>
          <FieldLabel>{t('nomaflow.steps.body')}</FieldLabel>
          <TextArea
            value={typeof f.body === 'string' ? f.body : (f.body == null ? '' : JSON.stringify(f.body, null, 2))}
            onChange={(e) => patch({ body: e.target.value } as Partial<StepConfig>)}
          />
        </Field>
      </Grid>
    </>
  )
}

// ── call_job: pick another job from the catalog ───────────────────────────────────
//
// Reuses the same module-level cache pattern as the PythonForm callable
// catalog — one fetch per browser session, shared across all call_job steps.
// allowCustom stays on (an operator might be drafting a target job that
// doesn't exist yet — let them type the id; the validator at fire time
// surfaces the typo as a clear "target not found" error).
type JobBrief = { id: string; description?: string | null; tags?: string[] }
let _jobIdsCache: JobBrief[] | null = null
let _jobIdsPromise: Promise<JobBrief[]> | null = null
function loadJobIds(): Promise<JobBrief[]> {
  if (_jobIdsCache) return Promise.resolve(_jobIdsCache)
  if (_jobIdsPromise) return _jobIdsPromise
  _jobIdsPromise = api.get<{ jobs: JobBrief[] }>('/admin/jobs')
    .then((r) => { _jobIdsCache = r.jobs; return r.jobs })
    .finally(() => { _jobIdsPromise = null })
  return _jobIdsPromise
}

function CallJobForm({ step, patch }: FormProps) {
  const { t } = useTranslation()
  const f = step as Record<string, unknown>
  const [catalog, setCatalog] = useState<JobBrief[]>(_jobIdsCache ?? [])
  useEffect(() => {
    if (_jobIdsCache) return
    let cancelled = false
    loadJobIds().then((list) => { if (!cancelled) setCatalog(list) }).catch(() => { /* silent */ })
    return () => { cancelled = true }
  }, [])
  const options = useMemo<SearchSelectOption[]>(
    () => catalog.map((j) => ({
      value: j.id,
      mono: j.id,
      label: j.description || j.id,
    })),
    [catalog],
  )
  return (
    <Grid>
      <Field $span={3}>
        <FieldLabel>{t('nomaflow.steps.targetJobId', 'Target job')}</FieldLabel>
        <SearchSelect
          value={String(f.target_job_id ?? '')}
          onChange={(v) => patch({ target_job_id: v } as Partial<StepConfig>)}
          options={options}
          allowCustom
          placeholder={t('nomaflow.steps.targetJobIdPlaceholder', 'Pick the job this step calls…')}
          loading={catalog.length === 0 && _jobIdsPromise !== null}
        />
      </Field>
    </Grid>
  )
}

// ── one step card ──────────────────────────────────────────────────────────────────
function StepCard({ step, index, count, sqlConnectors, onPatch, onMove, onDuplicate, onDelete }: {
  step: StepConfig
  index: number
  count: number
  sqlConnectors: ConnectorMeta[]
  onPatch: (p: Partial<StepConfig>) => void
  onMove: (dir: -1 | 1) => void
  onDuplicate: () => void
  onDelete: () => void
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const Chev = open ? ChevronDown : ChevronRight
  return (
    <Card>
      <Head onClick={() => setOpen((o) => !o)}>
        <Chev size={14} />
        <Tag $tone={STEP_TONE[step.type] ?? 'neutral'}>{step.type}</Tag>
        <HeadName>{step.name || t('nomaflow.steps.unnamed')}</HeadName>
        <HeadSummary>{stepSummary(step)}</HeadSummary>
        <IconBtn type="button" disabled={index === 0} title={t('common.up')}
          onClick={(e) => { e.stopPropagation(); onMove(-1) }}><ArrowUp size={13} /></IconBtn>
        <IconBtn type="button" disabled={index === count - 1} title={t('common.down')}
          onClick={(e) => { e.stopPropagation(); onMove(1) }}><ArrowDown size={13} /></IconBtn>
        <IconBtn type="button" title={t('nomaflow.steps.duplicate')}
          onClick={(e) => { e.stopPropagation(); onDuplicate() }}><Copy size={13} /></IconBtn>
        <IconBtn type="button" title={t('common.delete')}
          onClick={(e) => { e.stopPropagation(); onDelete() }}><Trash2 size={13} /></IconBtn>
      </Head>
      {open && (
        <Body>
          <Field>
            <FieldLabel>{t('nomaflow.steps.name')}</FieldLabel>
            <Input value={step.name} onChange={(e) => onPatch({ name: e.target.value })} />
          </Field>
          {step.type === 'sql_copy' && <SqlCopyForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'sql_query' && <SqlQueryForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'python' && <PythonForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'ldap_sync' && <LdapSyncForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'http' && <HttpForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
          {step.type === 'call_job' && <CallJobForm step={step} patch={onPatch} sqlConnectors={sqlConnectors} />}
        </Body>
      )}
    </Card>
  )
}

// ── the editor ─────────────────────────────────────────────────────────────────────
export default function StepEditor({ steps, onChange, connectors }: {
  steps: StepConfig[]
  onChange: (steps: StepConfig[]) => void
  connectors: ConnectorMeta[] | null
}) {
  const { t } = useTranslation()
  const sqlConnectors = useMemo(
    () => (connectors ?? []).filter((c): c is ConnectorMeta => c.type === 'sql'),
    [connectors],
  )

  const patchStep = (i: number, p: Partial<StepConfig>) =>
    onChange(steps.map((s, idx) => (idx === i ? { ...s, ...p } : s)))
  const moveStep = (i: number, dir: -1 | 1) => {
    const j = i + dir
    if (j < 0 || j >= steps.length) return
    const next = [...steps]
    ;[next[i], next[j]] = [next[j], next[i]]
    onChange(next)
  }
  const duplicateStep = (i: number) => {
    const clone: StepConfig = JSON.parse(JSON.stringify(steps[i]))
    clone.name = `${clone.name}_copy`
    onChange([...steps.slice(0, i + 1), clone, ...steps.slice(i + 1)])
  }
  const deleteStep = (i: number) => onChange(steps.filter((_, idx) => idx !== i))
  const addStep = (type: StepType) => onChange([...steps, blankStep(type)])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <List>
        {steps.map((step, i) => (
          <StepCard
            key={i}
            step={step}
            index={i}
            count={steps.length}
            sqlConnectors={sqlConnectors}
            onPatch={(p) => patchStep(i, p)}
            onMove={(dir) => moveStep(i, dir)}
            onDuplicate={() => duplicateStep(i)}
            onDelete={() => deleteStep(i)}
          />
        ))}
      </List>
      <AddBar>
        <Note>{t('nomaflow.steps.addLabel')}</Note>
        {STEP_TYPES.map((type) => (
          <Button key={type} type="button" $variant="ghost" $size="sm" onClick={() => addStep(type)}>
            <Plus size={13} /> {type}
          </Button>
        ))}
      </AddBar>
    </div>
  )
}
