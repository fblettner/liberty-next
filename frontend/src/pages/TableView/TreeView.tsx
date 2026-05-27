// Phase 3(c) — parent / child hierarchy view for the TableView.
//
// Renders the result set as a recursive collapsible tree when the screen carries a
// ``treeview`` config (parent / child / label column names). Walks the rows once at
// mount, indexes them by ``child`` value, then descends from roots (rows whose
// ``parent`` value is null / empty / not present in the index). Each node is one row;
// children are the rows whose ``parent`` equals this row's ``child`` value.
//
// Tooling kept lean on purpose:
//   * no virtualisation — 4 k menu rows render fine; revisit if a real customer
//     ships a 100 k-node tree
//   * read-only — clicking a node selects + scrolls; CRUD stays on the Table view
//   * search filter — substring match on the label, with auto-expand of the matching
//     ancestor path (so a deep hit doesn't stay hidden behind its parents)
//
// Same prop shape as ChartView so the host-page conditional in TableView stays
// uniform: ``<TreeView result={...} screen={...} />`` — the screen is needed for
// the treeview config; result carries the rows + columns.
import { useEffect, useMemo, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronRight, ChevronDown, Folder, FileText, Search } from 'lucide-react'
import { Input } from '../../common'
import type { QueryResult } from '../../types/connectors'
import type { ScreenDetail, ScreenTreeview } from '../../types/screens'
import { colors, fontSize, fonts, radius } from '../../theme'

const Frame = styled.div`
  display: flex; flex-direction: column; flex: 1; min-height: 0; gap: 10px;
`
const Controls = styled.div`
  display: flex; gap: 8px; align-items: center; flex-wrap: wrap;
  padding: 4px 2px;
`
const SearchWrap = styled.div`
  display: flex; align-items: center; gap: 6px;
  height: 30px; padding: 0 8px;
  border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  background: ${colors.bg.input}; min-width: 220px;
  & input { flex: 1; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; }
  & input::placeholder { color: ${colors.text.muted}; }
`
const Meta = styled.span`color: ${colors.text.muted}; font-size: ${fontSize.sm};`
const Pill = styled.button`
  background: transparent; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  height: 26px; padding: 0 10px; cursor: pointer;
  font-size: ${fontSize.sm}; color: ${colors.text.secondary}; font-family: ${fonts.sans};
  &:hover { color: ${colors.text.primary}; border-color: ${colors.text.muted}; }
`
const TreeScroll = styled.div`
  flex: 1; min-height: 0; overflow: auto;
  border: 1px solid ${colors.border}; border-radius: ${radius.md};
  background: ${colors.bg.input};
  padding: 6px 4px;
`
const Empty = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.sm}; padding: 28px 4px; text-align: center;`

const NodeRow = styled.div<{ $depth: number; $selected?: boolean }>`
  display: flex; align-items: center; gap: 6px;
  padding: 4px 6px 4px ${({ $depth }) => 6 + $depth * 18}px;
  font-size: ${fontSize.sm}; font-family: ${fonts.mono};
  color: ${colors.text.primary};
  cursor: pointer;
  border-radius: 4px;
  background: ${({ $selected }) => ($selected ? colors.blue.bg : 'transparent')};
  &:hover { background: ${({ $selected }) => ($selected ? colors.blue.bg : colors.bg.card)}; }
`
const Caret = styled.button`
  background: transparent; border: none; cursor: pointer; padding: 0;
  width: 18px; height: 18px; flex-shrink: 0;
  color: ${colors.text.muted}; display: inline-flex; align-items: center; justify-content: center;
  &:hover { color: ${colors.text.primary}; }
  &:disabled { visibility: hidden; cursor: default; }
`
const NodeLabel = styled.span`
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
`
const NodeIcon = styled.span`
  color: ${colors.text.muted}; display: inline-flex; align-items: center; flex-shrink: 0;
`
const ChildCount = styled.span`
  color: ${colors.text.muted}; font-size: ${fontSize.sm};
  margin-left: 4px;
