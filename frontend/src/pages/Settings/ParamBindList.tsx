// Dedicated editor for a ``list[ParamBind]`` — replaces SchemaForm's generic
// :class:`ObjectListEditor` rendering. Each bind is one compact row:
//
//   [param]  [val|src toggle]  [literal value text input  | source SearchSelect]  [×]
//
// The source widget is the chain-context-aware combobox (allowCustom over the candidates the
// caller computes — :func:`chainContextCandidates` from :file:`actionPath.ts` in the action
// editor's case, or the action's own prompt_fields in the dialog-hook editor's case). The
// value widget is a plain text input — what the SchemaForm rendering used to give for every
// field. Flipping the toggle clears the other mode so the saved TOML only carries one of
// ``value`` / ``source``.
//
// Why a dedicated component rather than extending SchemaForm: SchemaForm's generic
// object-list-of-rows rendering is an accordion (expand to edit each item's three fields),
// which is cramped for a ParamBind — operators rarely care about the param-bind metadata in
// isolation; they want the whole list visible at once. A row-based layout fits both better.
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Plus, Trash2 } from 'lucide-react'
import {
  Button, Input, SearchSelect, Stack, type SearchSelectOption,
} from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

/** One ParamBind shape — matches :class:`liberty.connectors.config.ParamBind`. */
export interface ParamBind {
  /** The parameter name on the target query / endpoint. */
  param: string
  /** Literal value (mode A — drop ``source`` when set). */
  value?: string | null
  /** Source path read at call time (mode B — drop ``value`` when set). The runtime resolves
   *  this against the chain context first (dotted paths) then falls back to the firing
   *  context (form / row); the operator picks from the autocomplete or types a custom path. */
  source?: string | null
}

const Row = styled.div`
  display: grid; grid-template-columns: 1fr auto 2fr auto; align-items: center; gap: 6px;
  /* On narrow inspectors (the Visual Designer's right column is 340 px) the source SearchSelect
     needs at least ~150 px to read sensibly — give it the larger fraction and let the param
     name field share what's left. */
  @container (max-width: 320px) {
    grid-template-columns: 1fr auto; grid-auto-rows: auto;
    & > :nth-child(3), & > :nth-child(4) { grid-column: 1 / -1; }
  }
`
// Segmented two-pill toggle: ``val`` on the left, ``src`` on the right. The *active* side is
// highlighted in blue (no hover affordance — clicking it would be a no-op); the *inactive*
// side is muted and flips the mode on click. This is the v2 replacement for the previous
// single-button toggle, which read "src" / "val" as the *current* mode and flipped on every
// click — operators kept clicking the active side expecting a popover and getting a wipe.
const ToggleGroup = styled.div`
  display: inline-flex; border: 1px solid ${colors.border}; border-radius: ${radius.sm};
  background: ${colors.bg.input}; overflow: hidden;
`
const ToggleSeg = styled.button<{ $active: boolean }>`
  height: 30px; padding: 0 8px; border: none; background: transparent;
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.muted)};
  font-family: ${fonts.mono}; font-size: ${fontSize.micro}; font-weight: 600;
  min-width: 34px; cursor: ${({ $active }) => ($active ? 'default' : 'pointer')};
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  & + & { border-left: 1px solid ${colors.border}; }
  &:hover { color: ${({ $active }) => ($active ? colors.blue.main : colors.blue.main)};
    background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.card)}; }
`
const RemoveBtn = styled.button`
  height: 30px; width: 30px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid transparent; background: transparent; color: ${colors.text.muted};
  border-radius: ${radius.sm}; cursor: pointer;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; background: ${colors.red.bg}; }
`
const EmptyHint = styled.div`color: ${colors.text.muted}; font-size: ${fontSize.micro}; font-style: italic; padding: 4px 0;`

export interface ParamBindListProps {
  /** Current binds (defaults to ``[]`` when undefined). */
  value: ParamBind[] | undefined
  /** Emits the next list — caller decides whether to drop an empty list (the migrator's
   *  default-stripping convention) or keep it. The runtime treats both ``null`` and ``[]`` as
   *  "no binds", so callers usually pass through ``next.length ? next : null``. */
  onChange: (next: ParamBind[]) => void
  /** Source-field autocomplete candidates. ``allowCustom`` stays on in the SearchSelect so
   *  the operator can type any path the suggestions don't cover. */
  sourceOptions: SearchSelectOption[]
  /** ``param``-field autocomplete candidates — the *target* query's declared params +
   *  scanned ``:bind_params``, or an endpoint's declared params. v1's row context menus +
   *  drill-into-another-table workflows surface these so the developer picks the destination
   *  column name from a list instead of remembering it. ``[]`` falls back to a plain input. */
  paramOptions?: SearchSelectOption[]
  /** Placeholder for the param-name input — defaults to ``param``. Lets a caller specialise
   *  ("loop binding name" inside a LoopAction, etc.) without re-i18n-ing the same key. */
  paramPlaceholder?: string
}

