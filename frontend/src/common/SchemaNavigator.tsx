// Master-detail navigator over a config object — shows ONE SchemaForm at a time (the level you're
// focused on) plus a breadcrumb of the path to it (e.g. `[connectors.nomasx1] / users_get / USR_ID`).
// Clicking a `list[Model]` row (or a nested-object "edit…") in the current form drills in (pushes a
// path segment); clicking a breadcrumb crumb pops back. The path is stored as segments and the
// current (schema, value, onChange) is *derived* from the root each render — so edits keep the path
// valid, and a re-fetch of the same root doesn't reset where you are. Reset to the top when the root
// `label` changes (= a different thing is being edited).
import { useEffect, useState } from 'react'
import styled from '@emotion/styled'
import { ChevronRight, ArrowLeft, Layers } from 'lucide-react'
import { SchemaForm, type JsonSchema, type NavSeg } from './SchemaForm'
import { colors, fontSize, fonts, radius } from '../theme'

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
}

const Crumbs = styled.nav`
  display: flex; align-items: center; flex-wrap: wrap; gap: 3px; margin-bottom: 14px;
  padding: 7px 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  & > .lead { color: ${colors.text.muted}; margin-right: 4px; display: inline-flex; }
`
const Crumb = styled.button<{ $current?: boolean }>`
  border: none; background: none; padding: 3px 6px; border-radius: ${radius.sm};
  font-family: ${fonts.mono}; font-size: ${fontSize.sm};
  font-weight: ${({ $current }) => ($current ? 600 : 400)};
  color: ${({ $current }) => ($current ? colors.text.primary : colors.text.muted)};
  cursor: ${({ $current }) => ($current ? 'default' : 'pointer')};
  ${({ $current }) => (!$current ? `&:hover { color: ${colors.blue.main}; background: var(--hover-subtle); }` : '')}
`
const Sep = styled(ChevronRight)`flex-shrink: 0; color: ${colors.text.muted}; opacity: 0.4; margin: 0;`
const BackBtn = styled.button`
  margin-left: auto; display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 10px; border-radius: ${radius.sm};
  border: 1px solid ${colors.border}; background: ${colors.bg.card}; color: ${colors.text.secondary};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; cursor: pointer;
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; }
`

export function SchemaNavigator({ root }: { root: NavRoot }) {
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

  return (
    <div>
      <Crumbs aria-label="path">
        <span className="lead" title="you're editing one item of the config tree"><Layers size={13} /></span>
        {levels.map((lv, i) => (
          <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>
            {i > 0 && <Sep size={13} />}
            <Crumb type="button" $current={i === levels.length - 1} onClick={() => i < levels.length - 1 && go(i)}>{lv.label}</Crumb>
          </span>
        ))}
        {path.length > 0 && <BackBtn type="button" onClick={() => go(path.length - 1)}><ArrowLeft size={13} /> back</BackBtn>}
      </Crumbs>
      <SchemaForm schema={cur.schema} defs={defs} value={cur.value} onChange={cur.onChange} onNavigate={(seg) => setPath((p) => [...p, seg])} />
    </div>
  )
}
