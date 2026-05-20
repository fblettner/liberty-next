// Recursive action runner — fires a list of v2 ``Action``s (typically a screen hook like
// ``dialog.on_save`` or a per-tab button click) and walks ``ChainAction.steps`` / IfAction
// branches / LoopAction iterations recursively. Maintains a chain context that accumulates
// each step's result so later steps can read ``ParamBind {source: "<step_id>.first_row.<col>"}``
// (v2's port of v1's ``allParams.current = {INPUT, TASK_<id>: {RESULTS: …}}``).
//
// Why a separate module: the original runner lived inline in :file:`ScreenDialog.tsx`. The
// recursion + new step variants would make that file too long; the runner is also called from
// other places (e.g. row_menu fires) that can now share the same code path.
//
// Slice B scope:
// - run_query / call_api with ``bind_result`` capture rows in the chain context
// - chain / if / loop / return execute correctly
// - ParamBind.source supports dotted paths: ``INPUT.AUUSER`` / ``select.first_row.COL`` /
//   ``select.rows.0.COL`` / ``loop.OBJECT`` — with case-insensitive segment matching
// - prompt_fields collected once per Action (v2's port of v1's ly_act_params)
//
// Slice B does *not* tackle: navigate (URL push from the runner), set_field side-effects,
// confirm modals from inside the runner (the dialog injects its own confirm provider).
import { api, ApiError } from '../../api/client'
import type { Action, Condition, ParamBind, PromptField } from '../../types/screens'

export type Row = Record<string, unknown>

/** The chain context — accumulates as the chain walks. v2's port of v1's flat
 *  ``allParams.current = {INPUT, TASK_<id>: {RESULTS: …}}``. Each ``run_query`` / ``call_api``
 *  with ``bind_result = true`` lands its rows under the step's ``id`` as
 *  ``{rows, first_row, success}`` so subsequent steps reference them via
 *  ``ParamBind {source: '<id>.first_row.<col>'}``. ``loop`` is set inside a :class:`LoopAction`
 *  iteration and points at the current element. */
export interface ChainCtx {
  INPUT: Row
  loop?: Row
  // Any other key is a step's bind_result payload: { rows, first_row, success }.
  [stepId: string]: unknown
}

/** Result of running a list of actions — surfaces warnings / refresh request / soft cancel /
 *  any values a :class:`ReturnAction` collected (caller writes them back to the form). */
export interface ChainResult {
  /** False when an action failed *and* its ``stop_on_error`` is not false. */
  ok: boolean
  /** Human-readable messages (notify actions, soft-error fallthroughs). */
  warnings: string[]
  /** True when a :class:`RefreshAction` fired — caller re-runs the read query. */
  refresh: boolean
  /** True when a prompt was cancelled — caller treats as a no-op, no error banner. */
  cancelled: boolean
  /** ``{caller_field_name: value}`` written by :class:`ReturnAction` / :class:`SetFieldAction`. */
  returnedValues: Row
  /** Set when ``ok`` is false — the message to show the operator. */
  error?: string
}

/** Pluggable dependencies the runner needs from the calling component. Keeping them in a
 *  deps bag means the runner stays decoupled from the React layer + we can mock for tests. */
export interface ActionRunnerDeps {
  /** SQL connector used when a ``run_query`` / ``navigate`` action doesn't set its own. */
  defaultConnector: string
  /** Show the prompt dialog and resolve with values, or ``null`` for a soft cancel. */
  requestPrompt: (
    spec: { fields: PromptField[]; title: string | null; cols: number | null; submitLabel: string | null },
    fallbackTitle: string,
  ) => Promise<Row | null>
  /** Optional confirm provider — used by :class:`ConfirmAction`. */
  confirm?: (message: string, opts?: { confirmLabel?: string | null; cancelLabel?: string | null }) => Promise<boolean>
  /** Optional router push — used by :class:`NavigateAction`. The runner resolves the action's
   *  param_binds + passes them as ``params``; the caller decides how to build the URL (the
   *  row-menu / toolbar use ``/sql/<connector>/<to>?<qs>``). When unset, ``navigate`` actions
   *  land a soft warning (ScreenDialog has no navigation target; row-menu / toolbar do). */
  navigate?: (to: string, connector: string, params: Record<string, string>) => void
}