export default function ParamBindList({
  value, onChange, sourceOptions, paramOptions, paramPlaceholder,
}: ParamBindListProps) {
  const { t } = useTranslation()
  const binds = value ?? []

  // Mode detection: source mode iff the ``source`` *key* is present (string, including ``''``).
  // Value mode iff ``value`` is present and ``source`` isn't. An empty bind (neither key set)
  // defaults to source mode — matches the v1 dev convention "binds usually read from somewhere".
  //
  // Checking key *presence* (typeof === 'string') rather than non-empty content is what lets
  // ``flipMode`` work: after a flip, the new mode's field is seeded as ``''`` and stays that way
  // until the operator types into it; mode detection still reads "value mode" because the key
  // exists. (The previous "non-empty content wins" check meant a freshly-flipped empty bind
  // read back as source mode no matter what, and the operator's click looked silently broken.)
  const isSourceMode = (b: ParamBind): boolean => {
    if (typeof b.source === 'string') return true
    if (typeof b.value === 'string') return false
    return true
  }

  // Generic patch — strip ``null``/``undefined`` so the saved TOML stays terse, but keep ``''``
  // as a "field present" marker (mode flip relies on it, and the migrator's default-stripping
  // pass on the backend already drops the empty when serialising to TOML).
  const updateBind = (idx: number, patch: Partial<ParamBind>) => {
    const next = binds.slice()
    next[idx] = { ...next[idx], ...patch }
    for (const k of Object.keys(patch) as (keyof ParamBind)[]) {
      if (k === 'param') continue
      const v = (next[idx] as ParamBind)[k]
      if (v == null) delete (next[idx] as ParamBind)[k]
    }
    onChange(next)
  }
  // Flip the bind's mode. Doesn't go through ``updateBind`` because we want the *new* mode's
  // field to land as ``''`` (the key-presence marker) AND the *old* mode's field to vanish —
  // both in one ``onChange`` so the row re-renders against the new mode without flicker.
  const flipMode = (idx: number) => {
    const next = binds.slice()
    const b = { ...next[idx] }
    if (isSourceMode(b)) {
      delete b.source
      b.value = ''
    } else {
      delete b.value
      b.source = ''
    }
    next[idx] = b
    onChange(next)
  }
  const addBind = () => onChange([...binds, { param: '', source: '' }])
  const removeBind = (idx: number) => {
    const next = binds.slice(); next.splice(idx, 1); onChange(next)
  }

  return (
    <Stack gap={6}>
      {binds.length === 0 && <EmptyHint>{t('settings.screens.paramBinds.empty')}</EmptyHint>}
      {binds.map((b, i) => {
        const sourceMode = isSourceMode(b)
        return (
          <Row key={i}>
            {/* ``param`` autocompletes from the target query / endpoint's params + bind_params
                when ``paramOptions`` is provided (the caller knows the action's target — see
                ActionTreeView.targetParamOptions). ``allowCustom`` stays on so the operator
                can type any param the dropdown doesn't list. Empty options → plain text input
                so the SearchSelect's dropdown doesn't suggest the empty state is broken. */}
            {paramOptions && paramOptions.length > 0 ? (
              <SearchSelect
                value={b.param ?? ''}
                options={paramOptions}
                onChange={(v) => updateBind(i, { param: v ?? '' })}
                allowCustom
                placeholder={paramPlaceholder ?? t('settings.screens.paramBinds.paramPlaceholder')}
              />
            ) : (
              <Input
                value={b.param ?? ''}
                onChange={(e) => updateBind(i, { param: e.target.value })}
                placeholder={paramPlaceholder ?? t('settings.screens.paramBinds.paramPlaceholder')}
              />
            )}
            {/* Segmented mode picker: literal ``val`` ⇄ chain-context ``src``. The active side
                paints blue + is a no-op; the inactive side flips. Tooltips on each segment
                describe what it does so the operator knows what they're picking without a wipe. */}
            <ToggleGroup>
              <ToggleSeg
                type="button"
                $active={!sourceMode}
                onClick={() => { if (sourceMode) flipMode(i) }}
                title={t('settings.screens.paramBinds.modeValueTip')}
              >
                {t('settings.screens.paramBinds.modeValue')}
              </ToggleSeg>
              <ToggleSeg
                type="button"
                $active={sourceMode}
                onClick={() => { if (!sourceMode) flipMode(i) }}
                title={t('settings.screens.paramBinds.modeSourceTip')}
              >
                {t('settings.screens.paramBinds.modeSource')}
              </ToggleSeg>
            </ToggleGroup>
            {sourceMode ? (
              <SearchSelect
                value={b.source ?? ''}
                options={sourceOptions}
                onChange={(v) => updateBind(i, { source: v ?? '' })}
                allowCustom
                placeholder={t('settings.screens.paramBinds.sourcePlaceholder')}
              />
            ) : (
              <Input
                value={b.value ?? ''}
                onChange={(e) => updateBind(i, { value: e.target.value })}
                placeholder={t('settings.screens.paramBinds.valuePlaceholder')}
              />
            )}
            <RemoveBtn type="button" onClick={() => removeBind(i)} title={t('common.remove')}>
              <Trash2 size={13} />
            </RemoveBtn>
          </Row>
        )
      })}
      <Button
        $variant="ghost"
        $size="sm"
        onClick={addBind}
        style={{ justifyContent: 'flex-start', alignSelf: 'flex-start' }}
      >
        <Plus size={13} /> {t('settings.screens.paramBinds.add')}
      </Button>
    </Stack>
  )
}