`

interface TreeNode {
  /** The unique key for this node — the row's ``child`` column value. */
  id: string
  /** Display text — the row's ``label`` column value, with fallback to id when empty. */
  label: string
  /** The full source row — caller can show extra detail in a side panel later. */
  row: Record<string, unknown>
  /** Child nodes — sorted by label for stable rendering. */
  children: TreeNode[]
}

export interface TreeViewProps {
  result: QueryResult
  /** ScreenDetail carries the ``treeview`` config (parent / child / label column names). */
  screen: ScreenDetail
}

export function TreeView({ result, screen }: TreeViewProps) {
  const { t } = useTranslation()

  // The treeview config MUST be present — the host page only mounts us when it is.
  // Defensive: bail with a hint if it isn't (no crash, just useful diagnostics).
  const cfg = screen.treeview ?? null
  if (!cfg) {
    return <Empty>{t('table.tree.noConfig', 'No treeview config on this screen')}</Empty>
  }

  return <TreeViewInner result={result} cfg={cfg} />
}

// Internal component runs the actual tree-build + render once we know cfg is present.
// Split out so the conditional-render guard above is the only code path that touches
// the optional config (TypeScript narrowing also works here, but the explicit split
// makes the "config present" assumption local + obvious).
function TreeViewInner({ result, cfg }: { result: QueryResult; cfg: ScreenTreeview }) {
  const { t } = useTranslation()
  const [query, setQuery] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [selectedId, setSelectedId] = useState<string | null>(null)

  // Case-insensitive column key resolution — same convention as ResultTable's cur().
  // Column hints in screens.toml are often UPPERCASE; row keys from Postgres come
  // back lowercase. Resolve the configured column NAME against the actual row keys
  // once, then read by the resolved key. Falls back to the configured name if nothing
  // matches (the table will simply show empty labels — fail-loudly enough that the
  // operator notices, doesn't crash).
  //
  // ``label`` accepts two forms: a bare column name (treated as ``{column}``) OR a
  // template like ``{menu_label} ({menu_object})`` — placeholders substitute with
  // case-insensitive column lookup; literal text between placeholders renders
  // verbatim. ``buildLabel`` compiles the template once at mount time so each
  // per-row call is just N map lookups and a join.
  const resolved = useMemo(() => {
    const rows = (result.rows ?? []) as Array<Record<string, unknown>>
    const sampleKeys = rows.length > 0 ? Object.keys(rows[0]) : []
    const byLower = new Map<string, string>()
    for (const k of sampleKeys) byLower.set(k.toLowerCase(), k)
    const resolve = (name: string) => byLower.get(name.toLowerCase()) ?? name
    return {
      parent: resolve(cfg.parent),
      child:  resolve(cfg.child),
      buildLabel: compileLabelTemplate(cfg.label, resolve),
      orderBy: (cfg.order_by ?? []).map(resolve),
    }
  }, [result.rows, cfg.parent, cfg.child, cfg.label, cfg.order_by])

  // Walk rows ONCE to build the tree. We index by child id, then descend from roots
  // (rows whose parent is empty / null / not present in the index). Duplicate child
  // ids collapse — the first row wins; subsequent ones become noise (logged once
  // per duplicate at DEBUG would be useful, deferred until an operator hits it).
  const { roots, totalNodes, orphans } = useMemo(() => {
    const rows = (result.rows ?? []) as Array<Record<string, unknown>>
    type Pending = { node: TreeNode; parentValue: string }
    const byId = new Map<string, TreeNode>()
    const pending: Pending[] = []
    for (const row of rows) {
      const idValue = stringify(row[resolved.child])
      if (!idValue) continue   // skip rows with no child id — they can't be linked
      if (byId.has(idValue)) continue   // first row wins on dup
      const labelValue = resolved.buildLabel(row) || idValue
      const node: TreeNode = { id: idValue, label: labelValue, row, children: [] }
      byId.set(idValue, node)
      pending.push({ node, parentValue: stringify(row[resolved.parent]) })
    }
    // Second pass: link each node to its parent if present; else collect as root.
    const tops: TreeNode[] = []
    let orphanCount = 0
    for (const { node, parentValue } of pending) {
      if (parentValue && byId.has(parentValue) && parentValue !== node.id) {
        byId.get(parentValue)!.children.push(node)
      } else {
        tops.push(node)
        // "orphan" = parent value present but unresolvable. Tracked so the operator
        // can see "12 roots (5 orphans)" — distinguishes a tree with real roots from
        // a stale / partial pull.
        if (parentValue && parentValue !== node.id) orphanCount += 1
      }
    }
    // Sibling ordering: when ``order_by`` is configured (e.g. MENU_SEQ_UKID on
    // security_menus, the JDE authoring sequence), sort by those columns in
    // priority order with per-column numeric-vs-string auto-detect. Without
    // ``order_by``, fall back to alphabetic on the label — deterministic
    // rendering either way.
    const compare = buildComparator(resolved.orderBy)
    const sort = (n: TreeNode) => { n.children.sort(compare); n.children.forEach(sort) }
    tops.sort(compare)
    tops.forEach(sort)
    return { roots: tops, totalNodes: byId.size, orphans: orphanCount }
  }, [result.rows, resolved.parent, resolved.child, resolved.buildLabel, resolved.orderBy])

  // Search: substring match on the label (case-insensitive). When a search is active,
  // we compute the set of node ids that should be VISIBLE = the matches + every
  // ancestor on their path to a root (so the operator sees the matched node in
  // context). Empty query = no filter.
  const visibleIds = useMemo<Set<string> | null>(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return null
    const matches = new Set<string>()
    const walk = (node: TreeNode, ancestors: string[]): boolean => {
      const here = node.label.toLowerCase().includes(needle)
      const childHits = node.children.map((c) => walk(c, [...ancestors, node.id]))
      const anyChildHit = childHits.some(Boolean)
      if (here || anyChildHit) {
        matches.add(node.id)
        for (const a of ancestors) matches.add(a)
        return true
      }
      return false
    }
    roots.forEach((r) => walk(r, []))
    return matches
  }, [query, roots])

  // When the user types in the search box, auto-expand every visible node so the
  // matches surface. Reset to whatever the operator had open when they clear search
  // (don't merge — overriding their manual choices would be surprising).
  const savedExpanded = useState<Record<string, boolean> | null>(null)
  useEffect(() => {
    if (visibleIds) {
      // Save the operator's manual state on the first search-active render so we
      // can restore it when the query clears.
      if (savedExpanded[0] === null) savedExpanded[1](expanded)
      const all: Record<string, boolean> = {}
      visibleIds.forEach((id) => { all[id] = true })
      setExpanded(all)
    } else if (savedExpanded[0] !== null) {
      setExpanded(savedExpanded[0])
      savedExpanded[1](null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleIds])

  const toggle = (id: string) => setExpanded((p) => ({ ...p, [id]: !p[id] }))

  const expandAll = () => {
    const all: Record<string, boolean> = {}
    const walk = (n: TreeNode) => { all[n.id] = true; n.children.forEach(walk) }
    roots.forEach(walk)
    setExpanded(all)
  }
  const collapseAll = () => setExpanded({})

  if (roots.length === 0) {
    return (
      <Frame>
        <Empty>{t('table.tree.empty', 'No rows returned — nothing to render as a tree.')}</Empty>
      </Frame>
    )
  }

  return (
    <Frame>
      <Controls>
        <SearchWrap>
          <Search size={13} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t('table.tree.searchPlaceholder', 'Filter the tree…')}
            aria-label={t('table.tree.searchAriaLabel', 'Filter tree nodes by label')}
          />
        </SearchWrap>
        <Pill type="button" onClick={expandAll}>{t('table.tree.expandAll', 'Expand all')}</Pill>
        <Pill type="button" onClick={collapseAll}>{t('table.tree.collapseAll', 'Collapse all')}</Pill>
        <Meta>
          {t('table.tree.summary', '{{nodes}} nodes · {{roots}} roots{{orphanSuffix}}', {
            nodes: totalNodes,
            roots: roots.length,
            orphanSuffix: orphans > 0
              ? ` · ${t('table.tree.orphans', '{{count}} orphan(s)', { count: orphans })}`
              : '',
          })}
        </Meta>
      </Controls>
      <TreeScroll>
        {roots.map((node) => (
          <Branch
            key={node.id}
            node={node}
            depth={0}
            expanded={expanded}
            visibleIds={visibleIds}
            selectedId={selectedId}
            onToggle={toggle}
            onSelect={setSelectedId}
          />
        ))}
      </TreeScroll>
    </Frame>
  )
}

// Recursive renderer — each TreeNode is one NodeRow + (when expanded) its children
// underneath. Extracted to keep the inner component's body readable.
function Branch({
  node, depth, expanded, visibleIds, selectedId, onToggle, onSelect,
}: {
  node: TreeNode
  depth: number
  expanded: Record<string, boolean>
  visibleIds: Set<string> | null
  selectedId: string | null
  onToggle: (id: string) => void
  onSelect: (id: string) => void
}) {
  // Search-active filter: hide nodes not on a match path.
  if (visibleIds && !visibleIds.has(node.id)) return null
  const hasChildren = node.children.length > 0
  const isOpen = !!expanded[node.id]
  return (
    <>
      <NodeRow
        $depth={depth}
        $selected={selectedId === node.id}
        onClick={() => onSelect(node.id)}
      >
        <Caret
          type="button"
          disabled={!hasChildren}
          onClick={(e) => { e.stopPropagation(); onToggle(node.id) }}
          aria-label={hasChildren ? (isOpen ? 'Collapse' : 'Expand') : undefined}
        >
          {hasChildren ? (isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />) : null}
        </Caret>
        <NodeIcon>
          {hasChildren ? <Folder size={13} /> : <FileText size={13} />}
        </NodeIcon>
        <NodeLabel>{node.label}</NodeLabel>
        {hasChildren && <ChildCount>({node.children.length})</ChildCount>}
      </NodeRow>
      {isOpen && hasChildren && node.children.map((c) => (
        <Branch
          key={c.id}
          node={c}
          depth={depth + 1}
          expanded={expanded}
          visibleIds={visibleIds}
          selectedId={selectedId}
          onToggle={onToggle}
          onSelect={onSelect}
        />
      ))}
    </>
  )
}

// Compile a treeview label spec into a row-to-string function. Two forms:
//
//   * A bare column name (``"menu_id"``) — treated as the template ``"{menu_id}"``.
//     Backward-compatible with the original schema where ``label`` was just a column.
//   * A template with ``{column}`` placeholders (``"{menu_label} ({menu_object})"``) —
//     each placeholder substitutes the row's value for that column (case-insensitive
//     resolution via the ``resolveCol`` callback); literal text between placeholders
//     renders verbatim. Empty values render as empty strings; unknown columns render
//     as the literal ``{unknown_col}`` so the operator notices the typo.
//
// Compiled once at mount, so each per-row call is just N substitutions.
function compileLabelTemplate(
  spec: string,
  resolveCol: (name: string) => string,
): (row: Record<string, unknown>) => string {
  // Detect bare-column form (no '{') and rewrite as a single-placeholder template.
  const template = spec.includes('{') ? spec : `{${spec}}`
  // Pre-split into a stable list of segments so substitution is just an Array.join.
  // Each segment is either { kind: 'lit', text } or { kind: 'col', name, original }.
  type Seg = { kind: 'lit'; text: string } | { kind: 'col'; name: string; original: string }
  const segs: Seg[] = []
  let i = 0
  while (i < template.length) {
    const open = template.indexOf('{', i)
    if (open === -1) { segs.push({ kind: 'lit', text: template.slice(i) }); break }
    if (open > i) segs.push({ kind: 'lit', text: template.slice(i, open) })
    const close = template.indexOf('}', open + 1)
    if (close === -1) { segs.push({ kind: 'lit', text: template.slice(open) }); break }
    const colName = template.slice(open + 1, close)
    segs.push({ kind: 'col', name: resolveCol(colName), original: `{${colName}}` })
    i = close + 1
  }
  return (row) => {
    const parts: string[] = []
    for (const s of segs) {
      if (s.kind === 'lit') { parts.push(s.text); continue }
      // The resolver returns the ORIGINAL name when no column matched — detect that
      // and render the literal placeholder so the operator sees the typo on screen
      // instead of an empty cell.
      const v = row[s.name]
      if (v === null || v === undefined) {
        // A configured column with a null value renders empty (not "{col}" — that's
        // for typos, not for a real-but-empty cell). Telling them apart: if the
        // resolved name exists as a row key, it's a real column.
        parts.push(s.name in row ? '' : s.original)
        continue
      }
      parts.push(String(v))
    }
    return parts.join('').trim()
  }
}

// Utility: normalise a cell value to a string for id / label / parent comparisons.
// Numbers come through as numbers from JSON; null / undefined become empty. The
// tree's id/parent matching is string-based (a number id and a string id with the
// same digits should match — JDE menu ids are mixed string/integer in practice).
function stringify(v: unknown): string {
  if (v === null || v === undefined) return ''
  return String(v)
}

// Build a TreeNode comparator from the resolved ``order_by`` column list. Each
// column is compared in priority order; first non-zero result wins. Per-column
// numeric-vs-string detection is per pair (both values numeric → numeric compare;
// else string compare via localeCompare for sensible Unicode ordering). null /
// undefined values sort last consistently. Empty ``orderBy`` falls back to
// alphabetic on the node label — preserves the original behaviour when an
// operator hasn't configured a sort.
function buildComparator(orderBy: string[]): (a: TreeNode, b: TreeNode) => number {
  if (orderBy.length === 0) {
    return (a, b) => a.label.localeCompare(b.label)
  }
  return (a, b) => {
    for (const col of orderBy) {
      const va = a.row[col]
      const vb = b.row[col]
      // null / undefined sort last (so configured rows beat unconfigured ones).
      const aMissing = va === null || va === undefined
      const bMissing = vb === null || vb === undefined
      if (aMissing && bMissing) continue
      if (aMissing) return 1
      if (bMissing) return -1
      // Numeric path when both sides parse cleanly to finite numbers — handles
      // both raw numbers from JSON and numeric strings ("00" vs "10" — string
      // compare would put "10" before "2"; numeric is what JDE intends).
      const na = typeof va === 'number' ? va : Number(va)
      const nb = typeof vb === 'number' ? vb : Number(vb)
      if (Number.isFinite(na) && Number.isFinite(nb)) {
        if (na !== nb) return na - nb
        continue
      }
      // String fallback — localeCompare handles Unicode + locale-aware folding.
      const cmp = String(va).localeCompare(String(vb))
      if (cmp !== 0) return cmp
    }
    return 0
  }
}

// Unused — re-exported so the lazy import in TableView reads cleanly.
export default TreeView
// Silence eslint about Input — kept in imports so a future search-as-Input swap is
// a one-line change; unused for now since we render a raw <input> inline.
void Input