// ── source-path resolution ──────────────────────────────────────────────────────────────────
//
// ``source`` paths come in three flavours:
//
// * **Plain** (no dots, e.g. ``USR_ID``) — a form-field reference. Falls back to ``formCtx``;
//   keeps the slice-2 behaviour for hand-written dialogs that don't use chain context.
// * **``INPUT.<X>``** — reads the prompt-collected (or caller-passed) values stored under
//   ``ctx.INPUT.<X>``. Case-insensitive segment matching.
// * **``<step_id>.first_row.<col>`` / ``<step_id>.rows.<N>.<col>``** — references a previous
//   step's captured result. Numeric segments index arrays; non-numeric segments lookup object
//   keys (case-insensitive). ``loop.<field>`` reads the current iteration's element.
//
// Reserved built-ins (paths starting with ``#``) are silently dropped — wired in a later
// auth slice (`#LOGIN_USER#` / `#SYSDATE#` / …).
export function resolveSource(path: string, ctx: ChainCtx, formCtx: Row): unknown {
  if (!path || path.startsWith('#')) return undefined
  if (!path.includes('.')) {
    // Plain key: try chain context first (covers ``INPUT`` / ``loop`` / step ids), then form.
    if (path in ctx) return ctx[path]
    return resolveFromForm(path, formCtx)
  }
  const segments = path.split('.')
  let cur: unknown = ctx
  for (const seg of segments) {
    if (cur == null) return undefined
    if (Array.isArray(cur)) {
      const idx = Number(seg)
      if (!Number.isInteger(idx) || idx < 0 || idx >= cur.length) return undefined
      cur = cur[idx]
      continue
    }
    if (typeof cur === 'object') {
      const obj = cur as Record<string, unknown>
      if (seg in obj) { cur = obj[seg]; continue }
      const lower = seg.toLowerCase()
      const matched = Object.keys(obj).find(k => k.toLowerCase() === lower)
      cur = matched ? obj[matched] : undefined
      continue
    }
    return undefined
  }
  return cur
}

function resolveFromForm(name: string, formCtx: Row): unknown {
  if (name in formCtx) return formCtx[name]
  const lower = name.toLowerCase()
  const matched = Object.keys(formCtx).find(k => k.toLowerCase() === lower)
  return matched ? formCtx[matched] : undefined
}

/** Resolve a ParamBind list to a string-keyed param dict. Drops null / empty values (the
 *  call site decides whether that means "skip the bind" or "send NULL" — for v2 SQL writes
 *  the migrated queries omit-binding-the-key matches the desired "leave column unchanged"
 *  semantic). Reserved ``#`` source paths are skipped — auth built-ins, future slice. */
export function resolveBinds(
  binds: ReadonlyArray<ParamBind> | undefined,
  ctx: ChainCtx,
  formCtx: Row,
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const b of binds ?? []) {
    if (b.value != null && b.value !== '') { out[b.param] = String(b.value); continue }
    if (b.source && !b.source.startsWith('#')) {
      const v = resolveSource(b.source, ctx, formCtx)
      if (v != null && String(v) !== '') out[b.param] = String(v)
    }
  }
  return out
}

/** Evaluate a :class:`Condition` against the chain context. Source resolves the same way
 *  ParamBind.source does (dotted-path or form-field fallback). Operator semantics mirror v2's
 *  Pydantic ``Condition`` shape — see ``liberty/screens/config.py``. */
export function evalCondition(condition: Condition, ctx: ChainCtx, formCtx: Row): boolean {
  const value = resolveSource(condition.source, ctx, formCtx)
  const cmp = (condition.value ?? '').toString()
  switch (condition.operator) {
    case 'equals':
      return String(value ?? '') === cmp
    case 'not_equals':
      return String(value ?? '') !== cmp
    case 'truthy':
      return isTruthyV1(value)
    case 'falsy':
      return !isTruthyV1(value)
    case 'has_rows':
      return Array.isArray(value) && value.length > 0
    case 'no_rows':
      return !Array.isArray(value) || value.length === 0
    case 'greater_than':
      return Number(value) > Number(condition.value)
    case 'less_than':
      return Number(value) < Number(condition.value)
    default:
      return false
  }
}

/** v1-flavoured "is true" — handles the ``'Y'`` / ``'N'`` convention JDE workflows use plus
 *  the standard JS falsy set. ``'N'`` / ``'0'`` / ``'false'`` are treated as falsy regardless
 *  of casing. v2's hand-written conditions should generally use ``equals`` over a string;
 *  this helper exists to keep migrated v1 IFs working without re-wiring each one. */
function isTruthyV1(value: unknown): boolean {
  if (value == null || value === '') return false
  if (typeof value === 'boolean') return value
  if (typeof value === 'number') return value !== 0
  const s = String(value).trim().toLowerCase()
  if (s === '' || s === 'n' || s === 'no' || s === '0' || s === 'false' || s === 'null') return false
  return true
}

