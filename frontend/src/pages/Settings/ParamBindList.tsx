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
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { ChevronDown, Plus, Trash2 } from 'lucide-react'
import {
  Button, Input, SearchSelect, Stack, type SearchSelectOption,
} from '../../common'
import { colors, fontSize, fonts, radius, shadow } from '../../theme'

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
  /** Fallback bound when *source mode* resolves to NULL / empty at call time. v2's port of
   *  v1's ``ly_act_tasks_params.map_default`` — useful for an optional input where a blank
   *  source should default to a sane value (e.g. ``#SYSDATE#`` or a fixed string). Ignored
   *  in value mode (the literal already wins). */
  default?: string | null
}

// Per-bind wrapper — wraps the primary [param][toggle][value/source][×] row PLUS an optional
// secondary row carrying the source-mode-only ``default`` input. Keeping both rows inside one
// flex container makes the visual grouping obvious (the default belongs to the bind above it).
const BindRow = styled.div`display: flex; flex-direction: column; gap: 4px;`
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
// Secondary row — shown only in source mode AND only when the operator has *opted in*
// (default already set on the bind, or just clicked the "+ default" affordance below the row).
// Without the opt-in every source-mode bind would render an empty fallback input, which is
// a lot of vertical noise for a feature most binds don't use. ``↳`` glyph signals it's a
// child of the bind above; the trailing × clears the default outright (collapsing the row).
const DefaultRow = styled.div`
  display: grid; grid-template-columns: auto 1fr auto; align-items: center; gap: 6px;
  padding-left: 6px;
  & .arrow { color: ${colors.text.muted}; font-family: ${fonts.mono}; font-size: ${fontSize.micro}; }
`
// Tiny "+ default" link below the bind, shown only when source-mode AND no default is set
// yet. Clicking adds an empty ``default`` field on the bind, which un-collapses the row.
const AddDefaultBtn = styled.button`
  align-self: flex-start;
  background: transparent; border: none; padding: 2px 6px 2px 12px; cursor: pointer;
  color: ${colors.text.muted}; font-size: ${fontSize.micro};
  &:hover { color: ${colors.blue.main}; }
`
const ClearDefaultBtn = styled.button`
  height: 24px; width: 24px; padding: 0; display: inline-flex; align-items: center; justify-content: center;
  border: 1px solid transparent; background: transparent; color: ${colors.text.muted};
  border-radius: ${radius.sm}; cursor: pointer;
  &:hover { color: ${colors.red.main}; border-color: ${colors.red.border}; background: ${colors.red.bg}; }
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

// ── SourcePathInput ─────────────────────────────────────────────────────────────────────────
//
// Plain text input with an inline ▾ button that opens a suggestions overlay. Picking a
// suggestion *inserts* it at the cursor (or replaces the current selection) rather than
// overwriting the whole field — which is what lets the operator pick the
// "<step>.first_row" prefix from the autocomplete and then keep typing ".userInfo.token".
// Replaces the previous SearchSelect-based UX where selecting a candidate wiped any path
// suffix the operator had typed, and there was no way to *extend* a suggestion.
//
// The picker is opt-in (the operator clicks ▾) so the input stays fully editable at all
// times — typing, pasting, selecting, deleting all work the way operators expect from a
// plain text field. The textbox itself never opens the popover on focus (would steal focus
// every time the operator clicked the field to edit). Closes on outside click + Escape.
const PathWrap = styled.div`
  position: relative; flex: 1; min-width: 0;
  display: grid; grid-template-columns: 1fr auto; align-items: stretch;
  border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.input};
  & input {
    border: none; background: transparent; outline: none; padding: 0 8px;
    font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary};
    height: 30px;
    &::placeholder { color: ${colors.text.muted}; font-family: ${fonts.sans}; }
  }
  &:focus-within { border-color: ${colors.blue.border}; }
`
const PickerBtn = styled.button<{ $open: boolean }>`
  display: inline-flex; align-items: center; justify-content: center;
  width: 28px; padding: 0;
  border: none; border-left: 1px solid ${colors.border};
  background: ${({ $open }) => ($open ? colors.blue.bg : 'transparent')};
  color: ${({ $open }) => ($open ? colors.blue.main : colors.text.muted)};
  cursor: pointer;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const PathPanel = styled.div`
  position: fixed; z-index: 1000;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  box-shadow: ${shadow.lg}; overflow: hidden; display: flex; flex-direction: column;
  max-height: min(360px, 60vh); min-width: 260px;
`
// Filter box pinned at the top of the panel — type to narrow a long candidate list (JDE tables
// carry 30+ columns). Sits outside the scroll area so it stays visible while the list scrolls.
const PathSearch = styled.div`
  flex: 0 0 auto; padding: 8px 10px; border-bottom: 1px solid ${colors.border};
  & input {
    display: block; width: 100%; box-sizing: border-box; border: none; background: transparent; outline: none;
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
    &::placeholder { color: ${colors.text.muted}; }
  }
`
// Scrollable list region — without this the panel's ``overflow: hidden`` clipped any list taller
// than its max-height with no way to reach the rest. ``min-height: 0`` unlocks flex shrink.
const PathList = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto;`
const PathGroup = styled.div`
  padding: 4px 0;
  & + & { border-top: 1px solid ${colors.border}; }
