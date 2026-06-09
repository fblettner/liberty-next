// A searchable single-select dropdown — a themed replacement for a native <select> when the option
// list is long (e.g. a dictionary LOOKUP that resolves to hundreds of rows, or one of the framework
// enums driving a builder field). Click the trigger to open; type to filter (matches the option
// *label*, the *value*, and the optional secondary *mono* column); click an item — or the optional
// "(any)" row — to pick. Closes on outside-click / Escape. Not virtualized (fine up to a few
// thousand rows); reuses the same visual language as the grid's filter pop-overs.
//
// Two extra modes the config builder needs:
//  · `mono` on an option → a secondary mono-font column (the code) shown beside the label/description
//    — so a "DICTIONARY_TYPE" pick reads `number  Number`, not just `Number`. The trigger shows the
//    mono code with the label muted next to it. Options without `mono` render single-column.
//  · `allowCustom` → in the search box, pressing Enter (with no matching option) commits the typed
//    text as the value. Free-text combobox semantics — for fields where v1 emitted aliases we don't
//    want to reject (`format`, `dialect`, …).
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Check } from 'lucide-react'
import { colors, radius, fontSize, fonts, shadow } from '../theme'

export interface SearchSelectOption {
  value: string
  label: string
  /** Optional secondary text rendered in a mono-font column beside `label` (typically the same as
   *  `value` for framework-enum dropdowns). When absent the option renders as a single column. */
  mono?: string
  /** Optional grouping key — consumed by the grouped suggestion overlay in ParamBindList's
   *  ``SourcePathInput`` (chain inputs / step results / loop). SearchSelect itself ignores it. */
  group?: string
}

// Everything inside the panel shares the same 12px left inset so the trigger label, the search
// input and the list items line up vertically.
const PAD_X = '12px'

