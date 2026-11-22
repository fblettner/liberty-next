// Path-based selection helpers for the Visual Designer's action editor (Theme A polish).
// A v2 chain workflow nests arbitrarily deep — ChainAction.steps may contain IfActions whose
// then_steps may contain LoopActions whose steps may contain another ChainAction. Trying to
// edit all that in one inline-accordion tree quickly becomes a 1500-px wall of folded panels.
//
// The path model fixes that: each clickable action gets a unique :type:`ActionPath`, the
// Inspector shows ONE action at a time, and a breadcrumb at the top lets the operator pop back
// up the stack. Same data, much less to scroll past.
//
// ──  Path encoding ──────────────────────────────────────────────────────────────────────────
// An :type:`ActionPath` is a list of segments. The first segment always navigates inside the
// root array (typically ``tab.actions``); subsequent segments name the parent's nested-step
// field (``steps`` / ``then_steps`` / ``else_steps``) and the index within it.
//
//   ``[{field: null, i: 0}]``                    → root[0]                       (top-level)
//   ``[{field: null, i: 0}, {field: 'steps', i: 2}]``           → root[0].steps[2]           (chain's 3rd step)
//   ``[{field: null, i: 0}, {field: 'then_steps', i: 0}]``      → root[0].then_steps[0]      (if's 1st then-step)
//   ``[{field: null, i: 0}, {field: 'else_steps', i: 1}]``      → root[0].else_steps[1]      (if's 2nd else-step)
//   ``[{field: null, i: 0}, {field: 'steps', i: 0}, {field: 'then_steps', i: 1}]`` → root[0].steps[0].then_steps[1]
//
// The ``field`` is ``null`` only at the root (the first segment); deeper segments must name
// the parent variant's step field (chain / loop → ``steps``; if → ``then_steps`` / ``else_steps``).
export type ActionPath = readonly ActionPathStep[]

export interface ActionPathStep {
  /** Name of the step list inside the parent action. ``null`` for the root segment (the path's
   *  first step, which indexes the top-level actions list — there's no parent). */
  field: 'steps' | 'then_steps' | 'else_steps' | null
  /** Index within the step list. */
  i: number
}

type Row = Record<string, unknown>

/** Resolve a path against a root actions list. Returns ``undefined`` when any segment falls
 *  off the end (the list has been mutated since the path was captured) — caller treats that as
 *  "selection vanished, pop back to the list". */
export function resolveAtPath(root: Row[], path: ActionPath): Row | undefined {
  if (path.length === 0) return undefined
  let arr: Row[] = root
  let cur: Row | undefined
  for (let depth = 0; depth < path.length; depth++) {
    const seg = path[depth]
    if (depth === 0) {
      if (seg.field != null) return undefined        // malformed — root segment must have null field
    } else {
      if (seg.field == null) return undefined        // malformed — non-root segment must name a field
      arr = (cur![seg.field] as Row[] | undefined) ?? []
    }
    cur = arr[seg.i]
    if (cur === undefined) return undefined
  }
  return cur
}

/** Apply a patch to the action at the given path; returns a fresh root list with only the
 *  affected branches re-built (immutable update). ``patch`` is merged onto the target action;
 *  keys whose value is ``undefined`` / ``null`` / ``''`` / ``false`` / an empty array are
 *  dropped (terser saved TOML).
 *
 *  An invalid path (missing segments / out-of-bounds index) returns the root unchanged. */
export function updateAtPath(root: Row[], path: ActionPath, patch: Row): Row[] {
  if (path.length === 0) return root
  const recur = (arr: Row[], depth: number): Row[] => {
    const seg = path[depth]
    if (seg.i < 0 || seg.i >= arr.length) return arr
    const next = arr.slice()
    const current = next[seg.i] as Row
    if (depth === path.length - 1) {
      const merged: Row = { ...current, ...patch }
      for (const k of Object.keys(patch)) {
        const v = patch[k]
        if (v === undefined || v === null || v === '' || v === false || (Array.isArray(v) && v.length === 0)) {
          delete merged[k]
        }
      }
      next[seg.i] = merged
      return next
    }
    const childSeg = path[depth + 1]
    if (childSeg.field == null) return arr           // malformed — non-root segment without a field
    const childArr = (current[childSeg.field] as Row[] | undefined) ?? []
    const updatedChild = recur(childArr, depth + 1)
    const updatedCurrent: Row = { ...current }
    if (updatedChild.length === 0) delete updatedCurrent[childSeg.field]
    else updatedCurrent[childSeg.field] = updatedChild
    next[seg.i] = updatedCurrent
    return next
  }
  return recur(root, 0)
}

