// A small, generic form renderer driven by a JSON Schema (as emitted by Pydantic's
// model_json_schema()). Handles the shapes our config models produce: string / integer /
// number / boolean fields, `X | None` (anyOf with a null branch — optional, empty ⇒ unset),
// string enums (→ a SearchSelect), and `dict[str, str]` (additionalProperties: {type: string}
// → a key/value list editor). This is the Phase-7 config-builder shell: point it at a config
// section's schema + slot in a custom widget where the generic one isn't enough. Not exhaustive —
// extend as new config models need shapes it doesn't cover. (Field hints come from a property's
// `description`; add `Field(description=…)` to the Pydantic models to surface them.)
import { type ReactNode } from 'react'
import styled from '@emotion/styled'
import { Plus, X } from 'lucide-react'
import { Input, Field } from './Input'
import { SearchSelect } from './SearchSelect'
import { colors, fontSize, fonts, radius } from '../theme'

export interface JsonSchema {
  type?: string
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  properties?: Record<string, JsonSchema>
  required?: string[]
  anyOf?: JsonSchema[]                       // `X | None` → [{type: X}, {type: 'null'}]
  additionalProperties?: JsonSchema | boolean  // `dict[str, X]` → additionalProperties: {type: X}
  items?: JsonSchema
}

// Peel `X | None` (anyOf with a 'null' branch) → the X branch (keeping the field-level default).
function effective(s: JsonSchema): JsonSchema {
  if (!s.anyOf) return s
  const nonNull = s.anyOf.find((b) => b.type !== 'null') ?? {}
  return { ...nonNull, default: s.default ?? nonNull.default }
}

const Hint = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; margin-top: 3px; line-height: 1.4;`
const Bool = styled.label`
  display: inline-flex; align-items: center; gap: 7px; cursor: pointer;
  font-size: ${fontSize.base}; color: ${colors.text.secondary};
  & input { accent-color: ${colors.blue.main}; }
`
const MapRow = styled.div`display: flex; gap: 6px; align-items: center; margin-bottom: 5px;`
const MiniBtn = styled.button`
  display: inline-flex; align-items: center; gap: 4px; height: 26px; padding: 0 9px; border-radius: ${radius.sm};
  border: 1px dashed ${colors.border}; background: transparent; color: ${colors.text.muted};
  font-size: ${fontSize.micro}; font-family: ${fonts.sans}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`
const SmallX = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 26px; height: 26px; flex-shrink: 0;
  border-radius: ${radius.sm}; border: 1px solid ${colors.border}; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; }
`

function StringMapEditor({ value, onChange }: { value: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const entries = Object.entries(value)
  const replace = (i: number, k: string, v: string) =>
    onChange(Object.fromEntries(entries.map(([ek, ev], idx) => (idx === i ? [k, v] : [ek, ev]))))
  return (
    <div>
      {entries.map(([k, v], i) => (
        <MapRow key={i}>
          <Input value={k} onChange={(e) => replace(i, e.target.value, v)} placeholder="name" style={{ flex: '0 0 35%', minWidth: 0 }} />
          <Input value={v} onChange={(e) => replace(i, k, e.target.value)} placeholder="value" style={{ flex: 1, minWidth: 0 }} />
          <SmallX type="button" title="remove" onClick={() => onChange(Object.fromEntries(entries.filter((_, idx) => idx !== i)))}><X size={12} /></SmallX>
        </MapRow>
      ))}
      <MiniBtn type="button" onClick={() => onChange({ ...value, '': '' })}><Plus size={12} /> add</MiniBtn>
    </div>
  )
}

/** Render an editing form for one object value against its JSON Schema. `undefined` for a key
 *  means "not set" (the backend's `exclude_defaults` then drops it from the file). */
export function SchemaForm({ schema, value, onChange }: {
  schema: JsonSchema
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
}) {
  const props = schema.properties ?? {}
  const required = new Set(schema.required ?? [])
  const set = (key: string, v: unknown) => {
    const next = { ...value }
    if (v === undefined) delete next[key]
    else next[key] = v
    onChange(next)
  }
  return (
    <div>
      {Object.entries(props).map(([key, raw]) => {
        const sub = effective(raw)
        const isReq = required.has(key)
        const label = (sub.title ?? raw.title ?? key) + (isReq ? ' *' : '')
        const cur = value[key]
        const desc = sub.description ?? raw.description
        let control: ReactNode
        if (Array.isArray(sub.enum)) {
          control = (
            <SearchSelect
              value={cur == null ? '' : String(cur)}
              onChange={(v) => set(key, v === '' ? undefined : v)}
              options={sub.enum.map((e) => ({ value: String(e), label: String(e) }))}
              anyLabel={isReq ? undefined : '(default)'}
              placeholder="(default)"
            />
          )
        } else if (sub.type === 'boolean') {
          const checked = cur === undefined ? Boolean(sub.default) : Boolean(cur)
          control = (
            <Bool>
              <input type="checkbox" checked={checked} onChange={(e) => set(key, e.target.checked)} /> {checked ? 'enabled' : 'disabled'}
            </Bool>
          )
        } else if (sub.type === 'object' && sub.additionalProperties && typeof sub.additionalProperties === 'object') {
          const map = (cur && typeof cur === 'object' && !Array.isArray(cur) ? cur : {}) as Record<string, unknown>
          control = (
            <StringMapEditor
              value={Object.fromEntries(Object.entries(map).map(([k, v]) => [k, v == null ? '' : String(v)]))}
              onChange={(v) => set(key, Object.keys(v).length ? v : undefined)}
            />
          )
        } else if (sub.type === 'integer' || sub.type === 'number') {
          control = (
            <Input
              type="number"
              value={cur == null ? '' : String(cur)}
              placeholder={sub.default != null ? `default: ${sub.default}` : (isReq ? 'required' : '')}
              onChange={(e) => {
                const txt = e.target.value
                set(key, txt === '' ? undefined : sub.type === 'integer' ? Math.trunc(Number(txt)) : Number(txt))
              }}
            />
          )
        } else {
          control = (
            <Input
              type="text"
              value={cur == null ? '' : String(cur)}
              placeholder={sub.default ? `default: ${sub.default}` : isReq ? 'required' : ''}
              onChange={(e) => set(key, e.target.value === '' ? undefined : e.target.value)}
            />
          )
        }
        return (
          <div key={key} style={{ marginBottom: 14 }}>
            <Field label={label}>{control}</Field>
            {desc && <Hint>{desc}</Hint>}
          </div>
        )
      })}
    </div>
  )
}
