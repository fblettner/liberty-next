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
// ``source`` paths come in four flavours:
//
// * **Plain** (no dots, e.g. ``USR_ID``) — a form-field reference. Falls back to ``formCtx``;
//   keeps the slice-2 behaviour for hand-written dialogs that don't use chain context.
// * **``INPUT.<X>``** — reads the prompt-collected (or caller-passed) values stored under
//   ``ctx.INPUT.<X>``. Case-insensitive segment matching.
// * **``<step_id>.first_row.<col>`` / ``<step_id>.rows.<N>.<col>``** — references a previous
//   step's captured result. Numeric segments index arrays; non-numeric segments lookup object
//   keys (case-insensitive). ``loop.<field>`` reads the current iteration's element.
// * **``#LOGIN_USER#`` / ``#SYSDATE#`` / …** — auth + runtime built-ins. v2's port of v1's
//   `dd_rules` LOGIN / SYSDATE / CURRENT_DATE / PASSWORD resolvers. See :func:`resolveBuiltin`.
//
// Anything else starting with ``#`` (an unknown built-in) → ``undefined``, same as before.
export function resolveSource(path: string, ctx: ChainCtx, formCtx: Row): unknown {
  if (!path) return undefined
  if (path.startsWith('#') && path.endsWith('#') && path.length > 2) {
    return resolveBuiltin(path.slice(1, -1))
  }
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

// ── built-in resolvers (v2's port of v1's `dd_rules` LOGIN / SYSDATE / CURRENT_DATE) ─────────
//
// A small set of runtime-provided values an action's ``ParamBind {source: '#X#'}`` can read.
// Static date / language values resolve from the JS runtime; auth-dependent ones (the user's
// username / email) read from the auth context installed via :func:`installAuthBuiltins` —
// the calling layer wires this up once at mount so the runner doesn't need a React handle.
//
// All built-ins return ``undefined`` if not installed; ``resolveBinds`` then skips the bind
// (same behaviour as a missing chain-context path — no NULL gets sent server-side).

interface BuiltinProvider {
  /** Currently authenticated user's username — wired from ``useAuth().user?.username``. */
  username?: string | null
  /** Currently authenticated user's email — null if the IdP didn't return one. */
  email?: string | null
  /** Current i18n language tag, ``en`` / ``fr`` / …. */
  language?: string | null
}
let authProvider: BuiltinProvider = {}

/** Install the auth + language built-in values. Call once on app mount (Layout / App) +
 *  whenever the user / language changes. Subsequent ``resolveSource('#LOGIN_USER#', …)`` calls
 *  read from the installed provider. */
export function installAuthBuiltins(next: BuiltinProvider): void {
  authProvider = next
}

/** Resolve one built-in by its inner key (the part between the ``#`` markers). Returns
 *  ``undefined`` for an unknown key or an uninstalled auth value — callers (``resolveBinds``)
 *  drop the bind silently. */
function resolveBuiltin(key: string): string | undefined {
  const k = key.toUpperCase()
  // Auth-bound — wait on the installed provider.
  if (k === 'LOGIN_USER' || k === 'LOGIN' || k === 'USER') {
    return authProvider.username ?? undefined
  }
  if (k === 'LOGIN_EMAIL' || k === 'EMAIL') {
    return authProvider.email ?? undefined
  }
  if (k === 'LANGUAGE' || k === 'LANG') {
    return authProvider.language ?? undefined
  }
  // Date / time — resolved lazily at each call (so a chain spanning seconds picks them up).
  // ``SYSDATE`` matches Oracle's lingo; ``CURRENT_DATE`` matches Postgres + v1 dd_rules.
  if (k === 'SYSDATE' || k === 'CURRENT_DATE' || k === 'TODAY') {
    return new Date().toISOString().split('T')[0]    // YYYY-MM-DD
  }
  if (k === 'NOW' || k === 'CURRENT_TIMESTAMP') {
    return new Date().toISOString()                  // YYYY-MM-DDTHH:mm:ss.sssZ
  }
  if (k === 'CURRENT_TIME') {
    return new Date().toISOString().split('T')[1]?.slice(0, 8)   // HH:MM:SS
  }
  return undefined
}

/** Resolve a form-side ``auto_fill`` rule's source id (e.g. ``"current_date"`` / ``"login_user"``)
 *  to the value that should pre-fill the field on dialog open (add mode only). v2's port of
 *  v1's ``dd_rules`` SYSDATE / CURRENT_DATE / LOGIN. Source ids match the matching built-ins
 *  (uppercased), so a single source of truth keeps action-bind built-ins + form auto-fills
 *  consistent. Unknown source → ``undefined`` (the seeder treats that as "no auto-fill"). */
export function resolveAutoFill(source: string): string | undefined {
  const v = resolveBuiltin(source.toUpperCase())
  return v == null || v === '' ? undefined : v
}

/** Catalog of built-in source paths surfaced as autocomplete options in the ParamBind editor.
 *  The runtime resolver above is the source of truth — keep them in sync. Labels are i18n-able
 *  *only* at the caller (the editor is the single consumer); the runtime never reads these. */
export const BUILTIN_SOURCE_PATHS: ReadonlyArray<{ value: string; label: string }> = [
  { value: '#LOGIN_USER#',  label: 'Connected user (username)' },
  { value: '#LOGIN_EMAIL#', label: 'Connected user (email)' },
  { value: '#LANGUAGE#',    label: 'Current language (en / fr / …)' },
  { value: '#SYSDATE#',     label: 'Today (YYYY-MM-DD)' },
  { value: '#NOW#',         label: 'Now (ISO timestamp)' },
  { value: '#CURRENT_TIME#', label: 'Current time (HH:MM:SS)' },
]

/** Resolve a ParamBind list to a string-keyed param dict. Drops null / empty values (the
 *  call site decides whether that means "skip the bind" or "send NULL" — for v2 SQL writes
 *  the migrated queries omit-binding-the-key matches the desired "leave column unchanged"
 *  semantic). ``#X#`` built-ins (LOGIN_USER / SYSDATE / NOW / …) resolve via
 *  :func:`resolveBuiltin`; an *unknown* / uninstalled built-in is dropped (same as a missing
 *  chain-context path).
 *
 *  ``ParamBind.default`` (v2's port of v1's ``ly_act_tasks_params.map_default``) is the
 *  fallback bound in *source mode* when the source resolves to NULL / empty. Useful for an
 *  optional workflow input where a blank source should default to a sane literal (e.g. an
 *  ``UPMJ`` defaulting to today / ``"0"`` for a numeric column). The default itself is also
 *  built-in-aware — a default of ``"#SYSDATE#"`` resolves to today's date. In *value mode*
 *  the literal already wins outright, so ``default`` is ignored. */
export function resolveBinds(
  binds: ReadonlyArray<ParamBind> | undefined,
  ctx: ChainCtx,
  formCtx: Row,
): Record<string, string> {
  const out: Record<string, string> = {}
  for (const b of binds ?? []) {
    if (b.value != null && b.value !== '') { out[b.param] = String(b.value); continue }
    if (b.source) {
      const v = resolveSource(b.source, ctx, formCtx)
      if (v != null && String(v) !== '') { out[b.param] = String(v); continue }
    }
    // Source resolved to nothing → try the default. v1's ``map_default`` was almost always
    // a literal (``"0"``, ``""``, an account-code default), but v2 lets it also be a built-in
    // (``"#SYSDATE#"`` / ``"#LOGIN_USER#"``) or a chain-context path. We dispatch by *shape*:
    //   * starts + ends with ``#`` → built-in (resolves via ``resolveSource``);
    //   * contains a dot → chain-context path (drops the default silently if it doesn't
    //     resolve — a typo in a path shouldn't accidentally bind the path string itself);
    //   * otherwise → literal, bound verbatim.
    if (b.default != null && b.default !== '') {
      const d = String(b.default)
      const isBuiltin = d.startsWith('#') && d.endsWith('#') && d.length > 2
      const isPath = d.includes('.')
      if (isBuiltin || isPath) {
        const dv = resolveSource(d, ctx, formCtx)
        if (dv != null && String(dv) !== '') out[b.param] = String(dv)
      } else {
        out[b.param] = d
      }
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
        // ``QueryResult.to_dict`` returns ``{rows, columns, row_count, rowcount, statement_type,
        // …}``. If we get past ``api.post`` without throwing, the call succeeded; on error
        // ``api.post`` raises ``ApiError`` which the outer ``try`` catches.
        const target = a.connector || deps.defaultConnector
        const bound = resolveBinds(a.param_binds, ctx, formCtx)
        const resp = await api.post<{
          rows?: Row[]; columns?: unknown; row_count?: number; rowcount?: number;
          statement_type?: string;
        }>(
          `/api/sql/${encodeURIComponent(target)}/${encodeURIComponent(a.query)}`,
          { params: withUpper(bound) },
        )
        // 0-row write surface — a write that affected zero rows is almost always a config
        // bug (CHAR-padding mismatch, wrong query variant ``_put`` vs ``_post``, etc.).
        // Surface as a warning on the chain result so the dialog can show it; the backend
        // logs the same case to the server console for the operator's logs.
        if (resp?.statement_type && resp.statement_type !== 'SELECT' && resp.rowcount === 0) {
          result.warnings.push(
            `${a.label || a.id}: ${resp.statement_type} affected 0 rows on ${target}.${a.query}.`,
          )
        }
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
        // ``ApiResult.to_dict`` from the backend is
        // ``{success, status_code, url, json, body, extracted, mapped, error, duration_ms}``
        // — the parsed payload is in ``json`` (null when the response wasn't JSON), with the
        // raw text in ``body``. Note: a non-2xx response is **not** an exception — the route
        // returns HTTP 200 with ``success: false`` set, so we check the flag explicitly.
        const bound = resolveBinds(a.param_binds, ctx, formCtx)
        const resp = await api.post<{
          success?: boolean
          status_code?: number
          error?: string | null
          json?: unknown
          body?: string | null
          extracted?: unknown
        }>(
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
          // Prefer the endpoint's ``response_field`` projection (``extracted``) when configured —
          // that's the operator's "what I actually care about" path. Else use the parsed JSON
          // payload. Wrap whatever we end up with in a single-element ``rows`` array (list
          // payloads pass through) so the chain-context shape stays uniform: ``<id>.first_row.<col>``
          // works for object payloads, ``<id>.rows`` for arrays.
          const payload = resp?.extracted != null ? resp.extracted : resp?.json
          const rows = Array.isArray(payload) ? (payload as Row[]) : (payload == null ? [] : [payload as Row])
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
