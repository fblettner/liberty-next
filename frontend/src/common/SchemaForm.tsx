// A generic form renderer driven by a JSON Schema (as emitted by Pydantic's model_json_schema()).
// Handles the shapes our config models produce: string / integer / number / boolean fields, `X | None`
// (anyOf with a 'null' branch — optional, empty ⇒ unset), string enums (→ a SearchSelect), `dict[str, str]`
// (additionalProperties → a key/value editor), `list[str]` (a string-list editor), `list[Model]` (a
// *collapsible* list — each item a nested SchemaForm, so a query with 200 columns is 200 rows you expand
// one at a time), `$ref`-to-a-`$defs` model (resolved), and a SQL field (`str | {dialect: str}` — a textarea,
// or per-dialect textareas if the value is a map). Anything it can't make sense of → a "edit in the raw
// editor" note. The Phase-7 config-builder shell: point it at a section's schema (with its `$defs`) +
// add a custom widget here when a config model needs a shape it doesn't cover.
import { useEffect, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { Plus, X, ChevronRight, ChevronDown, Search } from 'lucide-react'
import { Input, Textarea, Field } from './Input'
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
  anyOf?: JsonSchema[]
  additionalProperties?: JsonSchema | boolean
  items?: JsonSchema
  $ref?: string
  $defs?: Record<string, JsonSchema>
  x_group?: string   // from a Pydantic Field's json_schema_extra — groups the form into tabs
}

type Defs = Record<string, JsonSchema>

