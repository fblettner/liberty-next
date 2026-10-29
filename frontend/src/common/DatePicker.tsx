// One date, themed: a text field (an ISO date typed, checked when the field is left or on Enter)
// with a calendar button that opens the month calendar the period picker uses (MonthGrid) in a
// popover — Today / Clear in its footer. Replaces the browser's native date input (white, and
// different on every browser) in the record form, the grid's bulk edit and the explorer's filters.
// The value stays an ISO date (yyyy-mm-dd), '' when empty.
import { useEffect, useRef, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Calendar } from 'lucide-react'
import { colors, fontSize, fonts, radius, shadow } from '../theme'
import { PortalPanel } from './PortalPanel'
import { MonthGrid } from './MonthGrid'
import { fromIso, toIso } from '../services/period'

/** A real calendar day written yyyy-mm-dd (2026-02-30 is not). */
export function isIsoDate(v: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(v)) return false
  const d = fromIso(v)
  return !Number.isNaN(d.getTime()) && toIso(d) === v
}

const Wrap = styled.div`position: relative; width: 100%; min-width: 0;`
const Field = styled.input<{ $sm?: boolean }>`
  width: 100%; box-sizing: border-box; outline: none;
  height: ${({ $sm }) => ($sm ? 'auto' : '32px')};
  padding: ${({ $sm }) => ($sm ? '2px 26px 2px 6px' : '0 34px 0 10px')};
  border-radius: ${({ $sm }) => ($sm ? radius.sm : radius.md)};
  border: 1px solid ${colors.border}; background: ${colors.bg.input}; color: ${colors.text.primary};
  font-size: ${({ $sm }) => ($sm ? fontSize.sm : fontSize.base)};
  font-family: ${({ $sm }) => ($sm ? fonts.mono : fonts.sans)};
  font-variant-numeric: tabular-nums;
  transition: border-color 0.15s, box-shadow 0.15s;
  &:focus { border-color: ${colors.blue.main}; box-shadow: ${shadow.focus}; }
  &::placeholder { color: ${colors.text.muted}; }
  &:disabled, &:read-only { background: var(--bg-readonly); color: ${colors.text.muted}; }
`
const CalBtn = styled.button<{ $sm?: boolean }>`
  position: absolute; top: 50%; transform: translateY(-50%); right: ${({ $sm }) => ($sm ? '2px' : '4px')};
  width: ${({ $sm }) => ($sm ? '22px' : '26px')}; height: ${({ $sm }) => ($sm ? '20px' : '26px')};
  display: inline-flex; align-items: center; justify-content: center;
  border: none; border-radius: ${radius.sm}; background: transparent; color: ${colors.text.muted}; cursor: pointer;
  &:hover:not(:disabled) { background: var(--hover-subtle); color: ${colors.text.primary}; }
  &:disabled { cursor: default; opacity: 0.5; }
`
const Panel = styled.div`padding: 12px 14px 8px; display: flex; flex-direction: column; gap: 6px;`
const Footer = styled.div`display: flex; justify-content: space-between; border-top: 1px solid ${colors.border}; padding-top: 6px;`
const Link = styled.button`
  height: 28px; padding: 0 8px; border: none; border-radius: ${radius.md}; background: transparent; cursor: pointer;
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; color: ${colors.blue.main};
  &:hover { background: var(--hover-subtle); }
`

export function DatePicker({ value, onChange, disabled, readOnly, required, size, ariaLabel }: {
  /** yyyy-mm-dd, '' when empty. */
  value: string
  /** The new date (yyyy-mm-dd), '' when cleared. */
  onChange: (iso: string) => void
  disabled?: boolean
  readOnly?: boolean
  required?: boolean
  /** ``sm``: the compact grid-cell editor. */
  size?: 'sm'
  ariaLabel?: string
}) {
  const { t, i18n } = useTranslation()
  const [draft, setDraft] = useState(value)
  const [open, setOpen] = useState(false)
  const [view, setView] = useState(() => { const d = isIsoDate(value) ? fromIso(value) : new Date(); return { y: d.getFullYear(), m: d.getMonth() } })
  const wrapRef = useRef<HTMLDivElement>(null)
  useEffect(() => { setDraft(value) }, [value])
  const sm = size === 'sm'
  const locked = !!disabled || !!readOnly

  // A typed date is taken when it is a real day; '' clears; anything else goes back to the value.
  const commit = () => {
    const v = draft.trim()
    if (v === value) return
    if (v === '') onChange('')
    else if (isIsoDate(v)) onChange(v)
    else setDraft(value)
  }
  const pick = (iso: string) => { setOpen(false); setDraft(iso); onChange(iso) }
  const toggle = () => {
    if (locked) return
    if (!open) { const d = isIsoDate(value) ? fromIso(value) : new Date(); setView({ y: d.getFullYear(), m: d.getMonth() }) }
    setOpen((o) => !o)
  }
  const shift = (n: number) => setView((v) => { const d = new Date(v.y, v.m + n, 1); return { y: d.getFullYear(), m: d.getMonth() } })

  return (
    <Wrap ref={wrapRef}>
      <Field
        $sm={sm} value={draft} placeholder={t('common.datePlaceholder')} inputMode="numeric" aria-label={ariaLabel}
        disabled={disabled} readOnly={readOnly} required={required}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); commit() }
          if (e.key === 'ArrowDown' && e.altKey) { e.preventDefault(); toggle() }
        }}
      />
      <CalBtn type="button" $sm={sm} disabled={locked} onClick={toggle} title={t('common.pickDate')} aria-label={t('common.pickDate')}
        aria-haspopup="dialog" aria-expanded={open}>
        <Calendar size={sm ? 12 : 14} />
      </CalBtn>
      {open && (
        <PortalPanel triggerRef={wrapRef} maxWidth={290} minWidth={270} roomBelow={340} onDismiss={() => setOpen(false)}>
          <Panel role="dialog" aria-label={t('common.pickDate')}>
            <MonthGrid
              year={view.y} month={view.m} from={isIsoDate(value) ? value : ''} to={isIsoDate(value) ? value : ''}
              today={toIso(new Date())} lang={i18n.language} onPick={pick}
              nav={{ prev: () => shift(-1), next: () => shift(1) }}
              onJump={(y, m) => setView({ y, m })}
            />
            <Footer>
              <Link type="button" onClick={() => { setOpen(false); setDraft(''); onChange('') }}>{t('common.clear')}</Link>
              <Link type="button" onClick={() => pick(toIso(new Date()))}>{t('period.today')}</Link>
            </Footer>
          </Panel>
        </PortalPanel>
      )}
    </Wrap>
  )
}
