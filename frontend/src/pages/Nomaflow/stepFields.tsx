// What the forms of a job's steps share: the field grid and its labels, the name = value rows
// (with their catalogue of known names), the schema picker and the comma-separated list. A step's
// form imports from here, never from StepEditor (which imports the forms).
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Trash2, Plus } from 'lucide-react'
import { Input, Button, Checkbox, SearchSelect, type SearchSelectOption } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'
import type { ConnectorMeta } from '../../types/connectors'

// The choices of the shared dropdown: a fixed list of values, the SQL connectors, a connector's queries.
export const asOptions = (values: readonly string[]): SearchSelectOption[] => values.map((v) => ({ value: v, label: v }))
export const connectorOptions = (list: ConnectorMeta[]): SearchSelectOption[] => list.map((c) => ({ value: c.name, label: c.name }))

export const IconBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm}; background: transparent;
  color: ${colors.text.muted}; cursor: pointer; flex-shrink: 0;
  &:hover:not(:disabled) { color: ${colors.text.primary}; background: ${colors.bg.card}; }
  &:disabled { opacity: 0.35; cursor: default; }
`
export const Grid = styled.div`display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px;`
export const Field = styled.label<{ $span?: number }>`
  display: flex; flex-direction: column; gap: 4px;
  grid-column: ${({ $span }) => ($span ? `span ${$span}` : 'auto')};
`
// the same label as every editor's fields (common/Input FieldLabel)
export const FieldLabel = styled.span`font-size: ${fontSize.micro}; font-weight: 600; text-transform: uppercase; letter-spacing: 0.08em; color: ${colors.text.muted};`
export const SubHead = styled.div`
  font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.secondary};
  text-transform: uppercase; letter-spacing: 0.04em;
`
// Grid (not flex) so the key + value columns stay equal width regardless of the value
// widget's natural size — a Select for a short connector name was shrinking to its
// content width and pushing all the space onto the key cell. ``minmax(0, 1fr)`` lets
// long content overflow with text-ellipsis instead of blowing the column wider than 1fr.
export const KvRow = styled.div`
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr) auto;
  gap: 6px; align-items: center;
  & > *:not(button) { min-width: 0; width: 100%; }