function deref(s: JsonSchema, defs: Defs): JsonSchema {
  let cur = s
  for (let i = 0; cur.$ref && i < 5; i++) {
    const name = cur.$ref.replace(/^#\/\$defs\//, '')
    cur = { ...defs[name], ...cur, $ref: undefined } as JsonSchema   // ref overlays (title/description from the property)
  }
  return cur
}
// Normalise a property schema: resolve $ref, then peel `anyOf` — an array branch wins (e.g. the
// `T | list[T] | None` shape → treat as `list[T]`), else the single non-`null` branch, else leave as-is.
function effective(s: JsonSchema, defs: Defs): JsonSchema {
  const r = deref(s, defs)
  if (!r.anyOf) return r
  const branches = r.anyOf.map((b) => deref(b, defs))
  const arr = branches.find((b) => b.type === 'array')
  if (arr) return { ...arr, default: r.default ?? arr.default }
  const nonNull = branches.filter((b) => b.type !== 'null')
  return nonNull.length === 1 ? { ...nonNull[0], default: r.default ?? nonNull[0].default } : r
}
const isObjectModel = (s: JsonSchema) => s.type === 'object' && !!s.properties
const isStringMap = (s: JsonSchema) => s.type === 'object' && !!s.additionalProperties && typeof s.additionalProperties === 'object'

// ── small styled bits ───────────────────────────────────────────────────────
const Hint = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; margin-top: 3px; line-height: 1.4;`
const Bool = styled.label`display: inline-flex; align-items: center; gap: 7px; cursor: pointer; font-size: ${fontSize.base}; color: ${colors.text.secondary}; & input { accent-color: ${colors.blue.main}; }`
const Row = styled.div`display: flex; gap: 6px; align-items: center; margin-bottom: 5px;`
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
const ItemBox = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; margin-bottom: 6px; overflow: hidden;`
const ItemHead = styled.button<{ $open?: boolean }>`
  display: flex; align-items: center; gap: 6px; width: 100%; padding: 6px 9px; text-align: left;
  border: none; background: ${({ $open }) => ($open ? colors.bg.input : 'transparent')}; cursor: pointer;
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  & .name { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { color: ${colors.text.primary}; }
`
const ItemBody = styled.div`padding: 10px 10px 4px; border-top: 1px solid ${colors.border};`
const Complex = styled.div`font-size: ${fontSize.sm}; color: ${colors.text.muted}; font-style: italic;`
const DialectLabel = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.mono}; margin: 6px 0 2px;`

// ── leaf editors ────────────────────────────────────────────────────────────
function StringMapEditor({ value, onChange }: { value: Record<string, string>; onChange: (v: Record<string, string>) => void }) {
  const entries = Object.entries(value)
  const replace = (i: number, k: string, v: string) => onChange(Object.fromEntries(entries.map(([ek, ev], idx) => (idx === i ? [k, v] : [ek, ev]))))
  return (
    <div>
      {entries.map(([k, v], i) => (
        <Row key={i}>
          <Input value={k} onChange={(e) => replace(i, e.target.value, v)} placeholder="name" style={{ flex: '0 0 35%', minWidth: 0 }} />
          <Input value={v} onChange={(e) => replace(i, k, e.target.value)} placeholder="value" style={{ flex: 1, minWidth: 0 }} />
          <SmallX type="button" title="remove" onClick={() => onChange(Object.fromEntries(entries.filter((_, idx) => idx !== i)))}><X size={12} /></SmallX>
        </Row>
      ))}
      <MiniBtn type="button" onClick={() => onChange({ ...value, '': '' })}><Plus size={12} /> add</MiniBtn>
    </div>
  )
}

function StringListEditor({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div>
      {value.map((v, i) => (
        <Row key={i}>
          <Input value={v} onChange={(e) => onChange(value.map((x, idx) => (idx === i ? e.target.value : x)))} style={{ flex: 1, minWidth: 0 }} />
          <SmallX type="button" title="remove" onClick={() => onChange(value.filter((_, idx) => idx !== i))}><X size={12} /></SmallX>
        </Row>
      ))}
      <MiniBtn type="button" onClick={() => onChange([...value, ''])}><Plus size={12} /> add</MiniBtn>
    </div>
  )
}

// the `sql` field: a single string, or a per-dialect map { default = "…", oracle = "…" }
function SqlField({ value, onChange }: { value: unknown; onChange: (v: unknown) => void }) {
  const isMap = value != null && typeof value === 'object' && !Array.isArray(value)
  if (!isMap) {
    return (
      <div>
        <Textarea rows={6} value={value == null ? '' : String(value)} onChange={(e) => onChange(e.target.value === '' ? undefined : e.target.value)} style={{ fontFamily: fonts.mono }} />
        <MiniBtn type="button" style={{ marginTop: 4 }} onClick={() => onChange({ default: value == null ? '' : String(value) })}><Plus size={12} /> per-dialect variants</MiniBtn>
      </div>
    )
  }
  const map = value as Record<string, string>
  const dialects = ['default', ...Object.keys(map).filter((k) => k !== 'default')]
  const set = (d: string, v: string) => onChange({ ...map, [d]: v })
  return (
    <div>
      {dialects.map((d) => (
        <div key={d}>
          <DialectLabel>{d}{d === 'default' ? ' (required)' : ''}</DialectLabel>
          <Row>
            <Textarea rows={4} value={map[d] ?? ''} onChange={(e) => set(d, e.target.value)} style={{ fontFamily: fonts.mono, flex: 1 }} />
            {d !== 'default' && <SmallX type="button" title="remove variant" onClick={() => onChange(Object.fromEntries(Object.entries(map).filter(([k]) => k !== d)))}><X size={12} /></SmallX>}
          </Row>
        </div>
      ))}
      <Row style={{ marginTop: 4 }}>
        <Input placeholder="dialect (e.g. oracle)" onKeyDown={(e) => { if (e.key === 'Enter') { const k = e.currentTarget.value.trim(); if (k && !(k in map)) { set(k, ''); e.currentTarget.value = '' } } }} style={{ flex: '0 0 200px' }} />
        <MiniBtn type="button" onClick={() => { if (Object.keys(map).length <= 1) onChange(map.default ?? '') }} title="back to a single statement (drops the extra variants)">single</MiniBtn>
      </Row>
    </div>
  )
}

// the summary line for a nested-object item in a list (the breadcrumb / list-row label)
function itemSummary(it: Record<string, unknown>, itemSchema: JsonSchema, defs: Defs): string {
  if (typeof it.name === 'string' && it.name) return it.name
  const firstStr = Object.entries(itemSchema.properties ?? {}).find(([, p]) => effective(p, defs).type === 'string')?.[0]
  const v = firstStr ? it[firstStr] : undefined
  return typeof v === 'string' && v ? v : '(unnamed)'
}

// ── drill-in list (nav mode): each item is a clickable row; clicking it navigates into the item ──
const NavListRow = styled.button`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 7px 10px; margin-bottom: 4px; text-align: left;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; cursor: pointer;
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  & .lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  &:hover { border-color: ${colors.blue.border}; color: ${colors.text.primary}; }
`
const SearchRow = styled.div`
  display: flex; align-items: center; gap: 6px; height: 30px; padding: 0 9px; margin-bottom: 8px;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input}; color: ${colors.text.muted};
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; &::placeholder { color: ${colors.text.muted}; } }
`
function ObjectNavList({ itemSchema, defs, value, onChange, onNavigate }: {
  itemSchema: JsonSchema; defs: Defs; value: Record<string, unknown>[]
  onChange: (v: Record<string, unknown>[]) => void
  onNavigate: (index: number, summary: string) => void
}) {
  const [q, setQ] = useState('')
  const needle = q.trim().toLowerCase()
  const rows = value.map((it, i) => ({ i, label: itemSummary(it, itemSchema, defs) }))
  const shown = needle ? rows.filter((r) => r.label.toLowerCase().includes(needle)) : rows
  return (
    <div>
      {value.length > 6 && (
        <SearchRow>
          <Search size={13} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={`filter ${value.length}…`} />
          {q && <SmallX as="span" role="button" title="clear" onClick={() => setQ('')}><X size={12} /></SmallX>}
        </SearchRow>
      )}
      {shown.map(({ i, label }) => (
        <NavListRow key={i} type="button" onClick={() => onNavigate(i, label)}>
          <span className="lbl">{label}</span>
          <SmallX as="span" role="button" title="remove" onClick={(e) => { e.stopPropagation(); onChange(value.filter((_, j) => j !== i)) }}><X size={12} /></SmallX>
          <ChevronRight size={13} />
        </NavListRow>
      ))}
      {shown.length === 0 && needle && <div style={{ color: colors.text.muted, fontSize: fontSize.sm, padding: '4px 2px 8px' }}>no match</div>}
      <MiniBtn type="button" onClick={() => { setQ(''); onChange([...value, {}]); onNavigate(value.length, '(new)') }}><Plus size={12} /> add</MiniBtn>
    </div>
  )
}

// a collapsible list of nested objects (the no-navigator fallback — params / columns / queries / …)
function ObjectListEditor({ itemSchema, defs, value, onChange }: { itemSchema: JsonSchema; defs: Defs; value: Record<string, unknown>[]; onChange: (v: Record<string, unknown>[]) => void }) {
  const [open, setOpen] = useState<number | null>(null)
  const summary = (it: Record<string, unknown>) => itemSummary(it, itemSchema, defs)
  return (
    <div>
      {value.map((it, i) => (
        <ItemBox key={i}>
          <ItemHead $open={open === i} type="button" onClick={() => setOpen(open === i ? null : i)}>
            {open === i ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            <span className="name">{summary(it)}</span>
            <SmallX as="span" role="button" title="remove" onClick={(e) => { e.stopPropagation(); onChange(value.filter((_, idx) => idx !== i)); if (open === i) setOpen(null) }}><X size={12} /></SmallX>
          </ItemHead>
          {open === i && (
            <ItemBody>
              <SchemaForm schema={itemSchema} defs={defs} value={it} onChange={(v) => onChange(value.map((x, idx) => (idx === i ? v : x)))} />
            </ItemBody>
          )}
        </ItemBox>
      ))}
      <MiniBtn type="button" onClick={() => { onChange([...value, {}]); setOpen(value.length) }}><Plus size={12} /> add</MiniBtn>
    </div>
  )
}

// ── the form ────────────────────────────────────────────────────────────────
// Tabs for the field groups (a Pydantic Field's json_schema_extra={"x_group": "…"}). Only shown
// when a model has >1 group; the "General" group (or whichever appears first) is the default tab.
const TabsBar = styled.div`display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 14px; border-bottom: 1px solid ${colors.border}; padding-bottom: 6px;`
const TabBtn = styled.button<{ $active?: boolean }>`
  height: 28px; padding: 0 11px; border-radius: ${radius.sm}; cursor: pointer; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : 'transparent')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  &:hover { color: ${colors.text.primary}; background: ${({ $active }) => ($active ? colors.blue.bg : 'var(--hover-subtle)')}; }
