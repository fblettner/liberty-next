// Several values out of a searchable list — the multi-value twin of SearchSelect (same trigger and
// panel look, portaled, flips above near the viewport edge). The trigger shows the picks as chips
// (+N past a few); a pick toggles and keeps the panel open; "Select shown" adds every filtered
// option, Clear empties the selection; ``group`` headers and ``sort`` as in SearchSelect; Escape
// closes. Not virtualized: for lists of a few hundred.
import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Check, ChevronDown, X } from 'lucide-react'
import type { SearchSelectOption } from './SearchSelect'
import { orderOptions } from '../services/options'
import { colors, fontSize, fonts, radius, shadow } from '../theme'

const PAD_X = '12px'
const Wrap = styled.div`position: relative; flex: 1; min-width: 0;`
const Trigger = styled.button<{ $open: boolean; $sm?: boolean }>`
  display: flex; align-items: center; gap: 4px; width: 100%; min-height: ${({ $sm }) => ($sm ? '28px' : '32px')}; padding: 2px 8px 2px 6px;
  border: 1px solid ${({ $open }) => ($open ? colors.blue.border : colors.border)}; border-radius: ${radius.md};
  background: ${colors.bg.input}; cursor: pointer; text-align: left; flex-wrap: wrap;
  color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & .ph { color: ${colors.text.muted}; padding-left: 6px; flex: 1; }
  & .grow { flex: 1; }
  & svg { flex-shrink: 0; color: ${colors.text.muted}; }
  &:hover:not(:disabled) { border-color: ${colors.blue.border}; }
  &:focus-visible { outline: none; border-color: ${colors.blue.main}; }
  &:disabled { opacity: 0.5; cursor: default; }
`
const Chip = styled.span`
  display: inline-flex; align-items: center; gap: 3px; max-width: 160px; padding: 1px 4px 1px 8px; border-radius: 999px;
  background: ${colors.blue.bg}; border: 1px solid ${colors.blue.border}; font-size: ${fontSize.micro};
  & .t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .x { display: inline-flex; cursor: pointer; }
`
const Panel = styled.div`
  position: fixed; z-index: 1000; background: ${colors.bg.dropdown}; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  box-shadow: ${shadow.lg}; overflow: hidden; display: flex; flex-direction: column;
`
const SearchRow = styled.div`
  padding: 8px ${PAD_X}; border-bottom: 1px solid ${colors.border}; display: flex; gap: 8px; align-items: center;
  & input { flex: 1; min-width: 0; border: none; background: transparent; outline: none; color: ${colors.text.primary}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; }
  & button { border: none; background: none; cursor: pointer; color: ${colors.blue.main}; font-size: ${fontSize.micro}; font-family: ${fonts.sans}; white-space: nowrap; }
`
const List = styled.div`flex: 1 1 auto; min-height: 0; overflow-y: auto; padding: 4px 0;`
const GroupHead = styled.div`padding: 8px ${PAD_X} 4px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: ${colors.text.muted};`
const Item = styled.button<{ $on: boolean }>`
  display: flex; align-items: center; gap: 10px; width: 100%; padding: 6px ${PAD_X}; border: none; cursor: pointer; text-align: left;
  background: transparent;
  &:hover { background: var(--hover-subtle); }
  color: ${({ $on }) => ($on ? colors.text.primary : colors.text.secondary)}; font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  & .box { width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; display: inline-flex; align-items: center; justify-content: center;
    border: 1px solid ${({ $on }) => ($on ? colors.blue.main : colors.border)}; background: ${({ $on }) => ($on ? colors.blue.main : 'transparent')}; }
  & .box svg { color: #fff; }
  & .mono { font-family: ${fonts.mono}; color: ${colors.text.primary}; max-width: 55%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .t { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`
const Empty = styled.div`padding: 10px ${PAD_X}; color: ${colors.text.muted}; font-size: ${fontSize.sm};`

