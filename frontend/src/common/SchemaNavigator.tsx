// Master-detail navigator over a config object — shows ONE SchemaForm at a time (the level you're
// focused on) plus a breadcrumb of the path to it (e.g. `[connectors.nomasx1] / users_get / USR_ID`).
// Clicking a `list[Model]` row (or a nested-object "edit…") in the current form drills in (pushes a
// path segment); clicking a breadcrumb crumb pops back. The path is stored as segments and the
// current (schema, value, onChange) is *derived* from the root each render — so edits keep the path
// valid, and a re-fetch of the same root doesn't reset where you are. Reset to the top when the root
// `label` changes (= a different thing is being edited).
import { useEffect, useState, type ReactNode } from 'react'
import { SchemaForm, type JsonSchema, type NavSeg } from './SchemaForm'
import { SubNav } from './SubNav'

// minimal $ref / anyOf peeling, mirroring SchemaForm's `effective` (kept local to avoid exporting it)
function effective(s: JsonSchema | undefined, defs: Record<string, JsonSchema>): JsonSchema {
  let cur = s ?? {}
  for (let i = 0; cur.$ref && i < 5; i++) {
    cur = { ...defs[cur.$ref.replace(/^#\/\$defs\//, '')], ...cur, $ref: undefined } as JsonSchema
  }
  if (cur.anyOf) {
    const branches = cur.anyOf.map((b) => effective(b, defs))
    const arr = branches.find((b) => b.type === 'array'); if (arr) return arr
    const nn = branches.filter((b) => b.type !== 'null'); if (nn.length === 1) return nn[0]
  }
  return cur
}

export interface NavRoot {
  label: string
  schema: JsonSchema   // carries its own $defs
  value: Record<string, unknown>
  onChange: (v: Record<string, unknown>) => void
  /** Optional per-level context for the SchemaForm — given the level's value + schema, return
   *  tab groups to hide and/or extra notes to show under fields. Lets the caller (which has
   *  external data like the dictionary) drive conditional tabs / hints without SchemaForm knowing
   *  about it. Called for whichever level is currently shown. */
  deriveContext?: (value: Record<string, unknown>, schema: JsonSchema) => {
    hiddenGroups?: string[]
    hiddenFields?: string[]
    fieldNotes?: Record<string, ReactNode>
  }
}

export function SchemaNavigator({ root, onEditQuery, onCloneQuery, onAddQuery }: {
  root: NavRoot
  /** Forwarded to the underlying SchemaForm — fire when the operator clicks the in-line
   *  Edit / Clone / Add buttons next to a query-bearing dropdown. Caller mounts the
   *  EditQueryModal (with appropriate seed for clone / add) in response. */
  onEditQuery?: (connector: string | null | undefined, queryName: string) => void
  onCloneQuery?: (connector: string | null | undefined, queryName: string) => void
  onAddQuery?: (connector: string | null | undefined) => void
}) {
  const [path, setPath] = useState<NavSeg[]>([])
  useEffect(() => { setPath([]) }, [root.label])   // a different thing selected → back to the top

  const defs = root.schema.$defs ?? {}
  type Level = { label: string; schema: JsonSchema; value: Record<string, unknown>; onChange: (v: Record<string, unknown>) => void }
  const levels: Level[] = [{ label: root.label, schema: root.schema, value: root.value, onChange: root.onChange }]
  let truncate: number | null = null
  for (let i = 0; i < path.length; i++) {
    const seg = path[i]
    const parent = levels[levels.length - 1]
    const propSchema = effective(parent.schema.properties?.[seg.key], defs)
    if (seg.kind === 'prop') {
      const v = parent.value[seg.key]
      const obj = (v && typeof v === 'object' && !Array.isArray(v) ? v : {}) as Record<string, unknown>
      levels.push({
        label: seg.label, schema: propSchema, value: obj,
        onChange: (nv) => parent.onChange({ ...parent.value, [seg.key]: Object.keys(nv).length ? nv : undefined }),
      })
    } else {
      const itemSchema = effective(propSchema.items, defs)
      const raw = parent.value[seg.key]
      const arr = (Array.isArray(raw) ? raw : raw && typeof raw === 'object' ? [raw] : []) as Record<string, unknown>[]
      const idx = seg.index ?? 0
      if (idx >= arr.length) { truncate = i; break }   // the item was removed under us → pop to here
      const it = (arr[idx] && typeof arr[idx] === 'object' ? arr[idx] : {}) as Record<string, unknown>
      const summary = typeof it.name === 'string' && it.name ? it.name : seg.label
      levels.push({
        label: summary, schema: itemSchema, value: it,
        onChange: (nv) => parent.onChange({ ...parent.value, [seg.key]: arr.map((x, j) => (j === idx ? nv : x)) }),
      })
    }
  }
  // (defer the actual setPath until after render — see the effect below)
  const needTruncate = truncate
  useEffect(() => { if (needTruncate != null) setPath((p) => p.slice(0, needTruncate)) }, [needTruncate])

  const cur = levels[levels.length - 1]
  const go = (depth: number) => setPath((p) => p.slice(0, depth))   // depth 0 = root

  // Back lands on the parent level; the breadcrumb gives the full context. Both live in the pinned,
  // left-aligned SubNav so they never scroll out of view inside a long form.
  const parentLabel = levels.length > 1 ? levels[levels.length - 2].label : undefined
  const crumbs = levels.map((lv, i) => ({
    label: lv.label,
    onClick: i < levels.length - 1 ? () => go(i) : undefined,
  }))

  return (
    <div>
      <SubNav
        onBack={path.length > 0 ? () => go(path.length - 1) : undefined}
        backLabel={parentLabel}
        crumbs={crumbs}
      />
      <SchemaForm schema={cur.schema} defs={defs} value={cur.value} onChange={cur.onChange} onNavigate={(seg) => setPath((p) => [...p, seg])} onEditQuery={onEditQuery} onCloneQuery={onCloneQuery} onAddQuery={onAddQuery}
        {...(root.deriveContext?.(cur.value, cur.schema) ?? {})} />
    </div>
  )
}