`

/** A breadcrumb hop the navigator can take from this form (drill into a nested object property,
 *  or into item #index of a `list[Model]` property). `SchemaNavigator` consumes these. */
export interface NavSeg { kind: 'prop' | 'item'; key: string; index?: number; label: string }

/** Render an editing form for one object value against its JSON Schema. `undefined` for a key
 *  means "not set" (the backend's `exclude_defaults` then drops it from the file). Pass the
 *  top-level schema's `$defs` so `$ref`s in nested models resolve. When `onNavigate` is given,
 *  `list[Model]` / nested-object properties become drill-in rows (used inside `SchemaNavigator`);
 *  without it they render as inline collapsible accordions. */
export function SchemaForm({ schema, value, onChange, defs, onNavigate }: {
  schema: JsonSchema
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
  defs?: Defs
  onNavigate?: (seg: NavSeg) => void
}) {
  const allDefs = { ...(schema.$defs ?? {}), ...(defs ?? {}) }
  const props = schema.properties ?? {}
  const required = new Set(schema.required ?? [])
  const set = (key: string, v: unknown) => {
    const next = { ...value }
    if (v === undefined) delete next[key]
    else next[key] = v
    onChange(next)
  }
  // group the fields into tabs (a Pydantic Field's `x_group`); ungrouped → "General". Order =
  // first-appearance order, which puts the ungrouped/core fields' group first.
  const groups = new Map<string, [string, JsonSchema][]>()
  for (const [key, raw] of Object.entries(props)) {
    const g = raw.x_group ?? 'General'
    if (!groups.has(g)) groups.set(g, [])
    groups.get(g)!.push([key, raw])
  }
  const groupNames = [...groups.keys()]
  const showTabs = groupNames.length > 1
  const [tab, setTab] = useState(groupNames[0])
  // eslint-disable-next-line react-hooks/exhaustive-deps -- reset to the first tab when the *model* changes
  useEffect(() => { setTab(groupNames[0]) }, [schema.title])
  const activeProps = groups.get(tab) ?? groups.get(groupNames[0]) ?? []

  return (
    <div>
      {showTabs && (
        <TabsBar>
          {groupNames.map((g) => (
            <TabBtn key={g} type="button" $active={(groups.get(tab) ? tab : groupNames[0]) === g} onClick={() => setTab(g)}>{g}</TabBtn>
          ))}
        </TabsBar>
      )}
      {activeProps.map(([key, raw]) => {
        const sub = effective(raw, allDefs)
        const isReq = required.has(key)
        const label = (sub.title ?? raw.title ?? key) + (isReq ? ' *' : '')
        const desc = sub.description ?? raw.description
        const cur = value[key]
        let control: ReactNode

        if (key === 'sql') {
          control = <SqlField value={cur} onChange={(v) => set(key, v)} />
        } else if (Array.isArray(sub.enum)) {
          control = (
            <SearchSelect value={cur == null ? '' : String(cur)} onChange={(v) => set(key, v === '' ? undefined : v)}
              options={sub.enum.map((e) => ({ value: String(e), label: String(e) }))} anyLabel={isReq ? undefined : '(default)'} placeholder="(default)" />
          )
        } else if (sub.type === 'boolean') {
          const checked = cur === undefined ? Boolean(sub.default) : Boolean(cur)
          control = <Bool><input type="checkbox" checked={checked} onChange={(e) => set(key, e.target.checked)} /> {checked ? 'enabled' : 'disabled'}</Bool>
        } else if (sub.type === 'array') {
          const items = effective(sub.items ?? {}, allDefs)
          // `cur` may be a single object for the `T | list[T] | None` shape (a hand-written single rule)
          const arr = Array.isArray(cur) ? (cur as unknown[]) : cur && typeof cur === 'object' ? [cur] : []
          if (isObjectModel(items)) {
            const objs = arr as Record<string, unknown>[]
            const setArr = (v: Record<string, unknown>[]) => set(key, v.length ? v : undefined)
            const labelPlain = label.replace(' *', '')
            control = onNavigate
              ? <ObjectNavList itemSchema={items} defs={allDefs} value={objs} onChange={setArr}
                  onNavigate={(index, summary) => onNavigate({ kind: 'item', key, index, label: `${labelPlain}: ${summary}` })} />
              : <ObjectListEditor itemSchema={items} defs={allDefs} value={objs} onChange={setArr} />
          } else {
            control = <StringListEditor value={arr.map((x) => (x == null ? '' : String(x)))} onChange={(v) => set(key, v.length ? v : undefined)} />
          }
        } else if (isStringMap(sub)) {
          const map = (cur && typeof cur === 'object' && !Array.isArray(cur) ? cur : {}) as Record<string, unknown>
          control = <StringMapEditor value={Object.fromEntries(Object.entries(map).map(([k, v]) => [k, v == null ? '' : String(v)]))} onChange={(v) => set(key, Object.keys(v).length ? v : undefined)} />
        } else if (isObjectModel(sub)) {
          const subValue = (cur && typeof cur === 'object' && !Array.isArray(cur) ? cur : {}) as Record<string, unknown>
          control = onNavigate ? (
            <NavListRow type="button" onClick={() => onNavigate({ kind: 'prop', key, label: label.replace(' *', '') })}>
              <span className="lbl">edit…</span><ChevronRight size={13} />
            </NavListRow>
          ) : (
            <ItemBox><ItemBody style={{ borderTop: 'none' }}>
              <SchemaForm schema={sub} defs={allDefs} value={subValue} onChange={(v) => set(key, Object.keys(v).length ? v : undefined)} />
            </ItemBody></ItemBox>
          )
        } else if (sub.type === 'integer' || sub.type === 'number') {
          control = <Input type="number" value={cur == null ? '' : String(cur)} placeholder={sub.default != null ? `default: ${sub.default}` : isReq ? 'required' : ''}
            onChange={(e) => { const txt = e.target.value; set(key, txt === '' ? undefined : sub.type === 'integer' ? Math.trunc(Number(txt)) : Number(txt)) }} />
        } else if (sub.type === 'string' || sub.type === undefined) {
          control = <Input type="text" value={cur == null ? '' : String(cur)} placeholder={sub.default ? `default: ${sub.default}` : isReq ? 'required' : ''}
            onChange={(e) => set(key, e.target.value === '' ? undefined : e.target.value)} />
        } else {
          control = <Complex>complex value — edit it in the raw editor</Complex>
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