/** Remove the action at the given path (the leaf segment). Returns a fresh root with the
 *  affected branch re-built. An invalid path → root unchanged.
 *
 *  Use case: the inspector's "Delete action" button — removes the focused action AND prunes
 *  any now-empty parent step lists from the saved TOML (the immutable rebuild drops empty
 *  arrays on the way back up). */
export function removeAtPath(root: Row[], path: ActionPath): Row[] {
  if (path.length === 0) return root
  const recur = (arr: Row[], depth: number): Row[] => {
    const seg = path[depth]
    if (seg.i < 0 || seg.i >= arr.length) return arr
    const next = arr.slice()
    if (depth === path.length - 1) {
      next.splice(seg.i, 1)
      return next
    }
    const current = next[seg.i] as Row
    const childSeg = path[depth + 1]
    if (childSeg.field == null) return arr
    const childArr = (current[childSeg.field] as Row[] | undefined) ?? []
    const updatedChild = recur(childArr, depth + 1)
    const updatedCurrent: Row = { ...current }
    if (updatedChild.length === 0) delete updatedCurrent[childSeg.field]
    else updatedCurrent[childSeg.field] = updatedChild
    next[seg.i] = updatedCurrent
    return next
  }
  return recur(root, 0)
}

/** Append a new action to the step list at the parent's path + field. Returns a fresh root.
 *  When ``field`` is ``null`` and ``parentPath`` is empty, appends to the root.
 *
 *  Used by the inspector's "Add step" buttons inside a workflow variant's editor — clicking
 *  Add inside an if's then-steps appends a new action to that branch. */
export function appendAtPath(
  root: Row[],
  parentPath: ActionPath,
  field: 'steps' | 'then_steps' | 'else_steps' | null,
  newAction: Row,
): Row[] {
  if (parentPath.length === 0 && field == null) return [...root, newAction]
  if (parentPath.length === 0 || field == null) return root      // invalid
  const recur = (arr: Row[], depth: number): Row[] => {
    const seg = parentPath[depth]
    if (seg.i < 0 || seg.i >= arr.length) return arr
    const next = arr.slice()
    const current = next[seg.i] as Row
    if (depth === parentPath.length - 1) {
      // leaf — append to the named field's list
      const childArr = Array.isArray(current[field]) ? (current[field] as Row[]).slice() : []
      childArr.push(newAction)
      next[seg.i] = { ...current, [field]: childArr }
      return next
    }
    const childSeg = parentPath[depth + 1]
    if (childSeg.field == null) return arr
    const childArr = (current[childSeg.field] as Row[] | undefined) ?? []
    const updatedChild = recur(childArr, depth + 1)
    next[seg.i] = { ...current, [childSeg.field]: updatedChild }
    return next
  }
  return recur(root, 0)
}

/** Walk the path, resolving each step's action so the breadcrumb can show
 *  ``Import Security › check_workbench › Delete Workbench``. Each crumb carries its prefix
 *  path so the consumer can wire click-to-jump. The returned list always starts with a root
 *  entry (the rootLabel) so the operator has a way to pop all the way back to the list. */
export function breadcrumbCrumbs(
  root: Row[],
  path: ActionPath,
  rootLabel: string,
): { label: string; path: ActionPath }[] {
  const crumbs: { label: string; path: ActionPath }[] = [{ label: rootLabel, path: [] }]
  for (let depth = 0; depth < path.length; depth++) {
    const prefix = path.slice(0, depth + 1)
    const a = resolveAtPath(root, prefix)
    if (!a) break
    const label = String(a.label ?? a.id ?? `step ${prefix[prefix.length - 1].i + 1}`)
    crumbs.push({ label, path: prefix })
  }
  return crumbs
}

