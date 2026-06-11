// A generic form renderer driven by a JSON Schema (as emitted by Pydantic's model_json_schema()).
// Handles the shapes our config models produce: string / integer / number / boolean fields, `X | None`
// (anyOf with a 'null' branch — optional, empty ⇒ unset), string enums (→ a SearchSelect), `dict[str, str]`
// (additionalProperties → a key/value editor), `list[str]` (a string-list editor), `list[Model]` (a
// *collapsible* list — each item a nested SchemaForm, so a query with 200 columns is 200 rows you expand
// one at a time), `$ref`-to-a-`$defs` model (resolved), and a SQL field (`str | {dialect: str}` — a textarea,
// or per-dialect textareas if the value is a map). Anything it can't make sense of → a "edit in the raw
// editor" note. The Phase-7 config-builder shell: point it at a section's schema (with its `$defs`) +
// add a custom widget here when a config model needs a shape it doesn't cover.
//
// Dropdowns for `format` / `rules` / `dialect` / `method` / `align` / `auth_type` are driven by the
// **framework enums** — v2's port of v1's `ly_enum`-for-the-framework table. The Pydantic field
// carries `json_schema_extra={"x_enum_ref": "DICTIONARY_TYPE"}`; the enum values + descriptions come
// from `GET /admin/config/schema → framework_enums`, threaded through `FrameworkEnumsContext`. A
// Literal-typed `x_enum_ref` field renders as a strict SearchSelect (the value MUST be in the set);
// a free-text `x_enum_ref` field renders as a combobox (typing a custom value commits).
import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { Plus, X, ChevronRight, ChevronDown, Search, Edit3, Copy, GripVertical, Wand2 } from 'lucide-react'
import { Checkbox } from './Checkbox'
import { Input, PasswordInput, Field } from './Input'
import { SearchSelect, type SearchSelectOption } from './SearchSelect'
import { SqlEditor } from './SqlEditor'
import type { WizardStatementType } from './SqlWizardModal'
import { colors, fontSize, fonts, radius } from '../theme'

export interface JsonSchema {
  type?: string
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  examples?: unknown[]   // a Pydantic Field's `examples=[…]` — for free-text fields, surfaced as a combobox datalist
  /** JSON Schema's standard `format` keyword — we honour `"password"` (renders as a masked input
   *  with a reveal-eye toggle, for ENC: secrets like a pool password / API connector auth_token). */
  format?: string
  properties?: Record<string, JsonSchema>
  required?: string[]
  anyOf?: JsonSchema[]
  /** Pydantic discriminated unions emit `oneOf` + `discriminator` rather than `anyOf`. We honour
   *  both: arrays of discriminated objects (e.g. dashboard widgets = ChartWidget | KpiWidget)
   *  resolve to the concrete branch via `discriminator.propertyName` on the item value. */
  oneOf?: JsonSchema[]
  discriminator?: { propertyName: string; mapping?: Record<string, string> }
  additionalProperties?: JsonSchema | boolean
  items?: JsonSchema
  $ref?: string
  $defs?: Record<string, JsonSchema>
  x_group?: string   // from a Pydantic Field's json_schema_extra — groups the form into tabs
  /** Model-level (json_schema_extra on the BaseModel): field names to build a list-row summary
   *  from, joined by " · " (skipping empty). e.g. a union source shows "connector · query" instead
   *  of just its first string field. */
  x_summary?: string[]
  /** This field's options come from `framework_enums[<ref>]` (renders as a SearchSelect). */
  x_enum_ref?: string
  /** Like `x_enum_ref`, but the ref is picked at render-time from a sibling field's value:
   *  `{field: "rules", map: {ENUM: "ENUM_IDS", LOOKUP: "LOOKUP_IDS", BOOLEAN: "BOOLEAN_TRUE_VALUES"}}`.
   *  The form watches the sibling and swaps the dropdown when it changes. */
  x_enum_ref_when?: { field: string; map: Record<string, string> }
  /** Like `x_enum_ref`, but the ref NAME is built from a driver field found on the nearest ENCLOSING
   *  object (this object's siblings first, then up the ancestor chain) — `<prefix><driver value>`.
   *  Lets a deeply-nested field key its options off a parent's value, e.g. a lookup bind's `param`
   *  resolving the enclosing column/rule's `rules_values` → that lookup's query params
   *  (`LOOKUP_PARAMS__<lookup id>`). Falls through to no enum when no ancestor declares the field. */
  x_enum_ref_ancestor?: { field: string; prefix: string }
  /** For a `dict[str, T]` field, the KEY is drawn from this framework-enum (the value editor stays
   *  whatever the additionalProperties type produces). e.g. `l: dict[str, str]` for translations
   *  uses `SUPPORTED_LANGUAGES` so the user picks "fr" / "Français" instead of typing a code. */
  x_key_enum_ref?: string
  /** Per-field placeholder text. Surfaces the convention or expected shape (e.g. ``AUD_<TABLE>`` /
   *  ``e.g. https://api.example.com``) without hard-coding a default value. Without this, the
   *  string-input placeholder falls back to ``default: <value>`` or ``required``. */
  x_placeholder?: string
  /** Hide this field unless a sibling field matches a given value. Lets a form scope itself
   *  to one of several modes — e.g. ApiConnectorConfig hides every OAuth2 field unless
   *  ``auth_type === "oauth2"``. ``value`` can be a single value or a list of allowed values
   *  (any match). Hidden fields are not rendered at all; their stored value (if any) stays in
   *  the model — switching the gate back surfaces it again. */
  x_visible_when?: { field: string; value: string | string[] }
  /** Case-normalise the string value on save. Operator can type anything; the saved TOML
   *  carries the normalised form. ``"upper"`` matches the v1 convention for column names
   *  (USR_ID / APPS_ID style) + lines up with how the SQL connector's filter-wrap compares
   *  hint names. Applies to plain string Inputs, SearchSelect ``allowCustom`` writes, and
   *  every entry of a ``list[str]`` StringListEditor. */
  x_case?: 'upper' | 'lower'
}