`
/** What a field is for, under it: a sentence in plain case — a label stays a few words. */
export const Hint = styled.span`font-size: ${fontSize.micro}; color: ${colors.text.muted}; line-height: 1.5; text-transform: none; letter-spacing: 0; font-weight: 400;`

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
export function KeyValueEditor({ value, onChange, schema, sqlConnectors, heads, keyPlaceholder, valuePlaceholder, addLabel, keyOptions }: {
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
  /** What the two columns hold, said once above the rows ("Column of the table" / "Column of the file"). */
  heads?: [string, string]
  keyPlaceholder?: string
  valuePlaceholder?: string
  /** The Add button's words, when "Add parameter" says too little ("Add a column"). */
  addLabel?: string
  /** Known names for the left column (a table's columns): picked from a list, another can still be typed. */
  keyOptions?: SearchSelectOption[]
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
      {heads && entries.length > 0 && (
        <KvRow>
          <Hint>{heads[0]}</Hint>
          <Hint>{heads[1]}</Hint>
          <span style={{ width: 24 }} />
        </KvRow>
      )}
      {entries.map(([k, v], i) => (
        <KvRow key={i}>
          {keyOptions && keyOptions.length > 0 && !specFor(k) ? (
            <SearchSelect sort allowCustom value={k} onChange={(nk) => setEntry(i, nk, v)}
              options={keyOptions.filter((o) => o.value === k || !(o.value in (value ?? {})))}
              placeholder={keyPlaceholder ?? t('nomaflow.steps.kvName')} />
          ) : (
            <Input placeholder={keyPlaceholder ?? t('nomaflow.steps.kvName')} value={k}
              onChange={(e) => setEntry(i, e.target.value, v)}
              disabled={!!specFor(k)} />
          )}
          <ParamValueCell
            spec={specFor(k)}
            value={v}
            onChange={(nv) => setEntry(i, k, nv)}
            siblings={value ?? {}}
            sqlConnectors={sqlConnectors ?? []}
            placeholder={valuePlaceholder}
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
          <Plus size={13} /> {addLabel ?? t('nomaflow.steps.addParam')}
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
  spec, value, onChange, siblings, sqlConnectors, placeholder,
}: {
  spec: ParamKeySpec | undefined
  value: unknown
  onChange: (v: unknown) => void
  siblings: Record<string, unknown>
  sqlConnectors: ConnectorMeta[]
  /** for a name outside the catalogue */
  placeholder?: string
}) {
  const { t } = useTranslation()
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
    return <Input placeholder={placeholder ?? t('nomaflow.steps.kvValue')} value={strValue} onChange={(e) => onChange(e.target.value)} />
  }
  const widget = spec.widget
  if (widget.type === 'connector') {
    return (
      <SearchSelect sort clearable value={strValue} onChange={(v) => onChange(v)} options={connectorOptions(sqlConnectors)} placeholder="—" />
    )
  }
  if (widget.type === 'connector_schema') {
    const connector = String(siblings[widget.dependsOn] ?? '')
    return <SchemaSelect connector={connector || null} value={strValue} onChange={(v) => onChange(v)} />
  }
  if (widget.type === 'select') {
    return (
      <SearchSelect clearable value={strValue} onChange={(v) => onChange(v)} options={asOptions(widget.options)} placeholder="—" />
    )
  }
  return <Input placeholder={widget.placeholder ?? 'value'} value={strValue} onChange={(e) => onChange(e.target.value)} />
}

// Lazy schema dropdown — fetches schemas for the picked connector once, caches via
// poolSchema's session cache so picking the same connector twice is free. Falls back to
// a plain text input if the fetch returns no schemas (SQLite, missing perms, etc.).
export function SchemaSelect({ connector, value, onChange }: {
  connector: string | null; value: string; onChange: (v: string) => void
}) {
  const { t } = useTranslation()
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
    return <Input placeholder={t('nomaflow.steps.schemaPickConnector')} value={value} disabled />
  }
  if (loading) {
    return <Input placeholder={t('nomaflow.steps.schemaLoading')} value={value} disabled />
  }
  if (!schemas || schemas.length === 0) {
    // Schema picker has nothing to offer (SQLite / permissions / connector down) —
    // fall through to plain text so the operator can still type a value.
    return <Input placeholder={t('nomaflow.steps.schemaFree')} value={value} onChange={(e) => onChange(e.target.value)} />
  }
  return (
    <SearchSelect sort clearable allowCustom value={value} onChange={(v) => onChange(v)} options={asOptions(schemas)} placeholder="—" />
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
  const [coords, setCoords] = useState<{ top?: number; bottom?: number; left: number; minWidth: number; maxHeight: number } | null>(null)
  useEffect(() => {
    if (!open) { setCoords(null); return }
    const compute = () => {
      const r = btnRef.current?.getBoundingClientRect()
      if (!r) return
      // Open downward when the panel fits below the button, else upward (the form sits at the
      // bottom of a scrolling page more often than not); cap the height to the room available.
      const below = window.innerHeight - r.bottom - 8
      const above = r.top - 8
      const wanted = 360
      const base = { left: r.left, minWidth: Math.max(r.width, 360) }
      if (below >= Math.min(wanted, above)) setCoords({ ...base, top: r.bottom + 4, maxHeight: Math.min(wanted, below) })
      else setCoords({ ...base, bottom: window.innerHeight - r.top + 4, maxHeight: Math.min(wanted, above) })
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
            style={{ top: coords.top, bottom: coords.bottom, left: coords.left, minWidth: coords.minWidth, maxHeight: coords.maxHeight }}
            onClick={(e) => e.stopPropagation()}
          >
            {schema.map((s) => {
              const used = existing.has(s.key)
              return (
                <PickerRow key={s.key} type="button" $disabled={used} disabled={used}
                  onClick={() => { if (!used) { onPickPredefined(s.key); setOpen(false) } }}>
                  <span className="key">{s.label}{used ? ` · ${t('nomaflow.steps.alreadyAdded')}` : ''}</span>
                  {s.description && <span className="desc">{s.description}</span>}
                </PickerRow>
              )
            })}
            <PickerDivider />
            <PickerRow type="button" onClick={() => { onPickCustom(); setOpen(false) }}>
              <span className="key">{t('nomaflow.steps.addCustomParam')}</span>
              <span className="desc">{t('nomaflow.steps.addCustomParamDesc')}</span>
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

/** A comma-separated list typed freely: the text is local state (so a trailing comma survives
 *  the keystroke) and the parsed list goes out on every change. */
export function ListInput({ value, onChange, placeholder, disabled }: { value: string[]; onChange: (v: string[]) => void; placeholder?: string; disabled?: boolean }) {
  const joined = value.join(', ')
  const [text, setText] = useState(joined)
  const [lastValue, setLastValue] = useState(joined)
  if (joined !== lastValue) {       // changed from outside (another step selected, a reset): resync
    setLastValue(joined)
    if (text.split(',').map((s) => s.trim()).filter(Boolean).join(', ') !== joined) setText(joined)
  }
  return (
    <Input value={text} placeholder={placeholder} disabled={disabled}
      onChange={(e) => { setText(e.target.value); onChange(e.target.value.split(',').map((s) => s.trim()).filter(Boolean)) }} />
  )
}