// ── core runner ─────────────────────────────────────────────────────────────────────────────
function withUpper(o: Row): Row {
  const out: Row = { ...o }
  for (const [k, v] of Object.entries(o)) out[k.toUpperCase()] = v
  return out
}

/** Pull the prompt spec off any promptable action variant. Returns null for non-promptable
 *  variants or when ``prompt_fields`` is empty. */
function actionPrompt(a: Action): { fields: PromptField[]; title: string | null; cols: number | null; submitLabel: string | null } | null {
  if (a.type !== 'run_query' && a.type !== 'call_api' && a.type !== 'navigate' && a.type !== 'chain') return null
  // The four promptable variants share the ``_PromptableMixin`` shape via the Pydantic union.
  const r = a as Action & {
    prompt_fields?: PromptField[]; prompt_title?: string | null;
    prompt_cols?: number | null; prompt_submit_label?: string | null
  }
  const fields = r.prompt_fields
  if (!fields || fields.length === 0) return null
  return {
    fields,
    title: r.prompt_title ?? null,
    cols: r.prompt_cols ?? null,
    submitLabel: r.prompt_submit_label ?? null,
  }
}

/** Run a single Action. Returns ``{abort}`` — true when the chain should stop (hard error or
 *  soft cancel). Mutates ``ctx`` (merges prompt values into INPUT, binds step results, sets
 *  ``loop`` during a LoopAction iteration) and ``result`` (warnings / refresh / error). */
