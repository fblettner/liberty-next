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
const Panel = styled.div`
  position: absolute; top: calc(100% + 4px); left: 0; right: 0; z-index: 300; min-width: 240px;
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
const List = styled.div`max-height: 264px; overflow-y: auto; padding: 4px 0;`
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
  const searchRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!open) { setQ(''); return }
    const onDoc = (e: MouseEvent) => { if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    queueMicrotask(() => searchRef.current?.focus())
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [open])

  const current = options.find((o) => o.value === value)
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

  return (
    <Wrap ref={wrapRef}>
      <Trigger type="button" $open={open} $placeholder={!current && !(allowCustom && !!value)} disabled={disabled} onClick={() => !disabled && setOpen((o) => !o)}>
        {triggerLabel}
        <ChevronDown size={14} />
      </Trigger>
      {open && (
        <Panel>
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
      )}
    </Wrap>
  )
}