/** Equality check between two paths — useful for matching the selected path in a list render
 *  without holding referential identity. */
export function pathEquals(a: ActionPath | null, b: ActionPath | null): boolean {
  if (a === b) return true
  if (a == null || b == null) return false
  if (a.length !== b.length) return false
  for (let i = 0; i < a.length; i++) {
    if (a[i].field !== b[i].field || a[i].i !== b[i].i) return false
  }
  return true
}

// ── chain-context source candidates (Theme B autocomplete) ──────────────────────────────────
//
// When the operator wires a ``ParamBind.source`` / ``Condition.source`` / ``LoopAction.source``,
// the resolvable paths at the firing site are: prompt-field values from the nearest enclosing
// :class:`ChainAction` (``INPUT.<name>``), bind_result captures from *preceding* sibling steps
// at any ancestor level (``<step_id>.first_row`` / ``<step_id>.rows``), and the current loop
// iteration element when we're somewhere inside a :class:`LoopAction.steps`. The text is the
// runtime semantics; the helper here walks the path to produce that catalog as design-time
// suggestions for the editor.
//
// Column suffixes (``find_user.first_row.EMAIL``) are *not* listed — the columns are only known
// once the query runs. The operator picks the step prefix from the dropdown, then types the
// column name. Same pattern as v1's TASK_<id>.RESULTS[0].<col> — the result-row path was
// shipped as a dotted prefix; the column was hand-typed.
export interface SourceCandidate {
  /** The dotted path to insert into the source field. */
  value: string
  /** Short human-friendly hint (rendered as the SearchSelect option's label). */
  label: string
  /** Grouping for visual organisation in the dropdown. */
  group: 'inputs' | 'step_results' | 'loop'
}

/** Build the candidate list for the action at ``path``. Walks from the root, gathering:
 *  - prompt fields from every enclosing ``ChainAction`` (the runtime merges them into INPUT
 *    when the chain fires; nested steps see them as ``INPUT.<name>``);
 *  - bind_result-capturing steps that *preceded* the current one at any ancestor level (the
 *    chain context accumulates as steps run; later steps reference earlier ones);
 *  - ``loop`` when any path segment dives into a ``LoopAction.steps`` (the loop binds the
 *    current element under ``loop`` for the nested body's binds to read).
 *
 *  Order matches the typical operator scan: inputs first (cheapest to wire), then step
 *  results (the workflow-internal data), then the loop binding (transient, only inside a loop).
 *
 *  An empty / invalid ``path`` returns ``[]`` — callers gate the suggestions with a length check. */
export function chainContextCandidates(root: Row[], path: ActionPath, columnsOf?: (step: Row) => string[]): SourceCandidate[] {
  if (path.length === 0) return []
  const seen = new Set<string>()
  const out: SourceCandidate[] = []
  const add = (cand: SourceCandidate): void => {
    if (seen.has(cand.value)) return
    seen.add(cand.value)
    out.push(cand)
  }

  let arr: Row[] = root
  let cur: Row | undefined
  let inLoop = false

  for (let depth = 0; depth < path.length; depth++) {
    const seg = path[depth]
    if (depth > 0) {
      if (seg.field == null) return out  // malformed — stop here
      arr = (cur![seg.field] as Row[] | undefined) ?? []
    }
    // Preceding siblings at this depth ran *before* the action we're editing.
    for (let i = 0; i < seg.i; i++) {
      addStepResultCandidates(arr[i] as Row, add, columnsOf)
    }
    cur = arr[seg.i]
    if (!cur) break
    // Mid-path action (not the leaf) — if it's a chain, its prompts are in scope for nested
    // steps. If it's a loop, ``loop`` is in scope.
    if (depth < path.length - 1) {
      if (cur.type === 'chain') {
        const prompts = Array.isArray(cur.prompt_fields) ? (cur.prompt_fields as Row[]) : []
        for (const pf of prompts) {
          const name = String(pf.name ?? '').trim()
          if (name) add({ value: `INPUT.${name}`, label: '(prompt input)', group: 'inputs' })
        }
      }
      if (cur.type === 'loop') inLoop = true
    }
  }
  if (inLoop) {
    add({ value: 'loop', label: '(current loop iteration — append .<col>)', group: 'loop' })
  }
  return out
}

