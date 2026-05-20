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
 *  dropped (terser saved TOML, matches :func:`ActionListEditor.updateAction`'s behaviour).
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
