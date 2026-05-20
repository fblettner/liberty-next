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
const Toggle = styled.button<{ $mode: 'value' | 'source' }>`
  height: 30px; padding: 0 8px; border: 1px solid ${colors.border}; background: ${colors.bg.input};
  color: ${({ $mode }) => ($mode === 'source' ? colors.blue.main : colors.text.muted)};
  border-radius: ${radius.sm}; cursor: pointer; font-family: ${fonts.mono}; font-size: ${fontSize.micro};
  font-weight: 600; min-width: 38px;
  &:hover { border-color: ${colors.blue.border}; color: ${colors.blue.main}; }
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
  /** Source-field autocomplete candidates. Pass ``[]`` to disable the dropdown (the operator
   *  can still type a path — ``allowCustom`` stays on). */
  sourceOptions: SearchSelectOption[]
  /** Placeholder for the param-name input — defaults to ``param``. Lets a caller specialise
   *  ("loop binding name" inside a LoopAction, etc.) without re-i18n-ing the same key. */
  paramPlaceholder?: string
}

export default function ParamBindList({
  value, onChange, sourceOptions, paramPlaceholder,
}: ParamBindListProps) {
  const { t } = useTranslation()
  const binds = value ?? []

  // Mode detection: source mode iff ``source`` is set OR ``value`` is null/empty. A bind with
  // both set (shouldn't happen in well-formed TOML but the runtime tolerates it) reads as
  // source mode — matches actionRunner's source-wins-when-both convention.
  const isSourceMode = (b: ParamBind): boolean => {
    if (b.source != null && b.source !== '') return true
    if (b.value != null && b.value !== '') return false
    return true                                            // empty bind → default to source
  }

  const updateBind = (idx: number, patch: Partial<ParamBind>) => {
    const next = binds.slice()
    next[idx] = { ...next[idx], ...patch }
    // Empty string → drop the key so the saved TOML stays terse. Don't drop ``param`` (the
    // operator typed it; even when empty it's meaningful — they're editing).
    for (const k of Object.keys(patch) as (keyof ParamBind)[]) {
      if (k === 'param') continue
      const v = (next[idx] as ParamBind)[k]
      if (v === '' || v == null) delete (next[idx] as ParamBind)[k]
    }
    onChange(next)
  }
  const flipMode = (idx: number) => {
    const b = binds[idx]
    if (isSourceMode(b)) {
      // source → value: clear source, seed empty value.
      updateBind(idx, { source: undefined, value: '' })
    } else {
      // value → source: clear value, seed empty source.
      updateBind(idx, { value: undefined, source: '' })
    }
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
            <Input
              value={b.param ?? ''}
              onChange={(e) => updateBind(i, { param: e.target.value })}
              placeholder={paramPlaceholder ?? t('settings.screens.paramBinds.paramPlaceholder')}
            />
            {/* Two-state toggle between literal value and chain-context source. The button text
                shows the *current* mode (small + monospaced) so the operator at-a-glance reads
                "val" or "src"; clicking flips to the other. */}
            <Toggle
              type="button"
              $mode={sourceMode ? 'source' : 'value'}
              onClick={() => flipMode(i)}
              title={t('settings.screens.paramBinds.flipMode')}
            >
              {sourceMode ? t('settings.screens.paramBinds.modeSource') : t('settings.screens.paramBinds.modeValue')}
            </Toggle>
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