/** What a value field holds once a suggestion is picked. A suggestion is a whole place a value
 *  comes from — an input, a column of an earlier step, the signed-in user — so it REPLACES what
 *  was there. One case continues instead: a bare column name picked at the end of a path that
 *  waits for one (``find_user.first_row`` + ``EMAIL`` → ``find_user.first_row.EMAIL``). */
export function applyPick(value: string, start: number, end: number, picked: string): { next: string; cursor: number } {
  const before = value.slice(0, start)
  const bare = !/[.#]/.test(picked)
  const waits = /(\.first_row|\.rows|^loop|\.)$/.test(before.trim()) && value.slice(end).trim() === ''
  const next = bare && waits ? `${before}${before.endsWith('.') ? '' : '.'}${picked}` : picked
  return { next, cursor: next.length }
}

/** The kinds of step whose result the following steps can read. */
const RESULT_STEPS = new Set(['run_query', 'call_api', 'call_plugin'])

// What an earlier step returned is offered whether or not the step is marked as keeping its
// result: picking it marks the step (:func:`withCapturedResults`), so nobody has to know the
// flag exists. When the columns of the step are known (*columnsOf*: its query is a screen's
// read), each column of its first row is a pick of its own.
function addStepResultCandidates(action: Row, add: (c: SourceCandidate) => void, columnsOf?: (step: Row) => string[]): void {
  if (!RESULT_STEPS.has(String(action?.type ?? ''))) return
  const id = String(action.id ?? '').trim()
  if (!id) return
  const name = String(action.label ?? '').trim() || id
  for (const col of columnsOf?.(action) ?? []) {
    add({ value: `${id}.first_row.${col}`, label: `${col} — first row of "${name}"`, group: 'step_results' })
  }
  add({ value: `${id}.first_row`, label: `first row of "${name}" (add .COLUMN)`, group: 'step_results' })
  add({ value: `${id}.rows`, label: `every row of "${name}"`, group: 'step_results' })
}

/** Every value a step reads from somewhere (its binds, its condition, its loop, what it sets or answers). */
function sourcesOf(action: Row): string[] {
  const out: string[] = []
  const text = (v: unknown) => { if (typeof v === 'string' && v.trim()) out.push(v.trim()) }
  for (const key of ['param_binds', 'lookup_param_binds']) {
    for (const b of Array.isArray(action[key]) ? (action[key] as Row[]) : []) text(b?.source)
  }
  text(action.source)
  text((action.condition as Row | undefined)?.source)
  for (const v of Object.values((action.bindings as Record<string, unknown> | undefined) ?? {})) text(v)
  return out
}

/** *root* with every step whose result another step reads marked as keeping it
 *  (``bind_result``). A step already marked, or read by nobody, is left as it is — the flag is
 *  never taken away here. Returns *root* itself when nothing changes. */
export function withCapturedResults(root: Row[]): Row[] {
  const read = new Set<string>()
  const collect = (list: unknown): void => {
    for (const a of Array.isArray(list) ? (list as Row[]) : []) {
      for (const s of sourcesOf(a)) {
        const m = /^([^.\s#]+)\.(first_row|rows)\b/.exec(s)
        if (m) read.add(m[1])
      }
      collect(a.steps); collect(a.then_steps); collect(a.else_steps)
    }
  }
  collect(root)
  if (read.size === 0) return root
  // a list comes back as the same array when none of its steps changed
  const mark = (list: Row[]): Row[] => {
    const out = list.map((a) => {
      let next = a
      for (const field of ['steps', 'then_steps', 'else_steps']) {
        if (Array.isArray(a[field])) {
          const inner = mark(a[field] as Row[])
          if (inner !== a[field]) next = { ...next, [field]: inner }
        }
      }
      if (RESULT_STEPS.has(String(a.type ?? '')) && read.has(String(a.id ?? '').trim()) && a.bind_result !== true) {
        next = { ...next, bind_result: true }
      }
      return next
    })
    return out.every((a, i) => a === list[i]) ? list : out
  }
  return mark(root)
}
