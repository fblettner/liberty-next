// A searchable single-select dropdown — a themed replacement for a native <select> when the option
// list is long (e.g. a dictionary LOOKUP that resolves to hundreds of rows). Click the trigger to
// open; type to filter (matches the option *label* and its *value*, so a code lookup works); click
// an item — or the optional "(any)" row — to pick. Closes on outside-click / Escape. Not virtualized
// (fine up to a few thousand rows); reuses the same visual language as the grid's filter pop-overs.
import { useEffect, useMemo, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { ChevronDown, Search, Check } from 'lucide-react'
import { colors, radius, fontSize, fonts, shadow } from '../theme'

export interface SearchSelectOption { value: string; label: string }

const Wrap = styled.div`position: relative; flex: 1; min-width: 0;`
const Trigger = styled.button<{ $open: boolean; $placeholder: boolean }>`
  display: flex; align-items: center; gap: 6px; width: 100%; height: 32px; padding: 0 8px 0 10px;
  border: 1px solid ${({ $open }) => ($open ? colors.blue.border : colors.border)};
  border-radius: ${radius.md}; background: ${colors.bg.input}; cursor: pointer;
  color: ${({ $placeholder }) => ($placeholder ? colors.text.muted : colors.text.primary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans}; text-align: left;
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
  display: flex; align-items: center; gap: 6px; padding: 7px 9px; border-bottom: 1px solid ${colors.border};
  color: ${colors.text.muted};
  & input {
    flex: 1; min-width: 0; border: none; background: transparent; outline: none;
    color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
    &::placeholder { color: ${colors.text.muted}; }
  }
`
const List = styled.div`max-height: 264px; overflow-y: auto; padding: 4px;`
const Item = styled.button<{ $active?: boolean }>`
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 6px 8px;
  border: none; border-radius: ${radius.md}; cursor: pointer; text-align: left;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & .t { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & svg { flex-shrink: 0; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`
const Empty = styled.div`padding: 10px 8px; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};`

export function SearchSelect({
  value, onChange, options, placeholder, anyLabel, loading, disabled,
}: {
  value: string
  onChange: (v: string) => void
  options: SearchSelectOption[]
  placeholder?: string
  /** Label for the "clear to the empty value" row at the top of the list — omit it for no such row. */
  anyLabel?: string
  loading?: boolean
  disabled?: boolean
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
    return options.filter((o) => o.label.toLowerCase().includes(needle) || o.value.toLowerCase().includes(needle))
  }, [q, options])

  const pick = (v: string) => { onChange(v); setOpen(false) }

  return (
    <Wrap ref={wrapRef}>
      <Trigger type="button" $open={open} $placeholder={!current} disabled={disabled} onClick={() => !disabled && setOpen((o) => !o)}>
        <span className="lbl">{loading ? t('common.loading') : current ? current.label : (placeholder ?? '')}</span>
        <ChevronDown size={14} />
      </Trigger>
      {open && (
        <Panel>
          <SearchRow>
            <Search size={13} />
            <input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} placeholder={t('table.search')} />
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
                  <span className="t">{o.label}</span>
                  {o.value === value && <Check size={12} />}
                </Item>
              ))
            )}
          </List>
        </Panel>
      )}
    </Wrap>
  )
}