/** Normalise a string value according to ``x_case``. Whitespace is left intact so an
 *  operator's intentional pad (rare but possible) isn't silently stripped. */
function applyCase(v: string, sub: JsonSchema): string {
  if (sub.x_case === 'upper') return v.toUpperCase()
  if (sub.x_case === 'lower') return v.toLowerCase()
  return v
}

/** One entry in the framework-enum registry (server-rendered, fetched via /admin/config/schema).
 *  `mono` is an optional override for the SearchSelect's left mono column — defaults to `value`.
 *  Used by `MENU_TARGETS` to show a table's base name in mono while storing the read-query
 *  name as the value (e.g. mono="F0005", label="Address Book Master", value="f0005_get"). */
export interface FrameworkEnumValue { value: string; label: string; mono?: string }
export interface FrameworkEnum { label: string; values: FrameworkEnumValue[] }
export type FrameworkEnums = Record<string, FrameworkEnum>

/** Threaded through the builder shell — `null` means "no enums available", fields fall back to their
 *  Literal `enum` (strict SearchSelect) or to a plain Input. Provided by the Settings page once,
 *  consumed deeply by every SchemaForm in the tree (nested ObjectListEditor / drill-in pages). */
export const FrameworkEnumsContext = createContext<FrameworkEnums | null>(null)

/** The currently-edited SQL connector — when set, the `sql` field's Monaco editor enables
 *  schema-aware autocomplete against that connector's pool (GET /api/sql/{c}/_schema). Threaded
 *  via context so nested SchemaForms (drill-in list items, accordions) all pick it up without
 *  having to pass `sqlConnector` through every helper. `ConnectorsTableEditor` provides it. */
export const SqlConnectorContext = createContext<string | undefined>(undefined)

/** The statement kind of the SQL field being edited (the CRUD tab: read→SELECT, update→UPDATE,
 *  insert→INSERT, delete→DELETE). Threaded into the SqlEditor's wizard so it builds the right kind
 *  and locks read-only on a mismatch. `ConnectorsTableEditor` provides it per active tab; unset
 *  elsewhere (the wizard then infers from the SQL). */
export const SqlStatementContext = createContext<WizardStatementType | undefined>(undefined)

/** Resolve a field's effective enum ref: prefer `x_enum_ref_when` (which switches by a sibling
 *  field's current value) over plain `x_enum_ref`. Returns `null` when no ref applies (the
 *  conditional rule fell through, or neither annotation is set). */
function effectiveEnumRef(
  sub: JsonSchema,
  siblings: Record<string, unknown>,
  ancestors: Record<string, unknown>[] = [],
): string | null {
  if (sub.x_enum_ref_ancestor) {
    const { field, prefix } = sub.x_enum_ref_ancestor
    // Nearest scope that declares the driver field: this object first, then up the ancestor chain.
    for (const scope of [siblings, ...ancestors]) {
      const v = scope?.[field]
      if (typeof v === 'string' && v) return prefix + v
    }
    return null  // no enclosing object set the driver → fall through to a plain (free-text) input
  }
  if (sub.x_enum_ref_when) {
    const driver = siblings[sub.x_enum_ref_when.field]
    if (typeof driver === 'string') {
      const ref = sub.x_enum_ref_when.map[driver]
      if (ref) return ref
    }
    return null  // `x_enum_ref_when` declares the conditional behaviour — falling through means no enum
  }
  return sub.x_enum_ref ?? null
}

/** Pick the framework enum behind a ref, or `null` when the registry doesn't know it. */
function enumFor(ref: string | null, enums: FrameworkEnums | null): FrameworkEnum | null {
  if (!ref || !enums) return null
  return enums[ref] ?? null
}

/** Build SearchSelect options from a framework enum, optionally constraining to a Literal's `enum`
 *  (so a `Literal[…]` field driven by an enum drops values that aren't in its set). */