`
const PathGroupLabel = styled.div`
  padding: 4px 12px 2px;
  font-size: ${fontSize.micro}; text-transform: uppercase; letter-spacing: 0.06em;
  color: ${colors.text.muted};
`
const PathItem = styled.button`
  display: flex; flex-direction: column; align-items: flex-start; gap: 2px;
  width: 100%; padding: 6px 12px;
  border: none; background: transparent; cursor: pointer; text-align: left;
  & .mono { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; }
  & .hint { font-size: ${fontSize.micro}; color: ${colors.text.muted}; font-family: ${fonts.sans}; }
  &:hover { background: var(--hover-subtle); }
`
const PathEmpty = styled.div`
  padding: 10px 12px; color: ${colors.text.muted};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
`

function SourcePathInput({
  value, onChange, options, placeholder,
}: {
  value: string
  onChange: (v: string) => void
  options: SearchSelectOption[]
  placeholder?: string
}) {
  const { t } = useTranslation()
  const wrapRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  // Filter text for the suggestion popover (separate from the field ``value`` — typing here
  // narrows the list without touching what gets bound). Reset every time the popover opens.
  const [query, setQuery] = useState('')
  // Separate ref for the portaled popover — outside-click check must accept clicks inside
  // it too (the portal renders into ``document.body``, so it isn't a descendant of wrapRef).
  const panelRef = useRef<HTMLDivElement>(null)
  // Remember the cursor position the last time the input had focus — we'll insert the picked
  // path there. Otherwise picking from the ▾ menu (which steals focus) would always insert at
  // the end, which surprises operators who clicked into the middle of the field first.
  const cursorRef = useRef<{ start: number; end: number }>({ start: value.length, end: value.length })
  const [open, setOpen] = useState(false)
  const [panelPos, setPanelPos] = useState<{ left: number; top: number; width: number } | null>(null)

  // Position the popover under the input. Read on open + window resize so a scrolled
  // viewport places it correctly. Same fixed-positioning approach SearchSelect uses.
  useLayoutEffect(() => {
    if (!open || !wrapRef.current) return
    const updatePosition = () => {
      const rect = wrapRef.current!.getBoundingClientRect()
      setPanelPos({ left: rect.left, top: rect.bottom + 4, width: Math.max(rect.width, 260) })
    }
    updatePosition()
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open])

  // Clear the filter + focus the search box each time the popover opens, so the operator can
  // start typing immediately to narrow a long list.
  useEffect(() => {
    if (!open) { setQuery(''); return }
    const id = requestAnimationFrame(() => searchRef.current?.focus())
    return () => cancelAnimationFrame(id)
  }, [open])

  // Outside-click + Escape — same lifecycle SearchSelect uses, kept inline so this
  // component stays self-contained (one consumer for now).
  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node
      if (wrapRef.current?.contains(target)) return
      if (panelRef.current?.contains(target)) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const captureCursor = () => {
    const el = inputRef.current
    if (!el) return
    cursorRef.current = {
      start: el.selectionStart ?? value.length,
      end: el.selectionEnd ?? value.length,
    }
  }

  // Insert the picked path at the captured cursor position. Replaces any active selection.
  // Auto-prefixes a `.` when inserting after a non-empty, non-trailing-dot prefix — so
  // appending `userInfo.token` after `get_token_for_ais.first_row` produces
  // `get_token_for_ais.first_row.userInfo.token` instead of asking the operator to type
  // the joining dot. Leaves the value alone when it's empty or already ends with a dot.
  const insertAtCursor = (picked: string) => {
    const { start, end } = cursorRef.current
    const before = value.slice(0, start)
    const after = value.slice(end)
    let toInsert = picked
    // Smart-dot: only when we're inserting at the end of a non-empty, non-dot-terminated
    // prefix AND the picked path doesn't already start with one. Picking a top-level
    // candidate into an empty field stays clean.
    if (before && !before.endsWith('.') && !picked.startsWith('.')) {
      toInsert = '.' + picked
    }
    const next = before + toInsert + after
    onChange(next)
    // After React commits, restore focus + put the cursor right after the inserted text.
    queueMicrotask(() => {
      const el = inputRef.current
      if (!el) return
      el.focus()
      const newCursor = start + toInsert.length
      el.setSelectionRange(newCursor, newCursor)
      cursorRef.current = { start: newCursor, end: newCursor }
    })
    setOpen(false)
  }

  // Narrow by the filter text (case-insensitive, matches the path OR its label), then group by
  // the `group` prop when present — matches the chainContext candidate shape (inputs /
  // step_results / loop). Falls back to a single ungrouped list for callers that pass flat options.
  const q = query.trim().toLowerCase()
  const visible = q
    ? options.filter((o) => o.value.toLowerCase().includes(q) || (o.label ?? '').toLowerCase().includes(q))
    : options
  const grouped: Map<string | undefined, SearchSelectOption[]> = new Map()
  for (const o of visible) {
    const k = o.group
    const arr = grouped.get(k) ?? []
    arr.push(o)
    grouped.set(k, arr)
  }

  return (
    <PathWrap ref={wrapRef}>
      <input
        ref={inputRef}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onSelect={captureCursor}
        onClick={captureCursor}
        onKeyUp={captureCursor}
        onBlur={captureCursor}
      />
      <PickerBtn
        type="button"
        $open={open}
        onClick={(e) => { e.preventDefault(); captureCursor(); setOpen((o) => !o) }}
        title={t('settings.screens.paramBinds.pickerHint', 'Insert a suggestion at the cursor')}
        aria-label={t('settings.screens.paramBinds.pickerHint', 'Insert a suggestion at the cursor') as string}
      >
        <ChevronDown size={14} />
      </PickerBtn>
      {open && panelPos && createPortal(
        <PathPanel
          ref={panelRef}
          style={{ left: panelPos.left, top: panelPos.top, width: panelPos.width }}
        >
          {options.length > 0 && (
            <PathSearch>
              <input
                ref={searchRef}
                value={query}
                placeholder={t('settings.screens.paramBinds.pickerFilter', 'Filter suggestions…')}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => {
                  // Enter picks the first match — fast path for a filtered list of one.
                  if (e.key === 'Enter' && visible.length > 0) { e.preventDefault(); insertAtCursor(visible[0].value) }
                }}
              />
            </PathSearch>
          )}
          <PathList onMouseDown={(e) => e.preventDefault() /* keep focus while clicking an item */}>
            {options.length === 0 ? (
              <PathEmpty>{t('settings.screens.paramBinds.pickerEmpty', 'No suggestions — type the path directly.')}</PathEmpty>
            ) : visible.length === 0 ? (
              <PathEmpty>{t('settings.screens.paramBinds.pickerNoMatch', 'No match — type the path directly.')}</PathEmpty>
            ) : (
              Array.from(grouped.entries()).map(([groupKey, items]) => (
                <PathGroup key={groupKey ?? '_default'}>
                  {groupKey && <PathGroupLabel>{groupKey.replace(/_/g, ' ')}</PathGroupLabel>}
                  {items.map((opt) => (
                    <PathItem
                      key={opt.value}
                      type="button"
                      onClick={() => insertAtCursor(opt.value)}
                    >
                      <span className="mono">{opt.value}</span>
                      {opt.label && opt.label !== opt.value && <span className="hint">{opt.label}</span>}
                    </PathItem>
                  ))}
                </PathGroup>
              ))
            )}
          </PathList>
        </PathPanel>,
        document.body,
      )}
    </PathWrap>
  )
}

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
          <BindRow key={i}>
            <Row>
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
                <SourcePathInput
                  value={b.source ?? ''}
                  options={sourceOptions}
                  onChange={(v) => updateBind(i, { source: v })}
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
            {/* Source-mode-only fallback (v1's ``map_default``). Collapsed by default to keep
                the row compact — only shown when ``b.default`` is set (carried over from
                migration, or just opted-in via the "+ default" link). Accepts a literal
                (``"0"``), a built-in (``"#SYSDATE#"`` / ``"#LOGIN_USER#"``), or a chain-
                context path (``"INPUT.<X>"`` / ``"<step>.first_row.<col>"``); the runtime
                dispatches by shape. Clearing the input via × removes the key entirely so the
                row collapses back. */}
            {sourceMode && typeof b.default === 'string' && (
              <DefaultRow>
                <span className="arrow" title={t('settings.screens.paramBinds.defaultTip')}>↳</span>
                <Input
                  value={b.default}
                  onChange={(e) => updateBind(i, { default: e.target.value })}
                  placeholder={t('settings.screens.paramBinds.defaultPlaceholder')}
                  title={t('settings.screens.paramBinds.defaultTip')}
                  autoFocus={b.default === ''}
                />
                <ClearDefaultBtn
                  type="button"
                  onClick={() => updateBind(i, { default: null })}
                  title={t('settings.screens.paramBinds.defaultClear')}
                >
                  <Trash2 size={11} />
                </ClearDefaultBtn>
              </DefaultRow>
            )}
            {sourceMode && typeof b.default !== 'string' && (
              <AddDefaultBtn
                type="button"
                onClick={() => updateBind(i, { default: '' })}
                title={t('settings.screens.paramBinds.defaultTip')}
              >
                + {t('settings.screens.paramBinds.addDefault')}
              </AddDefaultBtn>
            )}
          </BindRow>
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