async function runOneAction(
  a: Action,
  ctx: ChainCtx,
  formCtx: Row,
  deps: ActionRunnerDeps,
  result: ChainResult,
): Promise<{ abort: boolean }> {
  // Prompt-before-fire (only on promptable variants). Cancelling → soft abort, no error.
  const prompt = actionPrompt(a)
  if (prompt) {
    const v = await deps.requestPrompt(prompt, a.label || a.id)
    if (v == null) { result.cancelled = true; return { abort: true } }
    ctx.INPUT = { ...ctx.INPUT, ...v }
  }
  try {
    switch (a.type) {
      case 'chain': {
        for (const step of (a.steps as Action[] | undefined) ?? []) {
          const r = await runOneAction(step, ctx, formCtx, deps, result)
          if (r.abort) return r
        }
        break
      }
      case 'if': {
        const branch = evalCondition(a.condition, ctx, formCtx) ? (a.then_steps as Action[] | undefined) : (a.else_steps as Action[] | undefined)
        for (const step of branch ?? []) {
          const r = await runOneAction(step, ctx, formCtx, deps, result)
          if (r.abort) return r
        }
        break
      }
      case 'loop': {
        const arr = resolveSource(a.source, ctx, formCtx)
        if (!Array.isArray(arr)) {
          const msg = `loop '${a.id}': source '${a.source}' did not resolve to an array`
          if (a.stop_on_error !== false) { result.error = msg; result.ok = false; return { abort: true } }
          result.warnings.push(msg)
          break
        }
        const savedLoop = ctx.loop
        for (const elem of arr) {
          ctx.loop = (elem && typeof elem === 'object') ? (elem as Row) : { value: elem }
          for (const step of (a.steps as Action[] | undefined) ?? []) {
            const r = await runOneAction(step, ctx, formCtx, deps, result)
            if (r.abort) { ctx.loop = savedLoop; return r }
          }
        }
        ctx.loop = savedLoop
        break
      }
      case 'return': {
        for (const [destField, sourcePath] of Object.entries(a.bindings ?? {})) {
          const v = resolveSource(sourcePath, ctx, formCtx)
          if (v != null) result.returnedValues[destField] = v
        }
        break
      }
      case 'set_field': {
        let v: unknown
        if (a.value != null) v = a.value
        else if (a.source) v = resolveSource(a.source, ctx, formCtx)
        if (v != null) result.returnedValues[a.target] = v
        break
      }
      case 'run_query': {
        // ``QueryResult.to_dict`` returns ``{rows, columns, row_count, rowcount, …}``. If we
        // get past ``api.post`` without throwing, the call succeeded; on error ``api.post``
        // raises ``ApiError`` which the outer ``try`` catches.
        const target = a.connector || deps.defaultConnector
        const bound = resolveBinds(a.param_binds, ctx, formCtx)
        const resp = await api.post<{ rows?: Row[]; columns?: unknown; row_count?: number }>(
          `/api/sql/${encodeURIComponent(target)}/${encodeURIComponent(a.query)}`,
          { params: withUpper(bound) },
        )
        if (a.bind_result) {
          const rows = Array.isArray(resp?.rows) ? resp.rows : []
          ctx[a.id] = {
            rows,
            first_row: (rows[0] ?? {}) as Row,
            success: true,
          }
        }
        break
      }
      case 'call_api': {
        // ``ApiResult.to_dict`` from the backend is ``{success, status_code, data, error}``.
        // Note: a non-2xx response is **not** an exception — the route returns HTTP 200 with
        // ``success: false`` set. Check the body so ``bind_result`` reflects what happened.
        const bound = resolveBinds(a.param_binds, ctx, formCtx)
        const resp = await api.post<{ success?: boolean; data?: unknown; status_code?: number; error?: string | null }>(
          `/api/http/${encodeURIComponent(a.connector)}/${encodeURIComponent(a.endpoint)}`,
          bound,
        )
        if (resp?.success === false) {
          const msg = `${a.label || a.id}: API ${resp?.status_code ?? ''} ${resp?.error ?? ''}`.trim()
          if (a.stop_on_error !== false) { result.error = msg; result.ok = false; return { abort: true } }
          result.warnings.push(msg)
          break
        }
        if (a.bind_result) {
          // API responses don't have a standard shape — wrap ``data`` in a rows array so the
          // chain-context interface stays uniform. A list payload becomes ``rows`` directly; a
          // scalar / object payload becomes a single-element ``rows`` (so ``first_row.foo``
          // works regardless).
          const data = resp?.data
          const rows = Array.isArray(data) ? (data as Row[]) : (data == null ? [] : [data as Row])
          ctx[a.id] = {
            rows,
            first_row: (rows[0] ?? {}) as Row,
            success: true,
          }
        }
        break
      }
      case 'notify': {
        result.warnings.push(a.message)
        break
      }
      case 'refresh': {
        result.refresh = true
        break
      }
      case 'confirm': {
        if (deps.confirm) {
          const ok = await deps.confirm(a.message, { confirmLabel: a.confirm_label ?? undefined, cancelLabel: a.cancel_label ?? undefined })
          if (!ok) { result.cancelled = true; return { abort: true } }
        } else {
          // No confirm provider configured — treat as a notify warning so the operator notices
          // (don't silently auto-confirm; that could fire something irreversible).
          result.warnings.push(`confirm '${a.id}': no confirm provider`)
        }
        break
      }
      case 'navigate': {
        // Navigation is router-side — the caller (row-menu / toolbar) supplies the router push
        // via ``deps.navigate``. ScreenDialog hooks usually don't navigate (the dialog stays
        // open, the user saves and reads from there), so the dialog leaves ``navigate``
        // undefined; in that case we soft-warn so a stray ``navigate`` in a dialog chain
        // doesn't crash the page.
        if (deps.navigate) {
          const target = a.connector || deps.defaultConnector
          const bound = resolveBinds(a.param_binds, ctx, formCtx)
          deps.navigate(a.to, target, bound)
          // The route change will unmount the firing surface — abort the rest of the chain.
          return { abort: true }
        }
        const msg = `action '${a.id}' (navigate) — no navigate handler in this context`
        console.warn(msg)  // eslint-disable-line no-console
        if (a.stop_on_error !== false) { result.error = msg; result.ok = false; return { abort: true } }
        result.warnings.push(msg)
        break
      }
    }
    return { abort: false }
  } catch (e) {
    const msg = `${a.label || a.id}: ${e instanceof ApiError ? e.message : String(e)}`
    if (a.stop_on_error !== false) { result.error = msg; result.ok = false; return { abort: true } }
    result.warnings.push(msg)
    return { abort: false }
  }
}

/** Top-level entry — runs a list of actions sequentially with a fresh chain context. The
 *  ``initialInput`` seeds ``ctx.INPUT`` (caller-passed params + any pre-existing form state
 *  the caller wants to expose under the INPUT bucket). ``formCtx`` is the firing context the
 *  plain-field source fallback reads from (dialog form / clicked row).
 *
 *  ScreenDialog calls this for ``dialog.on_load`` / ``on_save`` / ``on_cancel`` / per-tab
 *  ``actions`` button clicks. Future row_menu / toolbar wiring (Slice C) will reuse it. */
export async function runChain(
  actions: ReadonlyArray<Action>,
  initialInput: Row,
  formCtx: Row,
  deps: ActionRunnerDeps,
): Promise<ChainResult> {
  const ctx: ChainCtx = { INPUT: { ...initialInput } }
  const result: ChainResult = {
    ok: true,
    warnings: [],
    refresh: false,
    cancelled: false,
    returnedValues: {},
  }
  for (const a of actions) {
    const { abort } = await runOneAction(a, ctx, formCtx, deps, result)
    if (abort) break
  }
  return result
}