function enumOptions(fe: FrameworkEnum, allowed: Set<string> | null): SearchSelectOption[] {
  const vals = allowed ? fe.values.filter((v) => allowed.has(v.value)) : fe.values
  return vals.map((v) => ({ value: v.value, label: v.label, mono: v.mono ?? v.value }))
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
// Outer-property metadata (`description`/`title`/`default`/`x_group`/`x_enum_ref`) lives at the
// `anyOf` level for Pydantic Optional fields — fold it onto the chosen branch so the renderer
// still sees `x_enum_ref` on a `str | None` field after the null branch is peeled.
function mergePeel(outer: JsonSchema, branch: JsonSchema): JsonSchema {
  // Branch wins on type-shape keys (`type`, `enum`, `items`, `properties`, `additionalProperties`,
  // `$ref`); outer wins on metadata. Spread order: outer first, then branch — then the explicit
  // overrides below restore outer's metadata where the branch left them blank.
  return {
    ...outer,
    ...branch,
    default: outer.default ?? branch.default,
    description: outer.description ?? branch.description,
    title: outer.title ?? branch.title,
    x_group: outer.x_group ?? branch.x_group,
    x_enum_ref: outer.x_enum_ref ?? branch.x_enum_ref,
    x_enum_ref_when: outer.x_enum_ref_when ?? branch.x_enum_ref_when,
    x_enum_ref_ancestor: outer.x_enum_ref_ancestor ?? branch.x_enum_ref_ancestor,
    x_key_enum_ref: outer.x_key_enum_ref ?? branch.x_key_enum_ref,
    anyOf: undefined,
  } as JsonSchema
}
function effective(s: JsonSchema, defs: Defs): JsonSchema {
  const r = deref(s, defs)
  if (!r.anyOf) return r
  const branches = r.anyOf.map((b) => deref(b, defs))
  const arr = branches.find((b) => b.type === 'array')
  if (arr) return mergePeel(r, arr)
  const nonNull = branches.filter((b) => b.type !== 'null')
  return nonNull.length === 1 ? mergePeel(r, nonNull[0]) : r
}
const isObjectModel = (s: JsonSchema) => s.type === 'object' && !!s.properties
const isStringMap = (s: JsonSchema) => s.type === 'object' && !!s.additionalProperties && typeof s.additionalProperties === 'object'

/** True iff *s* is a Pydantic discriminated union of object models — `oneOf: [...]` with a
 *  `discriminator: {propertyName: …}`. Used by the array-handling branch to take the object-list
 *  path (drill-in / accordion) instead of the string-list fallback. */
function isDiscriminatedUnion(s: JsonSchema): boolean {
  if (!s.oneOf || !s.discriminator) return false
  return s.oneOf.length > 0
}

/** Resolve a discriminated-union schema to the concrete branch matching *value*'s discriminator.
 *  Returns the original schema when *s* isn't a union or the discriminator value isn't in the
 *  mapping (we render the union's structural shape — empty form — so the operator can still pick
 *  a type via the dropdown the next render produces). */
function resolveUnionBranch(s: JsonSchema, value: Record<string, unknown> | undefined, defs: Defs): JsonSchema {
  if (!isDiscriminatedUnion(s) || !s.discriminator) return s
  const propName = s.discriminator.propertyName
  const discr = value?.[propName]
  if (typeof discr !== 'string') return s
  // Two ways the spec maps a discriminator value → schema:
  //   1. an explicit `discriminator.mapping` entry → a `$ref` string
  //   2. each branch carries the matching `const`/`enum` on the discriminator field
  // Pydantic emits (1); we honour (2) too for hand-authored schemas. Either way, deref the
  // resolved branch + carry the parent's `$defs` so downstream lookups still work.
  const mapping = s.discriminator.mapping
  if (mapping && discr in mapping) {
    const branch = deref({ $ref: mapping[discr] }, defs)
    return { ...branch, $defs: branch.$defs ?? defs }
  }
  for (const raw of s.oneOf ?? []) {
    const branch = deref(raw, defs)
    const propSchema = branch.properties?.[propName]
    const enumSet = propSchema?.enum
    if (Array.isArray(enumSet) && enumSet.map(String).includes(discr)) {
      return { ...branch, $defs: branch.$defs ?? defs }
    }
  }
  return s
}

// ── small styled bits ───────────────────────────────────────────────────────
const Hint = styled.div`font-size: ${fontSize.micro}; color: ${colors.text.muted}; margin-top: 3px; line-height: 1.4;`
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
// Inline action trigger rendered next to query-bearing SearchSelects (LookupDef.query,
// ChartConfig.query, SequenceDef.query/previous_query). Hosts the Edit / Clone / Add
// trio — same look + size everywhere a query is picked. Disabled when the connector
// isn't resolvable (the apply layer needs the connector to open the right editor).
const InlineActionBtn = styled.button`
  display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px;
  flex-shrink: 0; border-radius: ${radius.sm}; border: 1px solid ${colors.border};
  background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover:not(:disabled) { color: ${colors.text.primary}; border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
  &:disabled { opacity: 0.35; cursor: not-allowed; }
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
// `keyEnum` (when given) renders the row's KEY as a SearchSelect over the framework enum's values
// — for translation maps (`l: dict[str, str]` with x_key_enum_ref="SUPPORTED_LANGUAGES") the user
// picks "fr / Français" from a dropdown instead of typing `fr` blindly. Picks that already exist in
// the map are filtered out of the per-row option list so the user can't duplicate a language. The
// VALUE editor stays a free-text Input either way.
function StringMapEditor({ value, onChange, keyEnum }: {
  value: Record<string, string>
  onChange: (v: Record<string, string>) => void
  keyEnum?: FrameworkEnum | null
}) {
  const entries = Object.entries(value)
  const replaceAt = (i: number, k: string, v: string) =>
    onChange(Object.fromEntries(entries.map(([ek, ev], idx) => (idx === i ? [k, v] : [ek, ev]))))
  const usedKeys = new Set(entries.map(([k]) => k))
  const optionsFor = (currentKey: string): SearchSelectOption[] => {
    if (!keyEnum) return []
    return keyEnum.values
      .filter((v) => v.value === currentKey || !usedKeys.has(v.value))
      .map((v) => ({ value: v.value, label: v.label, mono: v.value }))
  }
  return (
    <div>
      {entries.map(([k, v], i) => (
        <Row key={i}>
          {keyEnum ? (
            <div style={{ flex: '0 0 35%', minWidth: 0, display: 'flex' }}>
              <SearchSelect value={k} onChange={(nv) => replaceAt(i, nv, v)} options={optionsFor(k)} placeholder="language" />
            </div>
          ) : (
            <Input value={k} onChange={(e) => replaceAt(i, e.target.value, v)} placeholder="name" style={{ flex: '0 0 35%', minWidth: 0 }} />
          )}
          <Input value={v} onChange={(e) => replaceAt(i, k, e.target.value)} placeholder="value" style={{ flex: 1, minWidth: 0 }} />
          <SmallX type="button" title="remove" onClick={() => onChange(Object.fromEntries(entries.filter((_, idx) => idx !== i)))}><X size={12} /></SmallX>
        </Row>
      ))}
      <MiniBtn type="button" onClick={() => onChange({ ...value, '': '' })}><Plus size={12} /> add</MiniBtn>
    </div>
  )
}

export function StringListEditor(
  { value, onChange, options, allowCustom = true }: {
    value: string[]
    onChange: (v: string[]) => void
    /** When provided, each row renders as a SearchSelect over these options instead of a
     *  free-text Input. Used by ``list[str]`` fields with ``x_enum_ref`` (e.g. Screen's
     *  ``initial_group_by``: a multi-select over the read query's columns). */
    options?: SearchSelectOption[]
    /** Only meaningful when ``options`` is set — passes through to SearchSelect.allowCustom
     *  so the operator can type a value the registry doesn't know (defaults to true; flip
     *  off for strict Literal-typed enums). */
    allowCustom?: boolean
  },
) {
  return (
    <div>
      {value.map((v, i) => (
        <Row key={i}>
          {options ? (
            <div style={{ flex: 1, minWidth: 0 }}>
              <SearchSelect
                value={v}
                options={options}
                onChange={(nv) => onChange(value.map((x, idx) => (idx === i ? nv : x)))}
                allowCustom={allowCustom}
                placeholder="(pick…)"
              />
            </div>
          ) : (
            <Input
              value={v}
              onChange={(e) => onChange(value.map((x, idx) => (idx === i ? e.target.value : x)))}
              style={{ flex: 1, minWidth: 0 }}
            />
          )}
          <SmallX type="button" title="remove" onClick={() => onChange(value.filter((_, idx) => idx !== i))}><X size={12} /></SmallX>
        </Row>
      ))}
      <MiniBtn type="button" onClick={() => onChange([...value, ''])}><Plus size={12} /> add</MiniBtn>
    </div>
  )
}

// the `sql` field: a single string, or a per-dialect map { default = "…", oracle = "…" }. Renders
// as a Monaco SQL editor (registered in services/monaco.ts) — keyword colouring, comments, theme-aware;
// the `rows` prop mirrors the old `<Textarea rows={n}>` sizing so the form's vertical rhythm is unchanged.
// An empty editor maps to `undefined` (an absent `sql` field) to match the old textarea's behaviour.
// Reads the active connector from `SqlConnectorContext` (when set, schema-aware autocomplete is on).
function SqlField({ value, onChange }: { value: unknown; onChange: (v: unknown) => void }) {
  const connector = useContext(SqlConnectorContext)
  const statementType = useContext(SqlStatementContext)
  const isMap = value != null && typeof value === 'object' && !Array.isArray(value)
  if (!isMap) {
    const text = value == null ? '' : String(value)
    return (
      <div>
        <SqlEditor value={text} rows={14} onChange={(v) => onChange(v === '' ? undefined : v)} connector={connector} statementType={statementType} />
        <MiniBtn type="button" style={{ marginTop: 4 }} onClick={() => onChange({ default: text })}><Plus size={12} /> per-dialect variants</MiniBtn>
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
          <Row style={{ alignItems: 'flex-start' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <DialectLabel>{d}{d === 'default' ? ' (required)' : ''}</DialectLabel>
              <SqlEditor value={map[d] ?? ''} rows={10} onChange={(v) => set(d, v)} connector={connector} statementType={statementType} />
            </div>
            {d !== 'default' && <SmallX type="button" title="remove variant" style={{ marginTop: 22 }} onClick={() => onChange(Object.fromEntries(Object.entries(map).filter(([k]) => k !== d)))}><X size={12} /></SmallX>}
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
  // Pydantic discriminated unions don't have top-level `properties` — they live on each `oneOf`
  // branch. Resolve the item's branch via its discriminator value first; without this the
  // fallback below returns "(unnamed)" and (worse) the row may render as String(obj) when the
  // outer array handler treats the union as a non-object items list.
  const resolved = resolveUnionBranch(itemSchema, it, defs)
  // Model-level ``x_summary`` — explicit fields to compose the row label from (e.g. a lookup union
  // source: "connector · query" rather than just the first string field). Wins when set.
  const summaryFields = resolved.x_summary ?? itemSchema.x_summary
  if (Array.isArray(summaryFields) && summaryFields.length) {
    // A summary field may be a list (e.g. a rules_when's ``value = ["6","8"]``) — join it with "/"
    // so the row reads "FSSETY · 6/8 · get_form_name" and two entries on the same discriminator
    // are distinguishable (a bare string field still renders as-is).
    const parts = summaryFields
      .map((f) => {
        const v = it[f]
        if (Array.isArray(v)) return v.filter((x): x is string => typeof x === 'string' && x !== '').join('/')
        return typeof v === 'string' ? v : ''
      })
      .filter((v) => v !== '')
    if (parts.length) return parts.join(' · ')
  }
  if (typeof it.name === 'string' && it.name) return it.name
  if (typeof it.label === 'string' && it.label) {
    // Discriminated unions: prefer `<discriminator value> · <label>` (e.g. `chart · Users by app`)
    // so the row reads as both the variant and the user-given title.
    if (isDiscriminatedUnion(itemSchema) && resolved.discriminator) {
      const dv = it[resolved.discriminator.propertyName ?? '']
      if (typeof dv === 'string' && dv) return `${dv} · ${it.label}`
    }
    return it.label
  }
  // For a discriminated union, surface the discriminator value at least — "chart" / "kpi" reads
  // better than "(unnamed)" when the operator hasn't yet given the widget a label.
  if (isDiscriminatedUnion(itemSchema) && resolved !== itemSchema) {
    const propName = resolved.discriminator?.propertyName ?? itemSchema.discriminator?.propertyName
    const dv = propName ? it[propName] : undefined
    if (typeof dv === 'string' && dv) return dv
  }
  const firstStr = Object.entries(resolved.properties ?? {}).find(([, p]) => effective(p, defs).type === 'string')?.[0]
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
  & .grip { cursor: grab; }
  &:hover { border-color: ${colors.blue.border}; color: ${colors.text.primary}; }
  &[draggable='true'] { cursor: grab; }
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
  // Drag-reorder the items (their stored order IS the display order — e.g. a screen's columns
  // render in this sequence). Disabled while filtering (the visible rows aren't the full order, so
  // a drop index would be ambiguous); clear the filter to reorder.
  const dragFrom = useRef<number | null>(null)
  const reorder = (from: number | null, to: number) => {
    if (from == null || from === to) return
    const next = value.slice()
    const [moved] = next.splice(from, 1)
    next.splice(to, 0, moved)
    onChange(next)
  }
  const canReorder = !needle && value.length > 1
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
        <NavListRow
          key={i} type="button" onClick={() => onNavigate(i, label)}
          draggable={canReorder}
          onDragStart={() => { dragFrom.current = i }}
          onDragOver={canReorder ? (e) => e.preventDefault() : undefined}
          onDrop={canReorder ? (e) => { e.preventDefault(); reorder(dragFrom.current, i); dragFrom.current = null } : undefined}
        >
          {canReorder && <GripVertical size={13} className="grip" aria-label="drag to reorder" />}
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
function ObjectListEditor({ itemSchema, defs, value, onChange, ancestors }: { itemSchema: JsonSchema; defs: Defs; value: Record<string, unknown>[]; onChange: (v: Record<string, unknown>[]) => void; ancestors?: Record<string, unknown>[] }) {
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
              <SchemaForm schema={itemSchema} defs={defs} value={it} onChange={(v) => onChange(value.map((x, idx) => (idx === i ? v : x)))} ancestors={ancestors} />
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
/** The set of ``x_enum_ref`` values whose field picks a CONNECTOR QUERY — these are the
 *  fields that get an Edit-query button next to the picker when ``onEditQuery`` is set.
 *  Add new query-bearing enum refs here as they appear in the backend models.
 *
 *  ``LOOKUP_QUERIES``  — LookupDef.query, SequenceDef.query/previous_query (dictionary)
 *  ``CHART_QUERIES``   — ChartConfig.query (charts.toml)
 */
const QUERY_ENUM_REFS = new Set<string>(['LOOKUP_QUERIES', 'CHART_QUERIES'])

export function SchemaForm({ schema, value, onChange, defs, onNavigate, onEditQuery, onCloneQuery, onAddQuery, onGenerateQuery, hiddenGroups, hiddenFields, fieldNotes, ancestors }: {
  schema: JsonSchema
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
  defs?: Defs
  onNavigate?: (seg: NavSeg) => void
  /** The chain of ENCLOSING objects (nearest first) — threaded down through nested object fields,
   *  inline list items, and drill-in levels so a field's ``x_enum_ref_ancestor`` can read a driver
   *  field off the nearest parent that sets it. Empty at the form root. */
  ancestors?: Record<string, unknown>[]
  /** x_group tab names to omit entirely (the caller decides per-context — e.g. hide "Lookup"
   *  unless the column's effective rule is a lookup). Fields in a hidden group don't render. */
  hiddenGroups?: string[]
  /** Field names to omit (caller-driven, like hiddenGroups but per field) — e.g. hide the
   *  lookup-only fields on a non-lookup column without hiding the whole Rules tab. */
  hiddenFields?: string[]
  /** Extra content rendered under a field, keyed by field name — e.g. "default from the data
   *  dictionary: LOOKUP" beside the rules field. The caller computes it from external context. */
  fieldNotes?: Record<string, ReactNode>
  /** Callbacks for the in-line query-action buttons rendered next to fields whose
   *  ``x_enum_ref`` is a query-bearing one (LOOKUP_QUERIES, CHART_QUERIES, …). All three
   *  are optional; when omitted the matching button doesn't render. The connector
   *  argument resolves from a sibling ``connector`` field; the parent may fall back to
   *  the surrounding scope.
   *
   *    onEditQuery     — opens the existing query for editing (Edit3 icon)
   *    onCloneQuery    — prompts for a new name, clones the existing query (Copy icon)
   *    onAddQuery      — prompts for a new name, creates a blank query (Plus icon)
   *    onGenerateQuery — scaffolds a query from the record's params + a picked table (Wand2 icon)
   */
  onEditQuery?: (connector: string | null | undefined, queryName: string) => void
  onCloneQuery?: (connector: string | null | undefined, queryName: string) => void
  onAddQuery?: (connector: string | null | undefined) => void
  onGenerateQuery?: (connector: string | null | undefined) => void
}) {
  const allDefs = { ...(schema.$defs ?? {}), ...(defs ?? {}) }
  // A discriminated union (Pydantic `Widget = ChartWidget | KpiWidget` → `oneOf` + `discriminator`)
  // has no top-level `properties`; the fields live on each branch. We do two things here so the
  // form renders sensibly:
  //   1. Resolve to the matching branch via the value's discriminator field. When unset (fresh
  //      item), fall back to the first branch so the form isn't blank.
  //   2. Replace the branch's `const`-typed discriminator field with an `enum` of all possible
  //      values. Pydantic emits `{const: "chart", default: "chart", type: "string"}` per branch;
  //      we substitute `{enum: ["chart", "kpi"], type: "string"}` so SchemaForm's existing
  //      string-enum branch renders it as a SearchSelect — picking a different value changes the
  //      `value.type`, the parent re-renders, and this resolver picks the new branch.
  const resolvedSchema: JsonSchema = isDiscriminatedUnion(schema)
    ? (() => {
        const branch = resolveUnionBranch(schema, value, allDefs)
        const actualBranch = branch !== schema ? branch : deref(schema.oneOf?.[0] ?? schema, allDefs)
        const propName = schema.discriminator?.propertyName
        if (!propName || !actualBranch.properties) return actualBranch
        const discValues: string[] = []
        for (const raw of schema.oneOf ?? []) {
          const b = deref(raw, allDefs)
          const c = b.properties?.[propName]?.enum?.[0] ?? b.properties?.[propName]?.default
          if (typeof c === 'string' && !discValues.includes(c)) discValues.push(c)
        }
        const newProps = {
          ...actualBranch.properties,
          [propName]: {
            ...actualBranch.properties[propName],
            enum: discValues,
            // Drop the `const` so SchemaForm's enum-handling branch wins (otherwise the form sees
            // a frozen string and renders a plain Input). Default keeps the auto-fill behaviour.
          } as JsonSchema,
        }
        return { ...actualBranch, properties: newProps }
      })()
    : schema
  const props = resolvedSchema.properties ?? {}
  const required = new Set(resolvedSchema.required ?? [])
  // For a discriminated union, switching the discriminator (e.g. widget `type` from chart → kpi)
  // leaves the *previous* branch's keys on the value — `chart` / `spec` would still ride along
  // after picking kpi. Pydantic's `extra="forbid"` then rejects the save. Strip orphans here:
  // when the discriminator field changes, keep only keys the new branch declares (plus the
  // discriminator itself). Shared fields (label / col_span / connector / query …) survive.
  const unionDiscr = isDiscriminatedUnion(schema) ? schema.discriminator?.propertyName : undefined
  const set = (key: string, v: unknown) => {
    if (unionDiscr && key === unionDiscr && typeof v === 'string' && v !== value[unionDiscr]) {
      const newBranch = resolveUnionBranch(schema, { [unionDiscr]: v }, allDefs)
      const newProps = newBranch.properties ?? {}
      const next: Record<string, unknown> = { [unionDiscr]: v }
      for (const [k, val] of Object.entries(value)) {
        if (k !== unionDiscr && k in newProps) next[k] = val
      }
      onChange(next)
      return
    }
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
  const groupNames = [...groups.keys()].filter((g) => !(hiddenGroups ?? []).includes(g))
  const showTabs = groupNames.length > 1
  const [tab, setTab] = useState(groupNames[0])
  // Remember the active tab PER model (resolved schema title) within this form instance — the
  // SchemaNavigator reuses one SchemaForm across drill-in/out, so when the model changes (drill into
  // a list item, then back) we RESTORE the tab the operator last had on that model instead of always
  // snapping to the first. Fixes "edit a column → open a rules_when → Back lands on the General tab".
  const tabByModel = useRef<Map<string, string>>(new Map())
  const selectTab = (g: string) => { setTab(g); tabByModel.current.set(resolvedSchema.title ?? '', g) }
  // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional: only re-key on model change
  useEffect(() => {
    const remembered = tabByModel.current.get(resolvedSchema.title ?? '')
    setTab(remembered && groupNames.includes(remembered) ? remembered : groupNames[0])
  }, [resolvedSchema.title])
  const activeProps = groups.get(tab) ?? groups.get(groupNames[0]) ?? []
  const enums = useContext(FrameworkEnumsContext)

  return (
    <div>
      {showTabs && (
        <TabsBar>
          {groupNames.map((g) => (
            <TabBtn key={g} type="button" $active={(groups.get(tab) ? tab : groupNames[0]) === g} onClick={() => selectTab(g)}>{g}</TabBtn>
          ))}
        </TabsBar>
      )}
      {activeProps.map(([key, raw]) => {
        if ((hiddenFields ?? []).includes(key)) return null
        const sub = effective(raw, allDefs)
        // ``x_visible_when`` — hide this field unless a sibling matches. The form's sibling
        // ``value`` is the parent ``value`` prop. Multiple allowed values via a list. Hidden
        // fields render nothing (returning ``null`` from the map preserves the array shape).
        const vw = sub.x_visible_when ?? raw.x_visible_when
        if (vw) {
          const sibling = value[vw.field]
          const allowed = Array.isArray(vw.value) ? vw.value : [vw.value]
          if (!allowed.some((v) => v === sibling)) return null
        }
        const isReq = required.has(key)
        const label = (sub.title ?? raw.title ?? key) + (isReq ? ' *' : '')
        const desc = sub.description ?? raw.description
        const cur = value[key]
        let control: ReactNode

        // A field that points at a framework enum (json_schema_extra={"x_enum_ref": "…"}) — render
        // with rich labels from the registry. `x_enum_ref_when` picks the ref from a sibling field's
        // current value (e.g. dictionary `rules_values` becomes an ENUM_IDS / LOOKUP_IDS dropdown
        // depending on `rules`). If the field is *also* a Literal we constrain the option list to
        // that Literal's enum (the strict shape wins); otherwise it's a combobox (allowCustom —
        // typing a value the registry doesn't know commits).
        const ref = effectiveEnumRef(sub, value, ancestors)
        const fe = enumFor(ref, enums)
        if (key === 'sql') {
          control = <SqlField value={cur} onChange={(v) => set(key, v)} />
        } else if (fe && sub.type === 'array') {
          // ``list[str]`` with ``x_enum_ref`` — multi-select. The previous single-select
          // branch (below) saved the value as a bare string, which fails Pydantic's
          // ``list[str]`` validation on the next reload (the operator picks one column from
          // a screen's SCREEN_COLUMNS dropdown, the save writes ``"cla_module"`` instead of
          // ``["cla_module"]``, the next /admin/reload rejects the file). Rendering each row
          // as its own SearchSelect via StringListEditor keeps the shape correct + lets
          // operators pick more than one (nested grouping: pick app then module → two-level
          // collapse). Items-level enum constraint (sub.items.enum) wins over the field's
          // outer Literal, same precedence as the single-select branch below.
          const itemsBranch = effective(sub.items ?? {}, allDefs)
          const literalSet = Array.isArray(itemsBranch.enum)
            ? new Set(itemsBranch.enum.map((e) => String(e)))
            : null
          const opts = enumOptions(fe, literalSet)
          const arr = Array.isArray(cur) ? (cur as unknown[]).map((x) => (x == null ? '' : String(x))) : []
          control = (
            <StringListEditor
              value={arr}
              // Case-normalise each entry on save (same x_case hint as scalar string
              // fields). Operator can type lowercase column names; the saved list stays
              // in the configured convention.
              onChange={(v) => set(key, v.length ? v.map((x) => applyCase(x, sub)) : undefined)}
              options={opts}
              allowCustom={!literalSet}
            />
          )
        } else if (fe) {
          const literalSet = Array.isArray(sub.enum) ? new Set(sub.enum.map((e) => String(e))) : null
          const opts = enumOptions(fe, literalSet)
          const picker = (
            <SearchSelect value={cur == null ? '' : String(cur)}
              onChange={(v) => set(key, v === '' ? undefined : applyCase(v, sub))}
              options={opts} anyLabel={isReq ? undefined : '(default)'} placeholder="(default)" allowCustom={!literalSet} />
          )
          // Append the Edit / Clone / Add query trio when the field is one of the
          // query-bearing enum refs AND at least one of the handlers is wired. The
          // connector resolves from a sibling ``connector`` field on the same object
          // (the convention for LookupDef / SequenceDef / ChartConfig — when blank
          // the caller's handler is responsible for falling back to the surrounding
          // scope). Edit / Clone require both connector + queryName; Add only needs
          // connector. Each button hides itself when its prerequisites aren't met.
          if (ref && QUERY_ENUM_REFS.has(ref) && (onEditQuery || onCloneQuery || onAddQuery || onGenerateQuery)) {
            const sibConn = typeof value.connector === 'string' ? value.connector : null
            const hasQuery = cur != null && String(cur) !== ''
            control = (
              <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                <div style={{ flex: 1, minWidth: 0 }}>{picker}</div>
                {onEditQuery && hasQuery && (
                  <InlineActionBtn type="button" title="Edit query" aria-label="Edit query"
                    onClick={() => onEditQuery(sibConn, String(cur))} disabled={!sibConn}>
                    <Edit3 size={13} />
                  </InlineActionBtn>
                )}
                {onCloneQuery && hasQuery && (
                  <InlineActionBtn type="button" title="Clone query" aria-label="Clone query"
                    onClick={() => onCloneQuery(sibConn, String(cur))} disabled={!sibConn}>
                    <Copy size={13} />
                  </InlineActionBtn>
                )}
                {onAddQuery && (
                  <InlineActionBtn type="button" title="Add query" aria-label="Add query"
                    onClick={() => onAddQuery(sibConn)} disabled={!sibConn}>
                    <Plus size={13} />
                  </InlineActionBtn>
                )}
                {onGenerateQuery && (
                  <InlineActionBtn type="button" title="Generate query from table" aria-label="Generate query"
                    onClick={() => onGenerateQuery(sibConn)} disabled={!sibConn}>
                    <Wand2 size={13} />
                  </InlineActionBtn>
                )}
              </div>
            )
          } else {
            control = picker
          }
        } else if (Array.isArray(sub.enum)) {
          control = (
            <SearchSelect value={cur == null ? '' : String(cur)} onChange={(v) => set(key, v === '' ? undefined : v)}
              options={sub.enum.map((e) => ({ value: String(e), label: String(e) }))} anyLabel={isReq ? undefined : '(default)'} placeholder="(default)" />
          )
        } else if (sub.type === 'boolean') {
          const checked = cur === undefined ? Boolean(sub.default) : Boolean(cur)
          control = <Checkbox checked={checked} onChange={(v) => set(key, v)} label={checked ? 'enabled' : 'disabled'} />
        } else if (sub.type === 'array') {
          const items = effective(sub.items ?? {}, allDefs)
          // `cur` may be a single object for the `T | list[T] | None` shape (a hand-written single rule)
          const arr = Array.isArray(cur) ? (cur as unknown[]) : cur && typeof cur === 'object' ? [cur] : []
          // Discriminated unions of object models (Pydantic's `Widget = ChartWidget | KpiWidget`,
          // emitted as `oneOf` + `discriminator`) take the object-list path too — itemSummary
          // resolves each item's branch via its discriminator value (`it.type`) so the row labels
          // read sensibly + the per-item drill-in renders the matching branch's fields.
          if (isObjectModel(items) || isDiscriminatedUnion(items)) {
            const objs = arr as Record<string, unknown>[]
            const setArr = (v: Record<string, unknown>[]) => set(key, v.length ? v : undefined)
            const labelPlain = label.replace(' *', '')
            control = onNavigate
              ? <ObjectNavList itemSchema={items} defs={allDefs} value={objs} onChange={setArr}
                  onNavigate={(index, summary) => onNavigate({ kind: 'item', key, index, label: `${labelPlain}: ${summary}` })} />
              : <ObjectListEditor itemSchema={items} defs={allDefs} value={objs} onChange={setArr} ancestors={[value, ...(ancestors ?? [])]} />
          } else {
            control = <StringListEditor value={arr.map((x) => (x == null ? '' : String(x)))}
              onChange={(v) => set(key, v.length ? v.map((x) => applyCase(x, sub)) : undefined)} />
          }
        } else if (isStringMap(sub)) {
          const map = (cur && typeof cur === 'object' && !Array.isArray(cur) ? cur : {}) as Record<string, unknown>
          const keyEnum = enumFor(sub.x_key_enum_ref ?? null, enums)
          control = <StringMapEditor
            value={Object.fromEntries(Object.entries(map).map(([k, v]) => [k, v == null ? '' : String(v)]))}
            onChange={(v) => set(key, Object.keys(v).length ? v : undefined)}
            keyEnum={keyEnum}
          />
        } else if (isObjectModel(sub)) {
          const subValue = (cur && typeof cur === 'object' && !Array.isArray(cur) ? cur : {}) as Record<string, unknown>
          control = onNavigate ? (
            <NavListRow type="button" onClick={() => onNavigate({ kind: 'prop', key, label: label.replace(' *', '') })}>
              <span className="lbl">edit…</span><ChevronRight size={13} />
            </NavListRow>
          ) : (
            <ItemBox><ItemBody style={{ borderTop: 'none' }}>
              <SchemaForm schema={sub} defs={allDefs} value={subValue} onChange={(v) => set(key, Object.keys(v).length ? v : undefined)} ancestors={[value, ...(ancestors ?? [])]} />
            </ItemBody></ItemBox>
          )
        } else if (sub.type === 'integer' || sub.type === 'number') {
          control = <Input type="number" value={cur == null ? '' : String(cur)} placeholder={sub.default != null ? `default: ${sub.default}` : isReq ? 'required' : ''}
            onChange={(e) => { const txt = e.target.value; set(key, txt === '' ? undefined : sub.type === 'integer' ? Math.trunc(Number(txt)) : Number(txt)) }} />
        } else if (sub.type === 'string' || sub.type === undefined) {
          // ``x_placeholder`` (from Pydantic ``Field(json_schema_extra={"x_placeholder": "…"})``)
          // wins outright — it's the field-specific hint the operator types into. Falls back
          // to ``default: <value>`` or ``required`` so existing fields without an explicit
          // x_placeholder keep their current behaviour.
          const placeholder = sub.x_placeholder ?? (sub.default ? `default: ${sub.default}` : isReq ? 'required' : '')
          // `format: "password"` (set via Field(json_schema_extra={"format": "password"})) → masked
          // input with a reveal toggle, so ENC: ciphertext / auth tokens / etc. don't sit in plain
          // sight in the builder. The stored value is still the raw string — purely a visual mask.
          control = sub.format === 'password' ? (
            <PasswordInput value={cur == null ? '' : String(cur)} placeholder={placeholder}
              onChange={(e) => set(key, e.target.value === '' ? undefined : e.target.value)} />
          ) : (
            <Input type="text" value={cur == null ? '' : String(cur)} placeholder={placeholder}
              onChange={(e) => {
                const raw = e.target.value
                set(key, raw === '' ? undefined : applyCase(raw, sub))
              }} />
          )
        } else {
          control = <Complex>complex value — edit it in the raw editor</Complex>
        }
        return (
          <div key={key} style={{ marginBottom: 14 }}>
            <Field label={label}>{control}</Field>
            {fieldNotes?.[key]}
            {desc && <Hint>{desc}</Hint>}
          </div>
        )
      })}
    </div>
  )
}
