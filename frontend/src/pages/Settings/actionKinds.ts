// The kinds of action a screen or a service is made of (see :class:`Action` in
// :mod:`liberty.screens.config`): their names in the schema, the smallest valid shape of each, the
// settings each shows, and the keys of a question asked first (``prompt_fields``). Shared by the
// action tree (ActionTreeView) and the flow (ActionFlow). No React here.

type Row = Record<string, unknown>

// ── action variants ──────────────────────────────────────────────────────────────────────
// Discriminated union over the seven action types (see :class:`Action` in
// :mod:`liberty.screens.config`). Each maps to its $def name; ``blankActionOfType`` seeds the
// minimum-viable shape when the operator switches types so SchemaForm doesn't render a sea of
// validation red on first paint.
export const ACTION_TYPES = [
  'run_query', 'call_api', 'call_plugin', 'call_action', 'navigate', 'open_url', 'set_field', 'confirm', 'notify', 'send_notification', 'refresh',
  // Slice 4d step variants (Phase 6) — workflow control flow.
  'chain', 'if', 'loop', 'return',
] as const
export type ActionType = typeof ACTION_TYPES[number]
export const ACTION_DEF_NAME: Record<ActionType, string> = {
  run_query: 'RunQueryAction',
  call_api: 'CallApiAction',
  call_plugin: 'CallPluginAction',
  call_action: 'CallActionAction',
  send_notification: 'SendNotificationAction',
  navigate: 'NavigateAction',
  open_url: 'OpenUrlAction',
  set_field: 'SetFieldAction',
  confirm: 'ConfirmAction',
  notify: 'NotifyAction',
  refresh: 'RefreshAction',
  chain: 'ChainAction',
  if: 'IfAction',
  loop: 'LoopAction',
  return: 'ReturnAction',
}
export function blankActionOfType(t: ActionType, id: string): Row {
  const base: Row = { id, type: t }
  if (t === 'run_query') base.query = ''
  if (t === 'call_api') { base.connector = ''; base.endpoint = '' }
  if (t === 'call_plugin') base.callable = ''
  if (t === 'call_action') base.ref = ''
  if (t === 'navigate') base.to = ''
  if (t === 'open_url') base.url = 'https://'
  if (t === 'set_field') base.target = ''
  if (t === 'confirm') base.message = ''
  if (t === 'notify') { base.message = ''; base.tone = 'info' }
  if (t === 'send_notification') base.rule = ''
  if (t === 'chain') base.steps = []
  if (t === 'if') {
    base.condition = { source: '', operator: 'truthy' }
    base.then_steps = []
    base.else_steps = []
  }
  if (t === 'loop') { base.source = ''; base.steps = [] }
  if (t === 'return') base.bindings = {}
  return base
}
// Properties we render as custom widgets (above / below the variant SchemaForm) — strip them
// out of the variant schema so SchemaForm doesn't double-render them. The workflow-control
// variants (chain / if / loop / return) carry nested ``Action[]`` arrays + a ``Condition`` —
// those get dedicated recursive editors below; without stripping, SchemaForm would render
// them as generic object-list accordions with cramped widget choices.
export const ACTION_OVERRIDE_KEYS: Record<ActionType, ReadonlyArray<string>> = {
  run_query: ['connector', 'query', 'param_binds'],
  call_api: ['connector', 'endpoint', 'param_binds'],
  call_plugin: ['callable', 'param_binds'],
  call_action: ['ref', 'param_binds'],
  send_notification: ['rule', 'param_binds'],
  navigate: ['connector', 'to', 'screen', 'param_binds', 'inline'],
  open_url: [],
  set_field: [],
  confirm: [],
  notify: [],
  refresh: [],
  chain: ['steps'],
  if: ['condition', 'then_steps', 'else_steps'],
  loop: ['source', 'steps'],   // ``source`` → custom autocomplete (Theme B) in ActionTreeView;
                                // ``steps`` → recursive sub-list editor in both views
  return: [],        // ``bindings: Record<str,str>`` renders fine via SchemaForm's StringMap
  // ``param_binds`` on the three ParamBind-bearing variants is rendered by the dedicated
  // ParamBindList component (one row per bind: param name + value/source toggle + the
  // matching widget — autocomplete on source). SchemaForm's generic object-list accordion
  // was too cramped for the workflow editing case.
}
// The four variants that carry ``prompt_fields`` (the ``_PromptableMixin`` in
// :mod:`liberty.screens.config`). ``chain`` joins the trio so a chain-fired workflow can
// open a single pre-fire prompt for the operator's inputs. For the rest (set_field / confirm
// / notify / refresh / if / loop / return) the PromptField editor stays hidden — these are
// internal steps that don't fire their own prompt sub-dialogs.
export const PROMPTABLE_ACTION_TYPES = new Set<ActionType>(['run_query', 'call_api', 'call_plugin', 'navigate', 'open_url', 'chain', 'call_action'])
export const PROMPT_FIELDS_KEY = 'prompt_fields'
// PromptField has 11+ properties; split into Basic / Advanced / Lookup binds / Conditions so
// each per-field expander reads like the visual builder's field inspector instead of a wall of
// toggles. ``pickSchemaProperties`` flattens ``x_group`` so the picked fields all sit on one
// SchemaForm (no tab strip inside an already-expanded row).
export const PROMPT_FIELD_DEF_NAME = 'PromptField'
export const PROMPT_FIELD_BASIC_KEYS = ['name', 'dd', 'label', 'format', 'required', 'default'] as const
export const PROMPT_FIELD_ADVANCED_KEYS = ['hidden', 'disabled', 'colspan'] as const
export const PROMPT_FIELD_BINDS_KEY = 'lookup_param_binds'
export const PROMPT_FIELD_CONDITION_KEYS = ['visible_when', 'required_when', 'disabled_when'] as const