export function SearchSelectMulti({ value, onChange, options: given, placeholder, sort, size, disabled, maxChips = 3 }: {
  value: string[]
  onChange: (v: string[]) => void
  options: SearchSelectOption[]
  placeholder?: string
  sort?: boolean
  size?: 'sm' | 'md'
  disabled?: boolean
  /** Chips shown on the trigger before "+N". */
  maxChips?: number
}) {
  const { t } = useTranslation()
  const options = useMemo(() => orderOptions(given, !!sort), [given, sort])
  const [open, setOpen] = useState(false)
  const [q, setQ] = useState('')
  const [pos, setPos] = useState<{ top: number; left: number; width: number; maxHeight: number } | null>(null)
  const wrapRef = useRef<HTMLDivElement>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const picked = useMemo(() => new Set(value), [value])

  useEffect(() => {
    if (!open) { setQ(''); setPos(null); return }
    const compute = () => {
      const r = triggerRef.current?.getBoundingClientRect()
      if (!r) return
      const below = window.innerHeight - r.bottom - 4, above = r.top - 4
      const up = below < 160 && above > below
      const maxHeight = Math.min(340, Math.max(160, up ? above : below))
      setPos({ top: up ? Math.max(4, r.top - maxHeight - 4) : r.bottom + 4, left: r.left, width: r.width, maxHeight })
    }
    compute()
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node) && !panelRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    window.addEventListener('resize', compute)
    window.addEventListener('scroll', compute, true)
    queueMicrotask(() => searchRef.current?.focus())
    return () => {
      document.removeEventListener('mousedown', onDoc)
      window.removeEventListener('resize', compute)
      window.removeEventListener('scroll', compute, true)
    }
  }, [open])

  const filtered = useMemo(() => {
    const n = q.trim().toLowerCase()
    return n ? options.filter((o) => `${o.label} ${o.value} ${o.mono ?? ''}`.toLowerCase().includes(n)) : options
  }, [q, options])
  const toggle = (v: string) => onChange(picked.has(v) ? value.filter((x) => x !== v) : [...value, v])
  const onKey = (e: React.KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }

  const labelOf = (v: string) => options.find((o) => o.value === v)?.label ?? v
  let lastGroup: string | undefined
  const panel = open && pos ? (
    <Panel ref={panelRef} style={{ top: pos.top, left: pos.left, minWidth: Math.max(260, pos.width), maxHeight: pos.maxHeight }}>
      <SearchRow>
        <input ref={searchRef} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} placeholder={t('table.search')} />
        <button type="button" onClick={() => onChange([...value, ...filtered.map((o) => o.value).filter((v) => !picked.has(v))])}>{t('common.selectShown')}</button>
        {value.length > 0 && <button type="button" onClick={() => onChange([])}>{t('common.clear')}</button>}
      </SearchRow>
      <List>
        {filtered.map((o) => {
          const head = o.group && o.group !== lastGroup ? <GroupHead key={`h-${o.group}`}>{o.group}</GroupHead> : null
          lastGroup = o.group
          const on = picked.has(o.value)
          return [head, (
            <Item key={o.value} type="button" $on={on} onClick={() => toggle(o.value)}>
              <span className="box">{on && <Check size={10} />}</span>
              {o.mono && o.mono !== o.label && <span className="mono">{o.mono}</span>}
              <span className="t">{o.label}</span>
            </Item>
          )]
        })}
        {filtered.length === 0 && <Empty>{t('table.noMatches')}</Empty>}
      </List>
    </Panel>
  ) : null

  return (
    <Wrap ref={wrapRef}>
      <Trigger ref={triggerRef} type="button" $open={open} $sm={size === 'sm'} disabled={disabled}
        onClick={() => !disabled && setOpen((o) => !o)}>
        {value.length === 0 ? <span className="ph">{placeholder ?? ''}</span> : (
          <>
            {value.slice(0, maxChips).map((v) => (
              <Chip key={v}>
                <span className="t">{labelOf(v)}</span>
                {!disabled && <span className="x" role="button" tabIndex={0} aria-label={t('common.remove')}
                  onClick={(e) => { e.stopPropagation(); onChange(value.filter((x) => x !== v)) }}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); e.stopPropagation(); onChange(value.filter((x) => x !== v)) } }}><X size={11} /></span>}
              </Chip>
            ))}
            {value.length > maxChips && <Chip><span className="t">+{value.length - maxChips}</span></Chip>}
            <span className="grow" />
          </>
        )}
        <ChevronDown size={14} />
      </Trigger>
      {panel && createPortal(panel, document.body)}
    </Wrap>
  )
}