const Wrap = styled.div`position: relative; flex: 1; min-width: 0;`
const Trigger = styled.button<{ $open: boolean; $placeholder: boolean }>`
  display: flex; align-items: center; gap: 6px; width: 100%; height: 32px; padding: 0 8px 0 ${PAD_X};
  border: 1px solid ${({ $open }) => ($open ? colors.blue.border : colors.border)};
  border-radius: ${radius.md}; background: ${colors.bg.input}; cursor: pointer;
  color: ${({ $placeholder }) => ($placeholder ? colors.text.muted : colors.text.primary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; text-align: left;
  & .mono { font-family: ${fonts.mono}; }
  & .muted { color: ${colors.text.muted}; }
  & .lbl { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  &:hover:not(:disabled) { border-color: ${colors.blue.border}; }
  &:disabled { opacity: 0.5; cursor: default; }
`
// `position: fixed` + portaled to `document.body` so the dropdown escapes any `overflow: hidden`
// ancestor — most notably the Modal frame, which would otherwise clip the panel when the user
// opened a dropdown inside a nested ScreenDialog (the embedded "Activity Log" / "Audit Trail"
// row-edit case). Coords are computed from the trigger's getBoundingClientRect on open + on
// scroll / resize. z-index sits above every Modal Overlay (top-level: 400, nested: 500).
const Panel = styled.div`
  position: fixed; z-index: 1000;
  background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  box-shadow: ${shadow.lg}; overflow: hidden; display: flex; flex-direction: column;
`
const SearchRow = styled.div`
  padding: 8px ${PAD_X}; border-bottom: 1px solid ${colors.border};
  & input {
    display: block; width: 100%; box-sizing: border-box; border: none; background: transparent; outline: none;
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
    &::placeholder { color: ${colors.text.muted}; }
  }
`
// The List grows up to the Panel's available space (the Panel caps its own max-height
// against the viewport). ``min-height: 0`` is the flex-shrink unlock — without it the
// List would keep its content's full height and overflow the panel.
const List = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 4px 0;`
const Item = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 6px ${PAD_X};
  border: none; cursor: pointer; text-align: left;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & .mono { font-family: ${fonts.mono}; flex: 0 0 35%; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: ${colors.text.primary}; }
  & .t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & svg { flex-shrink: 0; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const Empty = styled.div`padding: 10px ${PAD_X}; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`
const CreateRow = styled.button`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 8px ${PAD_X}; border: none;
  border-top: 1px solid ${colors.border}; background: transparent; cursor: pointer; text-align: left;
  color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & .mono { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`

export function SearchSelect({
  value, onChange, options, placeholder, anyLabel, loading, disabled, allowCustom,
}: {
  value: string
  onChange: (v: string) => void
  options: SearchSelectOption[]
  placeholder?: string
  /** Label for the "clear to the empty value" row at the top of the list — omit it for no such row. */
  anyLabel?: string
  loading?: boolean
  disabled?: boolean
  /** Combobox mode: pressing Enter in the search box commits the typed text as the value, even when
   *  it doesn't match any option. For fields with `examples`-suggested values (free-text). */
  allowCustom?: boolean
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  // The trigger's viewport coords — drive the portaled Panel's `position: fixed` offsets.
  // Recomputed on open + on scroll/resize so the dropdown follows the trigger as the user
  // scrolls inside a modal body. `null` = panel not positioned yet (first render frame).
  //
  // ``placement`` records whether the panel opens BELOW (default) or ABOVE the trigger
  // — when below would overflow the viewport, flip up. The panel's max-height is also
  // capped to the available space so the list never overflows the viewport edge (the
  // user's complaint: in a modal-edge SearchSelect the dropdown would render off-screen).
  const [panelPos, setPanelPos] = useState<
    { top: number; left: number; width: number; maxHeight: number; placement: 'below' | 'above' } | null
  >(null)

  useEffect(() => {
    if (!open) { setQ(''); setPanelPos(null); return }
    const compute = () => {
      const el = triggerRef.current
      if (!el) return
      const r = el.getBoundingClientRect()
      const vh = window.innerHeight
      const GAP = 4
      const MIN_PANEL = 160         // never flip up if at least this much fits below
      const MAX_PANEL = 320         // matches List's max-height 264 + header/footer rows
      const spaceBelow = vh - r.bottom - GAP
      const spaceAbove = r.top - GAP
      // Prefer below. Flip up only when below has *less* room AND above has more — keeps
      // the natural "open downward" feel for most cases, only flipping on a near-the-edge
      // trigger.
      const flipUp = spaceBelow < MIN_PANEL && spaceAbove > spaceBelow
      const maxHeight = Math.min(MAX_PANEL, Math.max(MIN_PANEL, flipUp ? spaceAbove : spaceBelow))
      const top = flipUp ? Math.max(GAP, r.top - maxHeight - GAP) : r.bottom + GAP
      setPanelPos({ top, left: r.left, width: r.width, maxHeight, placement: flipUp ? 'above' : 'below' })
    }
    compute()
    // Click outside (either the trigger or the portaled panel itself) closes the dropdown.
    const onDoc = (e: MouseEvent) => {
      const inWrap = wrapRef.current?.contains(e.target as Node)
      const inPanel = panelRef.current?.contains(e.target as Node)
      if (!inWrap && !inPanel) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    // `capture: true` so we catch scroll on every ancestor (ModalBody, page) — bubbling scroll
    // events skip non-Window scrollers without it.
    window.addEventListener('resize', compute)
    window.addEventListener('scroll', compute, true)
    queueMicrotask(() => searchRef.current?.focus())
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', compute)
      window.removeEventListener('scroll', compute, true)
    }
  }, [open])

  // Exact match drives the trigger label; fall back to a trim-tolerant match so a JDE-padded
  // lookup value (option ``"      01"``) still shows its label when the stored value reads back
  // as ``"01"`` (or vice versa) — common after a UDC lookup moved to read JDE directly. Display
  // only: the stored ``value`` passed to ``onChange`` is never rewritten, so save keeps its form.
  const current = options.find((o) => o.value === value)
    ?? (value ? options.find((o) => o.value.trim() === String(value).trim()) : undefined)
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase()
    if (!needle) return options
    return options.filter((o) =>
      o.label.toLowerCase().includes(needle)
      || o.value.toLowerCase().includes(needle)
      || (o.mono ?? '').toLowerCase().includes(needle),
    )
  }, [q, options])

  const pick = (v: string) => { onChange(v); setOpen(false) }
  const commitCustom = () => {
    const typed = q.trim()
    if (!typed) return
    const match = options.find((o) => o.value.toLowerCase() === typed.toLowerCase() || o.label.toLowerCase() === typed.toLowerCase())
    pick(match ? match.value : typed)
  }
  const exactMatch = options.some((o) => o.value === q.trim())
  const showCreateRow = allowCustom && q.trim() !== '' && !exactMatch

  // Render the trigger label. With `mono` (framework-enum dropdowns) we show "code  Description" so
  // the user sees both the stored value and its human meaning. Without it the regular label suffices.
  // A custom value (not in `options`) still surfaces in the trigger so the user knows what's stored.
  const triggerLabel = (() => {
    if (loading) return <span className="lbl">{t('common.loading')}</span>
    if (current) {
      return current.mono && current.mono !== current.label
        ? <span className="lbl"><span className="mono">{current.mono}</span> <span className="muted">{current.label}</span></span>
        : <span className="lbl">{current.label}</span>
    }
    if (value && allowCustom) return <span className="lbl"><span className="mono">{value}</span></span>
    return <span className="lbl">{placeholder ?? ''}</span>
  })()

  // Build the portaled Panel only when open *and* its position has been computed (one frame
  // after open). Rendering with `null` coords would flash the dropdown at top-left of the
  // viewport for a frame — `position: fixed; visibility: hidden` until ready is also fine but
  // a single useEffect compute is simpler here.
  const panel = open && panelPos ? (
    <Panel
      ref={panelRef}
      style={{
        top: panelPos.top,
        left: panelPos.left,
        minWidth: Math.max(240, panelPos.width),
        maxHeight: panelPos.maxHeight,
      }}
    >
      <SearchRow>
        <input
          ref={searchRef}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder={allowCustom ? t('common.searchOrType', 'Search or type a value…') : t('table.search')}
          onKeyDown={(e) => { if (e.key === 'Enter' && allowCustom) { e.preventDefault(); commitCustom() } }}
        />
      </SearchRow>
      <List>
        {anyLabel !== undefined && (
          <Item type="button" $active={value === ''} onClick={() => pick('')}>
            <span className="t">{anyLabel}</span>
            {value === '' && <Check size={12} />}
          </Item>
        )}
        {filtered.length === 0 ? (
          <Empty>{t('table.noMatches', 'No matches')}</Empty>
        ) : (
          filtered.map((o) => (
            <Item key={o.value} type="button" $active={o.value === value} onClick={() => pick(o.value)}>
              {o.mono && o.mono !== o.label && <span className="mono">{o.mono}</span>}
              <span className="t">{o.label}</span>
              {o.value === value && <Check size={12} />}
            </Item>
          ))
        )}
      </List>
      {showCreateRow && (
        <CreateRow type="button" onClick={commitCustom}>
          {t('common.useCustom', 'Use')} <span className="mono">{q.trim()}</span>
        </CreateRow>
      )}
    </Panel>
  ) : null

  return (
    <Wrap ref={wrapRef}>
      <Trigger ref={triggerRef} type="button" $open={open} $placeholder={!current && !(allowCustom && !!value)} disabled={disabled} onClick={() => !disabled && setOpen((o) => !o)}>
        {triggerLabel}
        <ChevronDown size={14} />
      </Trigger>
      {panel && createPortal(panel, document.body)}
    </Wrap>
  )
}
